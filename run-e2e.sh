#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 O.S. Systems Software LTDA.
# SPDX-License-Identifier: Apache-2.0
# Run the UpdateHub CoAPS e2e harness. Needs Nix; everything else comes from
# this repo's flake. The Zephyr tree is expected in ws/ (see README.md).
set -euo pipefail
cd "$(dirname "$0")"
exec scripts/devshell python3 scripts/e2e.py "$@"
