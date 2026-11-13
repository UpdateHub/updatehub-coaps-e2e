#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 O.S. Systems Software LTDA.
# SPDX-License-Identifier: Apache-2.0
#
# DEV ONLY test PKI, mirroring UpdateHub's production layout: two P-256 roots
# (R1 signs the server, R2 is the standby) shipped to the device as one PEM
# bundle, dev-roots.pem. Never use any of this in a product.
#
# Roots are kept across runs, because builds embed dev-roots.pem and
# --no-build reuses those builds. Leaves are recreated every run:
#   server      valid for coap.updatehub.io, localhost and 127.0.0.1 (R1)
#   qemu-host   valid for QEMU's slirp host address 10.0.2.2 (R1)
#   standby     valid, signed by R2
#   wrong-root  valid names, signed by a root the device does not trust
#   wrong-host  signed by R1 for not-updatehub.example
#   expired     signed by R1, expired on 2025-02-01
set -euo pipefail

out=${1:?output directory}
mkdir -p "$out"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

good_san="subjectAltName=DNS:coap.updatehub.io,DNS:localhost,IP:127.0.0.1"

root() {
	local name=$1 cn=$2
	[ -f "$out/$name.key" ] && return
	openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
		-keyout "$out/$name.key" -subj "/O=UpdateHub DEV ONLY/CN=$cn" \
		-days 3650 -out "$out/$name.pem" \
		-addext basicConstraints=critical,CA:TRUE,pathlen:0 \
		-addext keyUsage=critical,keyCertSign,cRLSign 2>/dev/null
}

leaf() {
	local name=$1 ca=$2 san=$3
	shift 3
	openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
		-keyout "$out/$name.key" -subj "/O=UpdateHub DEV ONLY/CN=$name" \
		-out "$tmp/$name.csr" 2>/dev/null
	openssl x509 -req -in "$tmp/$name.csr" -CA "$ca.pem" -CAkey "$ca.key" \
		-set_serial "0x$(openssl rand -hex 8)" -sha256 \
		-extfile <(printf '%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n' "$san") \
		"$@" -out "$out/$name.pem" 2>/dev/null
}

root dev-root-r1 "UpdateHub DEV Root CA R1"
root dev-root-r2 "UpdateHub DEV Root CA R2"
cat "$out/dev-root-r1.pem" "$out/dev-root-r2.pem" >"$out/dev-roots.pem"

openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
	-keyout "$tmp/wrong-root.key" -subj "/O=Not UpdateHub/CN=Wrong Root CA" \
	-days 30 -out "$tmp/wrong-root.pem" \
	-addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign 2>/dev/null

leaf server "$out/dev-root-r1" "$good_san" -days 30
leaf qemu-host "$out/dev-root-r1" "subjectAltName=IP:10.0.2.2" -days 30
leaf standby "$out/dev-root-r2" "$good_san" -days 30
leaf wrong-root "$tmp/wrong-root" "$good_san" -days 30
leaf wrong-host "$out/dev-root-r1" "subjectAltName=DNS:not-updatehub.example" -days 30
leaf expired "$out/dev-root-r1" "$good_san" -not_before 20250101000000Z -not_after 20250201000000Z

for c in server qemu-host standby wrong-root wrong-host expired; do
	printf '%-11s ' "$c"
	openssl x509 -in "$out/$c.pem" -noout -issuer -enddate -ext subjectAltName | tr '\n' ' '
	echo
done
