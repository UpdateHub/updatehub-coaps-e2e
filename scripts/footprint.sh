#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 O.S. Systems Software LTDA.
# SPDX-License-Identifier: Apache-2.0
# Static flash/RAM cost of dtls.conf on a real MCU board.
set -euo pipefail
e2e=$(cd "$(dirname "$0")/.." && pwd)
board=${1:-frdm_k64f}
out=$e2e/build/footprint-$board
uid='CONFIG_UPDATEHUB_PRODUCT_UID="e4d37cfe6ec48a2d069cc0bbb8b078677e9a0d8df3a027c4d8ea131130c4265f"'
printf '%s\n' "$uid" >"$e2e/run/footprint.conf"

git -C "$e2e/ws/zephyr" show updatehub-dtls-ecdsa~4:samples/subsys/mgmt/updatehub/dtls.conf \
	| grep -v MAIN_STACK_SIZE >"$e2e/run/footprint-old-dtls.conf"

for v in base dtls old-rsa-dtls; do
	case $v in
	base) conf="$e2e/run/footprint.conf" ;;
	dtls) conf="dtls.conf;$e2e/run/footprint.conf" ;;
	old-rsa-dtls) conf="$e2e/run/footprint-old-dtls.conf;$e2e/run/footprint.conf" ;;
	esac
	(cd "$e2e/ws" && python3 -m west build -p -b "$board" -d "$out-$v" zephyr/samples/subsys/mgmt/updatehub \
		-- "-DEXTRA_CONF_FILE=$conf" >"$out-$v.log" 2>&1) || { tail -20 "$out-$v.log"; exit 1; }
	grep -E "^ +(FLASH|RAM):" "$out-$v.log" | sed "s/^/$v /"
done
