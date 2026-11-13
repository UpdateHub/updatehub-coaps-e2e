// SPDX-FileCopyrightText: Copyright (c) 2026 O.S. Systems Software LTDA.
// SPDX-License-Identifier: Apache-2.0

// Command coaps-standin terminates DTLS 1.2 (pion/dtls) and proxies the
// UpdateHub CoAP API to an HTTP backend. It stands in for the cdn DTLS
// listener until that lands, using the same cipher suites and PKI.
package main

import (
	"bytes"
	"crypto/tls"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	piondtls "github.com/pion/dtls/v3"
	"github.com/plgd-dev/go-coap/v3/dtls"
	"github.com/plgd-dev/go-coap/v3/message"
	"github.com/plgd-dev/go-coap/v3/message/codes"
	"github.com/plgd-dev/go-coap/v3/mux"
	coapnet "github.com/plgd-dev/go-coap/v3/net"
	"github.com/plgd-dev/go-coap/v3/net/blockwise"
	"github.com/plgd-dev/go-coap/v3/options"
	"github.com/plgd-dev/go-coap/v3/udp/client"
)

// UpdateHub sends its API version header in this private CoAP option.
const optAPIHeader message.OptionID = 2048

var (
	backend = flag.String("backend", "http://127.0.0.1:8080", "HTTP backend base URL")
	maxSZX  = flag.Int("max-block-szx", 6, "largest Block2 SZX the server will send (6 = 1024 bytes)")
	httpc   = &http.Client{Timeout: 10 * time.Second}
)

func main() {
	listen := flag.String("listen", "127.0.0.1:5684", "UDP address for DTLS")
	certFile := flag.String("cert", "", "server certificate chain (PEM)")
	keyFile := flag.String("key", "", "server private key (PEM)")
	flag.Parse()

	cert, err := tls.LoadX509KeyPair(*certFile, *keyFile)
	if err != nil {
		log.Fatalf("load keypair: %v", err)
	}

	cfg := &piondtls.Config{
		Certificates: []tls.Certificate{cert},
		CipherSuites: []piondtls.CipherSuiteID{
			piondtls.TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,
			piondtls.TLS_ECDHE_ECDSA_WITH_AES_128_CCM_8,
			piondtls.TLS_ECDHE_ECDSA_WITH_AES_128_CCM,
		},
		ExtendedMasterSecret: piondtls.RequestExtendedMasterSecret,
	}

	l, err := coapnet.NewDTLSListener("udp", *listen, cfg)
	if err != nil {
		log.Fatalf("listen: %v", err)
	}
	defer l.Close()

	r := mux.NewRouter()
	r.DefaultHandleFunc(proxy)

	s := dtls.NewServer(
		options.WithMux(r),
		options.WithBlockwise(false, blockwise.SZX1024, time.Minute),
		options.WithErrors(func(err error) { log.Printf("coap error: %v", err) }),
		options.WithOnNewConn(func(cc *client.Conn) {
			state := "unknown"
			if dc, ok := cc.NetConn().(*piondtls.Conn); ok {
				if st, ok := dc.ConnectionState(); ok {
					state = fmt.Sprintf("suite=%s", piondtls.CipherSuiteName(st.CipherSuiteID))
				}
			}
			log.Printf("HANDSHAKE OK remote=%s %s", cc.RemoteAddr(), state)
		}),
	)
	log.Printf("listening on udp %s (DTLS 1.2), backend %s", *listen, *backend)
	if err := s.Serve(l); err != nil {
		log.Fatalf("serve: %v", err)
	}
}

func proxy(w mux.ResponseWriter, r *mux.Message) {
	path := rawPath(r.Options())
	var method string
	switch r.Code() {
	case codes.GET:
		method = http.MethodGet
	case codes.POST:
		method = http.MethodPost
	default:
		reply(w, codes.MethodNotAllowed, nil)
		return
	}

	var body []byte
	if r.Body() != nil {
		body, _ = io.ReadAll(r.Body())
	}

	req, err := http.NewRequestWithContext(r.Context(), method, *backend+path, bytes.NewReader(body))
	if err != nil {
		reply(w, codes.InternalServerError, nil)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if h, err := r.Options().GetString(optAPIHeader); err == nil {
		if k, v, ok := strings.Cut(h, ":"); ok {
			req.Header.Set(strings.TrimSpace(k), strings.TrimSpace(v))
		}
	}

	var num int64
	szx := blockwise.SZX(*maxSZX)
	block2 := false
	if v, err := r.Options().GetUint32(message.Block2); err == nil {
		reqSZX, n, _, err := blockwise.DecodeBlockOption(v)
		if err != nil {
			reply(w, codes.BadOption, nil)
			return
		}
		if reqSZX < szx {
			szx = reqSZX
		}
		num = n
		block2 = true
	}
	if method == http.MethodGet {
		start := num * int64(szx.Size())
		req.Header.Set("Range", fmt.Sprintf("bytes=%d-%d", start, start+int64(szx.Size())-1))
	}

	resp, err := httpc.Do(req)
	if err != nil {
		log.Printf("%s %s -> backend error: %v", method, path, err)
		reply(w, codes.BadGateway, nil)
		return
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)

	code := mapStatus(method, resp.StatusCode)
	log.Printf("%s %s block2=%v num=%d -> http %d -> coap %v (%d bytes)",
		method, path, block2, num, resp.StatusCode, code, len(data))

	if method == http.MethodGet && resp.StatusCode == http.StatusPartialContent {
		total := totalFromContentRange(resp.Header.Get("Content-Range"))
		more := (num+1)*int64(szx.Size()) < total
		v, err := blockwise.EncodeBlockOption(szx, num, more)
		if err != nil {
			reply(w, codes.InternalServerError, nil)
			return
		}
		w.SetResponse(code, message.AppOctets, bytes.NewReader(data),
			message.Option{ID: message.Block2, Value: encodeUint(v)})
		return
	}
	reply(w, code, data)
}

// rawPath joins Uri-Path options verbatim. The Zephyr UpdateHub client sends
// the whole download path as one option containing '/', which strict CoAP
// stacks reject as an invalid segment.
func rawPath(opts message.Options) string {
	var segs []string
	for _, o := range opts {
		if o.ID == message.URIPath {
			segs = append(segs, string(o.Value))
		}
	}
	return "/" + strings.Join(segs, "/")
}

func reply(w mux.ResponseWriter, code codes.Code, data []byte) {
	if data == nil {
		w.SetResponse(code, message.TextPlain, nil)
		return
	}
	w.SetResponse(code, message.AppJSON, bytes.NewReader(data))
}

func mapStatus(method string, status int) codes.Code {
	switch {
	case status == http.StatusNotFound:
		return codes.NotFound
	case status >= 200 && status < 300 && method == http.MethodPost && status != http.StatusOK:
		return codes.Changed
	case status >= 200 && status < 300:
		return codes.Content
	case status >= 400 && status < 500:
		return codes.BadRequest
	default:
		return codes.BadGateway
	}
}

func totalFromContentRange(h string) int64 {
	_, total, ok := strings.Cut(h, "/")
	if !ok {
		return 0
	}
	n, _ := strconv.ParseInt(total, 10, 64)
	return n
}

func encodeUint(v uint32) []byte {
	switch {
	case v == 0:
		return nil
	case v < 1<<8:
		return []byte{byte(v)}
	case v < 1<<16:
		return []byte{byte(v >> 8), byte(v)}
	default:
		return []byte{byte(v >> 16), byte(v >> 8), byte(v)}
	}
}
