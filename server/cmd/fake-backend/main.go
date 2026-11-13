// SPDX-FileCopyrightText: Copyright (c) 2026 O.S. Systems Software LTDA.
// SPDX-License-Identifier: Apache-2.0

// Command fake-backend implements the UpdateHub device API endpoints the
// Zephyr client uses (probe, object download, report) over HTTP, so the
// CoAPS front end can be exercised without updatehub-ce.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)

func main() {
	listen := flag.String("listen", "127.0.0.1:8080", "HTTP listen address")
	product := flag.String("product", "", "product UID")
	pkgFile := flag.String("package", "", "image to offer; empty means no update")
	flag.Parse()

	// The package is read on every probe so a harness can start with no
	// update offered and publish one later by creating the file.
	load := func() ([]byte, string) {
		if *pkgFile == "" {
			return nil, ""
		}
		image, err := os.ReadFile(*pkgFile)
		if err != nil {
			return nil, ""
		}
		h := sha256.Sum256(image)
		return image, hex.EncodeToString(h[:])
	}

	http.HandleFunc("POST /upgrades", func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		image, sum := load()
		log.Printf("PROBE api=%q body=%s", r.Header.Get("Api-Content-Type"), body)
		if image == nil {
			log.Printf("PROBE -> no update")
			http.NotFound(w, r)
			return
		}
		log.Printf("PROBE -> update sha256=%s size=%d", sum, len(image))
		obj := []map[string]any{{"mode": "raw", "sha256sum": sum, "size": len(image)}}
		meta, _ := json.Marshal(map[string]any{
			"product":            *product,
			"supported-hardware": "any",
			"objects":            [][]map[string]any{obj, obj},
		})
		w.Header().Set("Content-Type", "application/json")
		w.Write(meta)
	})

	http.HandleFunc("GET /products/{product}/packages/{pkg}/objects/{sha}", func(w http.ResponseWriter, r *http.Request) {
		image, sum := load()
		if image == nil || r.PathValue("product") != *product || r.PathValue("sha") != sum {
			log.Printf("DOWNLOAD unknown object %s", r.URL.Path)
			http.NotFound(w, r)
			return
		}
		log.Printf("DOWNLOAD %s range=%s", r.URL.Path, r.Header.Get("Range"))
		http.ServeContent(w, r, "image.bin", time.Time{}, bytes.NewReader(image))
	})

	http.HandleFunc("POST /report", func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		log.Printf("REPORT %s", strings.TrimSpace(string(body)))
		w.WriteHeader(http.StatusOK)
	})

	fmt.Fprintf(os.Stderr, "fake-backend on %s, package %q\n", *listen, *pkgFile)
	log.Fatal(http.ListenAndServe(*listen, nil))
}
