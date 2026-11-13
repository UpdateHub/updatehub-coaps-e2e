/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 O.S. Systems Software LTDA.
 * SPDX-License-Identifier: Apache-2.0
 */

#include <zephyr/init.h>
#include <zephyr/storage/flash_map.h>
#include <zephyr/sys/byteorder.h>

#define IMAGE_MAGIC 0x96f3b83d

struct image_header {
	uint32_t magic;
	uint32_t load_addr;
	uint16_t hdr_size;
	uint16_t protect_tlv_size;
	uint32_t img_size;
	uint32_t flags;
	uint8_t major;
	uint8_t minor;
	uint16_t revision;
	uint32_t build_num;
	uint32_t pad;
} __packed;

static int slot0_header_init(void)
{
	const struct flash_area *fa;
	struct image_header hdr = {
		.magic = sys_cpu_to_le32(IMAGE_MAGIC),
		.hdr_size = sys_cpu_to_le16(0x200),
		.major = 1,
	};
	int ret;

	ret = flash_area_open(PARTITION_ID(slot0_partition), &fa);
	if (ret < 0) {
		return ret;
	}

	ret = flash_area_flatten(fa, 0, fa->fa_size);
	if (ret == 0) {
		ret = flash_area_write(fa, 0, &hdr, sizeof(hdr));
	}

	flash_area_close(fa);
	return ret;
}

SYS_INIT(slot0_header_init, APPLICATION, 0);

#if defined(CONFIG_HWINFO)
#include <string.h>
#include <zephyr/drivers/hwinfo.h>

/* qemu_x86 has no hwinfo driver; the UpdateHub probe needs a device id. */
ssize_t z_impl_hwinfo_get_device_id(uint8_t *buffer, size_t length)
{
	static const uint8_t id[] = {0xe2, 0xe0, 0x51, 0x86};

	length = MIN(length, sizeof(id));
	memcpy(buffer, id, length);
	return length;
}
#endif
