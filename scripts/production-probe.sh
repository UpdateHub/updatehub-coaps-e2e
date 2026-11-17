#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 O.S. Systems Software LTDA.
# SPDX-License-Identifier: Apache-2.0
# One probe against coap.updatehub.io:5684 with the sample's production root
# bundle and a throwaway product UID. Stops after the first probe answer, so
# no update or report is ever sent. Run inside the devshell.
set -euo pipefail
e2e=$(cd "$(dirname "$0")/.." && pwd)
b=$e2e/build/production-probe
l=$e2e/run/logs/production-probe
mkdir -p "$l"
python3 - "$e2e" "$b" <<'PY'
import subprocess, sys
e2e, b = sys.argv[1:]
c = f"{e2e}/conf"
subprocess.run([sys.executable, "-m", "west", "build", "-p", "-b", "native_sim/native/64", "-d", b,
                "zephyr/samples/subsys/mgmt/updatehub", "--",
                f"-DEXTRA_CONF_FILE=dtls.conf;{c}/native_sim.conf;{c}/tls-debug.conf;{c}/production-probe.conf"],
               cwd=f"{e2e}/ws", check=True, stdout=subprocess.DEVNULL)
PY
grep -E '^CONFIG_UPDATEHUB_(SERVER|SAMPLE_ROOT_CA_FILE|PRODUCT_UID)=' "$b/zephyr/.config"
"$e2e/scripts/make-images.sh" "$e2e/run/img" >/dev/null
cp "$e2e/run/img/flash.bin" "$l/flash.bin"
"$b/zephyr/zephyr.exe" --flash="$l/flash.bin" </dev/null >"$l/device.raw" 2>&1 &
pid=$!
for _ in $(seq 60); do
	sed 's/\x1b\[[0-9;]*[a-zA-Z]//g' "$l/device.raw" >"$l/device.log"
	grep -qE 'No update available|Probe metadata received|Cannot connect|Could not|Invalid' "$l/device.log" && break
	sleep 1
done
sleep 1
kill "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true
sed 's/\x1b\[[0-9;]*[a-zA-Z]//g' "$l/device.raw" >"$l/device.log"
grep -E 'chosen ciphersuite: TLS|Certificate verification flags|subject name|issuer name|<inf> updatehub|<err> updatehub|CoAP|code' "$l/device.log" | sed 's/^uart:~\$ //' | sort -u
