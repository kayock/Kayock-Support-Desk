#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

PYTHON_BIN="python3"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo ""
    echo "Python 3 was not found. Please install it from https://www.python.org/downloads/"
    echo "or with Homebrew: brew install python"
    echo ""
    read -p "Press Enter to close..."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "Setting up Kayock Support Desk for the first time. This may take a few minutes..."
    "$PYTHON_BIN" -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip >/dev/null
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

echo ""
echo "Starting Kayock Support Desk..."
echo ""
python run.py

read -p "Press Enter to close..."
