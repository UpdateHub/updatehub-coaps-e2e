#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 O.S. Systems Software LTDA.
# SPDX-License-Identifier: Apache-2.0
# Build the native_sim flash backing file (slot0 holds an MCUboot image
# header so the client can read its firmware version) and the update image
# served by the fake backend.
set -euo pipefail

out=${1:?output directory}
mkdir -p "$out"

flash_size=$((2048 * 1024))
slot0_off=$((0xc000))
slot_size=$((0x69000))

mkimg() {
	local version=$1 payload_kib=$2 dest=$3
	head -c $((payload_kib * 1024)) /dev/urandom >"$out/payload.bin"
	imgtool create --version "$version" --header-size 0x200 --pad-header \
		--align 8 --slot-size "$slot_size" "$out/payload.bin" "$dest" >/dev/null
	rm "$out/payload.bin"
}

mkimg 1.0.0 16 "$out/slot0.bin"
mkimg 2.0.0 64 "$out/update.bin"
# qemu_x86 slots are 64 KiB including the trailer
mkimg 2.0.0 32 "$out/update-32k.bin"

head -c "$flash_size" /dev/zero | tr '\0' '\377' >"$out/flash.bin"
dd if="$out/slot0.bin" of="$out/flash.bin" bs=1 seek="$slot0_off" conv=notrunc status=none
echo "flash: $out/flash.bin  update: $out/update.bin ($(stat -c %s "$out/update.bin") bytes)"
