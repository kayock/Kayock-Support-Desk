#!/usr/bin/env bash
# Kayock Support Desk launcher for Linux (Ubuntu first, works on other distros).
#
# Does NOT install or restart any system services; it only:
#   1. checks Python 3 (>= 3.10) and the venv module,
#   2. creates ./data, ./.venv and installs Python dependencies on first run,
#   3. starts the Kayock server (which manages llama-server/Ollama itself).
#
# See docs/LINUX_SETUP.md for system dependencies (python3-venv, python3-tk,
# Ollama, llama-server) and headless-server notes.
set -e
cd "$(dirname "$0")"

PYTHON_BIN="${KAYOCK_PYTHON:-python3}"

fail() {
    echo ""
    echo "ERROR: $1"
    echo ""
    exit 1
}

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo ""
    echo "Python 3 was not found. Please install it with your package manager, e.g.:"
    echo "  sudo apt install python3 python3-venv python3-pip"
    echo ""
    exit 1
fi

# Python version check: 3.10 or newer is required (matches README).
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    echo ""
    echo "Python 3.10 or newer is required (found: $("$PYTHON_BIN" -V 2>&1))."
    echo "Install a newer Python and set KAYOCK_PYTHON=/path/to/python3 to use it."
    echo ""
    exit 1
fi

# The venv module lives in python3-venv on Debian/Ubuntu. Catch the common
# first-run failure before it happens, with the exact fix.
if ! "$PYTHON_BIN" -c 'import ensurepip' >/dev/null 2>&1; then
    echo ""
    echo "Python's venv/ensurepip module is missing (Ubuntu: 'python3-venv' package)."
    echo "Install it with:"
    echo "  sudo apt install python3-venv python3-pip"
    echo ""
    echo "Kayock will not install system packages on its own."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "Setting up Kayock Support Desk for the first time. This may take a few minutes..."
    "$PYTHON_BIN" -m venv .venv
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
else
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Advisory checks only -- none of these block startup. The app itself
# degrades gracefully when tkinter/a display/llama-server/Ollama are
# missing (text-field fallbacks and 'AI needs attention' states).
if ! python -c 'import tkinter' >/dev/null 2>&1; then
    echo ""
    echo "NOTE: tkinter is not installed. The Browse... buttons will fall back to"
    echo "      a plain text field. To enable native file dialogs:"
    echo "        sudo apt install python3-tk"
fi
if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    echo ""
    echo "NOTE: No graphical display detected (headless?). File-selection dialogs"
    echo "      will fall back to text fields, and no browser tab will open."
    echo "      Set KAYOCK_OPEN_BROWSER=0 to silence this."
fi
if ! command -v xdg-open >/dev/null 2>&1; then
    echo ""
    echo "NOTE: xdg-open not found; if a browser tab does not open automatically,"
    echo "      open the URL printed below manually."
fi

echo ""
echo "Starting Kayock Support Desk..."
echo ""
exec python run.py
