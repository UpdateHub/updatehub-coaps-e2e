# SPDX-FileCopyrightText: Copyright (c) 2026 O.S. Systems Software LTDA.
# SPDX-License-Identifier: Apache-2.0

if(CONFIG_E2E_SLOT0_HEADER)
  zephyr_library()
  zephyr_library_sources(src/slot0_header.c)
endif()
