#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 O.S. Systems Software LTDA.
# SPDX-License-Identifier: Apache-2.0
# Start UpdateHub cdn (coaps-dtls branch) as the CoAPS front end for e2e.py:
#   ./run-e2e.sh --server-cmd "scripts/cdn-server.sh {cert} {key} {backend}"
# Package objects are cached; probe and report are proxied to the backend.
set -euo pipefail
cert=$1 key=$2 backend=$3
cdn=${CDN_BIN:-$(dirname "$0")/../bin/cdn-coaps}
state=$(mktemp -d)
trap 'rm -rf "$state"' EXIT
mkdir -p "$state/storage"
"$cdn" --backend "$backend" --cache '^/?products/' \
	--http 127.0.0.1:18081 --cluster 127.0.0.1:11313 \
	--coap 127.0.0.1:5683 --coaps 127.0.0.1:5684 \
	--coaps-cert "$cert" --coaps-key "$key" \
	--db "$state/state.db" --storage "$state/storage" --log debug &
pid=$!
trap 'kill $pid 2>/dev/null; wait $pid 2>/dev/null; rm -rf "$state"' EXIT TERM INT
wait $pid
