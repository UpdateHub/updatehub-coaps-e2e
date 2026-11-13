# Results: UpdateHub CoAPS/DTLS on Zephyr (2026-11-13)

## Summary

On upstream Zephyr main and on v4.5-branch, the client could not complete a
verified DTLS handshake with any server. With the series in `patches/`, it does
so in simulation against both the pion/dtls stand-in and UpdateHub cdn
(`coaps-dtls` branch, OSSystems/cdn#15). A full probe → download → install
cycle completes, the downloaded image verifies in flash, and wrong-root,
wrong-hostname and expired server certificates are all rejected. A single
probe against production (`coap.updatehub.io:5684`) with the shipped R1+R2
bundle also succeeds (see "Production probe").

## Zephyr series

Base: main `c4647e2491f` (`patches/main`). The same four commits cherry-pick
cleanly onto v4.5-branch `cc8e9835a3d` (`patches/v4.5`);
`scripts/setup-workspace.sh` recreates either tree. Every commit builds on its
own.

Zephyr's `scripts/ci/check_compliance.py`, run with
`scripts/requirements-compliance.txt`, passes Gitlint, Checkpatch,
CMakeStyle, LicenseAndCopyrightCheck, Nits, KconfigBasic, KconfigFormat,
SphinxLint, YAMLLint, BinaryFiles and the other file checks. Notes:
- The full Kconfig check only flags symbols from modules this workspace
  doesn't check out (LVGL, CMSIS-DSP, Percepio, OpenThread), none from this
  series.
- ClangFormat leaves one notice: the `0x00` that NUL-terminates the
  generated include in `c_certificates.h`, written the same way as the
  existing `samples/net/common/quic_certs/quic_certificate.h`.
- `reuse lint-file` accepts the new PEM bundle.

Twister `--build-only` builds all three `tests.yaml` configurations on
frdm_k64f with no warnings (warnings are errors).

1. **mgmt: updatehub: set the DTLS server host name.** The library never set
   `ZSOCK_TLS_HOSTNAME`. Without it, on main mbedTLS aborts verification
   (`Certificate verification without having set hostname`, -0x5d80; this
   was reproduced). v4.5's socket layer sets `""` instead, which by code
   reading can never match. A bug fix and backport candidate.
2. **mgmt: updatehub: allow overriding the server address.**
   `CONFIG_UPDATEHUB_SERVER` is now always available. It defaults to
   `coap.updatehub.io` when `UPDATEHUB_CE=n` and must still be set with CE,
   so existing configurations keep their behaviour. The sample's 802.15.4
   and OpenThread overlays already set it without CE, which was silently
   ignored before; it now takes effect.
3. **mgmt: updatehub: make the DTLS security tag configurable.**
   `CONFIG_UPDATEHUB_DTLS_SEC_TAG`, default 1.
4. **samples: mgmt: updatehub: pin the UpdateHub root CAs for DTLS.**
   - Ships production roots R1 and R2 as `src/certificates/updatehub-root-ca.pem`.
     R1 has SHA-256 `D2:D8:AB:…:49:3E` and signs the server; R2 has
     `73:E8:0C:…:C2:AB` and is the standby. Both are ECDSA P-256 and valid
     until 2051.
   - Registers the bundle as `TLS_CREDENTIAL_CA_CERTIFICATE` under
     `UPDATEHUB_DTLS_SEC_TAG` and drops the private key and the old RSA
     certificate (expired in 2020).
   - Adds `CONFIG_UPDATEHUB_SAMPLE_ROOT_CA_FILE` so the roots can be swapped.
   - Moves `overlay-dtls.conf` to ECDHE-ECDSA-AES128-GCM-SHA256 with extended
     master secret and PEM parsing, and sets a 24 KiB mbedTLS heap.
   - Rewrites README step 2.2.

### Several CA certificates under one security tag

Zephyr's TLS credentials keep **one credential per (tag, type)**. A second
`tls_credential_add(tag, TLS_CREDENTIAL_CA_CERTIFICATE, …)` returns `-EEXIST`
in both the volatile and protected-storage backends. Two ways work:

- **One tag, PEM bundle (used).** Concatenate the roots into one PEM buffer
  ending in NUL and add it once. The socket layer calls
  `mbedtls_x509_crt_parse()`, which loads every certificate in the buffer
  into the CA chain. This needs `CONFIG_MBEDTLS_PEM_PARSE_C`: +2.3 KB flash
  on frdm_k64f compared with one DER root.
- **One tag per root.** Each tag holds one DER root, and all tags go into
  `ZSOCK_TLS_SEC_TAG_LIST`; the socket layer merges every tag's CA certs into
  one chain. This would need the library to take a list of tags. Every
  listed tag must exist, or the handshake fails with `-ENOENT`.

Rollover was tested: a server whose leaf is signed by the second root in the
bundle (dev "R2") completes the handshake.

Both production roots carry `pathlen:0`, so the server leaf must be signed
directly by R1 or R2. An intermediate CA would be rejected.

## How the harness was run

`./run-e2e.sh` runs inside this repo's flake devshell. The final runs below
started from a fresh clone of this repository, with the workspace created by
`scripts/setup-workspace.sh`.

| Run | Command | Result |
|---|---|---|
| main, stand-in, native_sim + qemu_x86 | `./run-e2e.sh` | PASS, 7/7 |
| v4.5, stand-in, native_sim + qemu_x86 | `E2E_WORKSPACE=$PWD/ws45 E2E_BUILD=build45 ./run-e2e.sh` | PASS, 7/7 |
| main, **real cdn** (`coaps-dtls` @ `08b3188`), native_sim | `./run-e2e.sh --skip-qemu --server-cmd "$PWD/scripts/cdn-server.sh {cert} {key} {backend}"` | PASS, 6/6 |
| frdm_k64f footprint | `scripts/devshell scripts/footprint.sh` | see below |
| production probe, native_sim | `scripts/devshell scripts/production-probe.sh` | PASS (see "Production probe") |
| Twister build-only, frdm_k64f | `west twister -T samples/subsys/mgmt/updatehub -p frdm_k64f --build-only` | 3/3 built |

Scenarios checked by the harness:

| Scenario | Checks | Outcome |
|---|---|---|
| positive (native_sim) | probe 4.04 → publish → `updatehub run` → 65×1024 B blocks → SHA-256 on download and in flash; slot1 byte-identical; reports downloading/installing/downloaded/installed/rebooting | pass; cipher suite ECDHE-ECDSA-AES128-GCM-SHA256 with EMS |
| positive (qemu_x86, full Zephyr IP stack over slirp, IP-literal server name `10.0.2.2`) | same, 33×1024 B | pass |
| standby root | leaf signed by the 2nd root in the bundle | handshake and probe pass |
| wrong root | leaf from an untrusted root | `BADCERT_NOT_TRUSTED` (0x08); alert UnknownCA; no request reaches the backend |
| wrong host | valid chain, SAN `not-updatehub.example` | `BADCERT_CN_MISMATCH` (0x04); alert BadCertificate |
| expired leaf | valid chain, expired 2025-02-01 | `BADCERT_EXPIRED` (0x01); alert CertificateExpired |

The cdn ran with `--cache '^/?products/'`. With the default `--cache ""`,
every path counts as a cached object, so the probe is answered from object
storage and the device always sees "no update". Production must keep probe
and report out of the cache regex.

## Production probe

`scripts/devshell scripts/production-probe.sh` (2026-11-13, after the local e2e
passed): native_sim/native/64, the series before its final formatting-only
and SPDX-header changes (`e09506550b1`), sample defaults
(`CONFIG_UPDATEHUB_SERVER="coap.updatehub.io"`, the shipped
`updatehub-root-ca.pem` with R1 and R2), date checks on (host clock), and the
throwaway product UID
`8ae70b5860c8a4328a36f3531aac56ace2ea5827097346a81428f3cb31ef54c9`
(sha256 of "updatehub-coaps-e2e throwaway product, no customer"). It is the
polling mode's boot-time probe only: the script stops the device after the
answer, so no update, download or report is sent.

| Check | Result |
|---|---|
| DNS | `coap.updatehub.io` → 5.161.251.167 |
| Handshake | 1, ECDHE-ECDSA-AES128-GCM-SHA256 |
| Server certificate | `O=O.S. Systems, CN=coap.updatehub.io`, SAN `coap.updatehub.io`, issued by `UpdateHub Device Root CA R1` |
| Verification | flags clear (chain to R1, host name, dates) |
| Probe | CoAP 4.04, device logs `No update available` |

The probe sent the throwaway product UID, the simulator's hwinfo device ID,
`hardware: native_sim` and version 1.0.0.

## Footprint and memory

frdm_k64f (Cortex-M4), sample `prj.conf`:

| Build | FLASH | RAM | Δ FLASH | Δ RAM |
|---|---|---|---|---|
| plain CoAP | 209,192 | 80,636 | — | — |
| + new `overlay-dtls.conf` | 289,616 | 118,124 | **+80,424** | **+37,488** (24,576 of it is the mbedTLS heap) |
| + old RSA overlay (for comparison) | 298,344 | 154,924 | +89,152 | +74,288 |

Runtime, from the device shell after a full cycle:

| | native_sim/native/64 | qemu_x86 (32-bit) |
|---|---|---|
| mbedTLS heap peak | 11,248 B | 9,872 B |
| sysworkq stack (polling probe) | n/a | 3,396 / 8,192 B |
| shell stack (`updatehub run`, download) | n/a | 3,752 / 8,192 B |

**Heap sizing.** 16 KiB is *not* enough even though peak use is ~10 KiB.
mbedTLS's buffer allocator is first-fit and fragments: with both roots
loaded, PSA returned `PSA_ERROR_INSUFFICIENT_MEMORY` (-141) while checking
R1's signature, and the handshake failed as "not trusted". Found under gdb;
whether it failed depended on certificate order. 20 KiB passed; the overlay
uses 24 KiB.

**Stacks.** `prj.conf` already gives sysworkq and shell 8 KiB, more than
double the measured use. The old overlay's `MAIN_STACK_SIZE=3000` was dropped:
`main()` only registers the CA bundle. On qemu_x86, `MAIN_STACK_SIZE=1024`
crashes at boot even **without** DTLS, so that is board-specific and not
caused by DTLS.

## CoAP block size after DTLS overhead

1024-byte blocks (`CONFIG_UPDATEHUB_COAP_BLOCK_SIZE_EXP=6`, the default) fit.

- Largest DTLS datagram received: 1,079 B (stand-in) and 1,082 B (cdn). That
  is the 1,024 B payload, 18–21 B of CoAP header and options, and 37 B of
  DTLS 1.2 AES-GCM overhead (13 B header, 8 B explicit nonce, 16 B tag).
- On the wire this is 1,110 B over IPv4 or 1,130 B over IPv6, below the
  1,280 B IPv6 minimum MTU, so no IP fragmentation on Ethernet, Wi-Fi or
  typical cellular. 6LoWPAN/Thread fragments at the link layer anyway; use
  EXP=5 there if link-layer fragmentation is a problem.

Two margins to watch:

- The client's receive buffer is `MAX_DOWNLOAD_DATA` = 1,056 B and a
  1024-byte block response uses 1,042–1,045 B, leaving 11–14 B. One extra
  CoAP option from the server (an ETag, for example) would truncate blocks.
- The client advertises DTLS `max_fragment_length` = 1024
  (`CONFIG_NET_SOCKETS_DTLS_MAX_FRAGMENT_LENGTH`). pion ignores MFL, so the
  1,045 B record is accepted. A server that honoured MFL would have to
  reject EXP=6.

## Open issues

1. **Certificate expiry is not checked by default.** Expiry is only enforced
   with `CONFIG_MBEDTLS_HAVE_TIME_DATE=y` and a trusted clock (SNTP or RTC)
   set before the first connection. Without a clock, every certificate is
   rejected as not yet valid (`BADCERT_FUTURE`, 0x200, reproduced on
   qemu_x86). The sample leaves the option off and the README explains why.
   Each product must decide based on its board's time source. The 2051
   root expiry makes this mostly about server leaf rotation and revocation.
2. **A full DTLS handshake for every request.** A probe-and-update cycle opens
   8 sockets (probe, 5 reports, download, plus the first probe), which means
   8 ECDHE-ECDSA handshakes. This is costly on cellular. Possible fixes in
   the library: reuse one socket per cycle, or enable session resumption
   (`ZSOCK_TLS_SESSION_CACHE`).
3. **DTLS Connection ID** (`CONFIG_MBEDTLS_SSL_DTLS_CONNECTION_ID` and the
   `ZSOCK_TLS_DTLS_CID` socket option) is worth enabling for cellular NAT
   rebinding during long downloads. It is not tested: the library sets no
   CID and pion's server side would need `ConnectionIDGenerator` in cdn.
   Note that `Kconfig.mbedtls` defines `MBEDTLS_SSL_DTLS_CONNECTION_ID`
   twice (upstream cleanup).
4. **Non-standard Uri-Path.** The client sends the download path as a single
   Uri-Path option containing `/`. RFC 7252 doesn't allow this and strict
   stacks reject it (go-coap v3: "invalid encoding"). cdn's go-coap fork
   accepts it, and the stand-in works around it. Fixing the client means
   coordinating with the server.
5. **Probe fails without logging** when hwinfo has no device ID
   (`updatehub_get_device_identity()`), found on qemu_x86.
6. **The sample's manual mode does not compile** (`ret` undeclared in
   `start_updatehub()`, missing `break` after `UPDATEHUB_HAS_UPDATE`). This
   is pre-existing and not fixed in this series.
7. **Upstream review may ask** for a release note on `UPDATEHUB_SERVER` and
   `UPDATEHUB_DTLS_SEC_TAG`, and possibly for a native_sim DTLS variant in
   `tests.yaml`, which would need NSOS configs in the sample.
8. `NET_BUF_RX_COUNT=50` is kept from the old overlay; its necessity was not
   measured.

## Harness notes

- native_sim uses offloaded sockets (host networking, no root). It can't
  report thread stack use, which is why qemu_x86 is there (with a test-only
  module in `harness/` that seeds the slot0 MCUboot header and a device ID).
- The swap-using-offset MCUboot mode on main stores the secondary image one
  sector into slot1; the harness accounts for it.
- `scripts/make-test-pki.sh` generates a DEV-ONLY PKI in `run/testpki/`:
  roots R1 and R2, the bundle, and the server and negative-test leaves.
