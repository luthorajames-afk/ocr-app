#!/usr/bin/env bash
set -euo pipefail
WORKER="${1:-simple}"
python scripts/build.py --worker "$WORKER"