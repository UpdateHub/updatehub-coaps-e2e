# UpdateHub CoAPS/DTLS end-to-end tests for Zephyr

This repository tests the [Zephyr](https://zephyrproject.org) UpdateHub client
(`subsys/mgmt/updatehub` and `samples/subsys/mgmt/updatehub`) over CoAPS
(CoAP over DTLS 1.2), in simulation, against a server that behaves like
UpdateHub Cloud. It also carries the Zephyr patches that make the client work
with UpdateHub Cloud over DTLS. Results, measurements and open issues are in
[RESULTS.md](RESULTS.md).

```
patches/main/   Zephyr series on upstream main (git am)
patches/v4.5/   the same series on v4.5-branch
server/         Go stand-in for UpdateHub cdn (pion/dtls + go-coap) and a
                fake UpdateHub HTTP API (probe, object download, report)
harness/        Zephyr module used only on qemu_x86 (slot0 header, device id)
conf/           Kconfig fragments layered on top of the sample's dtls.conf
scripts/        e2e.py (the harness), workspace setup, PKI and image generators,
                footprint report, cdn wrapper, production probe
flake.nix       toolchain: Zephyr SDK (x86_64, arm), west, QEMU, Go, imgtool
```

The only requirement is [Nix](https://nixos.org) with flakes enabled;
`scripts/devshell <cmd>` runs a command with the whole toolchain.

## Quick start

```sh
scripts/devshell scripts/setup-workspace.sh main   # Zephyr main + patches in ws/
./run-e2e.sh                                       # build and run every scenario
```

`run/results.json` holds the outcome, and every process logs to
`run/logs/<scenario>/`.

Other runs:

```sh
./run-e2e.sh --no-build            # reuse the builds
./run-e2e.sh --skip-qemu           # native_sim only

# v4.5 backport
scripts/devshell scripts/setup-workspace.sh v4.5
E2E_WORKSPACE=$PWD/ws45 E2E_BUILD=build45 ./run-e2e.sh

# against UpdateHub cdn (github.com/OSSystems/cdn) built to bin/cdn-coaps
./run-e2e.sh --skip-qemu --server-cmd "$PWD/scripts/cdn-server.sh {cert} {key} {backend}"

# flash/RAM cost of dtls.conf on a real MCU board
scripts/devshell scripts/footprint.sh frdm_k64f
```

## What a run does

1. Builds the Go servers, a native_sim flash image whose slot0 holds an
   MCUboot header (version 1.0.0), the update images, and a DEV-ONLY test PKI
   in `run/testpki/` (`scripts/make-test-pki.sh`). The PKI mirrors
   production: two roots, R1 signing the server and R2 as the standby,
   shipped to the device as one PEM bundle.
2. Builds the sample with `dtls.conf`, the dev bundle as
   `CONFIG_UPDATEHUB_SAMPLE_ROOT_CA_FILE`, and `CONFIG_UPDATEHUB_SERVER`
   pointing at the local server (`localhost`, or `10.0.2.2` under QEMU slirp).
3. Positive scenarios: the device boots and probes (no update), the harness
   publishes an image, runs `updatehub run` on the device shell, and waits
   for the client to verify the image in flash. On native_sim it also
   compares slot1 byte for byte. Afterwards it reads the mbedTLS heap peak and
   thread stack usage from the shell.
4. Standby root: the server presents a leaf signed by the second root in the
   bundle; the handshake and probe must succeed.
5. Negative scenarios: the server presents a leaf from an untrusted root, a
   leaf for another host name, and an expired leaf. Each must fail with the
   matching mbedTLS verification flag, and no request may reach the backend.

native_sim uses offloaded sockets (host networking; no root or TAP device
needed). qemu_x86 runs the full Zephyr IP stack over QEMU user networking and
is there to measure 32-bit heap and stack use, which native_sim cannot report.

## Production probe

`scripts/devshell scripts/production-probe.sh` sends a single probe to
`coap.updatehub.io:5684` using the production roots shipped in the sample.
It uses a throwaway product UID (`conf/production-probe.conf`) and stops the
device once the probe is answered, so no update, download or report is sent.
Do not change the UID to a real product.

## License

Apache-2.0, see [LICENSE](LICENSE). The patches in `patches/` are
contributions to Zephyr under its Apache-2.0 license.
