#!/usr/bin/env bash
# Starts a Kayock server + fake model server for UI visual inspection,
# seeds demo data, and prints the base URL. Used by the visual check step.
set -e
cd "$(dirname "$0")/.."

export KAYOCK_DATA_DIR="${1:-/tmp/kayock_ui_check}"
export KAYOCK_OPEN_BROWSER=0
export KAYOCK_PORT=8798
export KAYOCK_HOST=127.0.0.1

if [ ! -f "$KAYOCK_DATA_DIR/.seeded" ]; then
    .venv/bin/python scripts/seed_demo.py "$KAYOCK_DATA_DIR" || true
fi

exec .venv/bin/python run.py
