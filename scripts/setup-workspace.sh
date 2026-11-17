#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 O.S. Systems Software LTDA.
# SPDX-License-Identifier: Apache-2.0
#
# Create the west workspace the harness builds against: upstream Zephyr at
# the tested revision with patches/<series> applied, plus only the modules
# the targets need. Run inside the devshell:
#   scripts/devshell scripts/setup-workspace.sh main   # -> ws/
#   scripts/devshell scripts/setup-workspace.sh v4.5   # -> ws45/
# ZEPHYR_GIT_REFERENCE=<existing zephyr clone> borrows its objects.
set -euo pipefail

series=${1:-main}
e2e=$(cd "$(dirname "$0")/.." && pwd)
case $series in
main) ws=$e2e/ws rev=5ac3f20197937a2ee859e1c789bc3b1c11faf219 ;;
v4.5) ws=$e2e/ws45 rev=cc8e9835a3de0dbcbe18207688c9a05b097c7d04 ;;
*) echo "unknown series: $series" >&2; exit 1 ;;
esac

if [ ! -d "$ws/zephyr" ]; then
	git clone ${ZEPHYR_GIT_REFERENCE:+--reference-if-able "$ZEPHYR_GIT_REFERENCE" --dissociate} \
		https://github.com/zephyrproject-rtos/zephyr "$ws/zephyr"
	git -C "$ws/zephyr" checkout -q -b updatehub-dtls-ecdsa "$rev"
	GIT_COMMITTER_NAME=${GIT_COMMITTER_NAME:-e2e} GIT_COMMITTER_EMAIL=${GIT_COMMITTER_EMAIL:-e2e@localhost} \
		git -C "$ws/zephyr" am -q "$e2e/patches/$series"/*.patch
fi

cd "$ws"
[ -d .west ] || python3 -m west init -l zephyr
python3 -m west config manifest.project-filter -- '-.*,+mbedtls,+tf-psa-crypto,+mcuboot,+zcbor,+hal_nxp,+cmsis_6'
python3 -m west update --narrow -o=--depth=1
