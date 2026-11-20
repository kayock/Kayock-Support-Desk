"""
Entry point: starts the Kayock Support Desk web server, prints how to
reach it from this computer and from other devices on the LAN, and opens
it in the default browser once it's actually ready to serve requests.
"""
import os
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from app.config import HOST, PORT
from app.services import network_info

# Set KAYOCK_OPEN_BROWSER=0 to skip auto-opening a browser tab (e.g. when
# running headless, in a container, or during automated testing).
OPEN_BROWSER = os.environ.get("KAYOCK_OPEN_BROWSER", "1") not in ("0", "false", "False")


def _wait_for_server_and_open_browser(url: str, timeout_seconds: float = 15.0) -> None:
    """Polls localhost until the server is accepting connections, then
    opens the default browser. Runs in a background thread so it never
    blocks or delays server startup; any failure here (no display, no
    browser available, etc.) is swallowed -- the printed URLs are always
    there as a fallback."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        return  # server never came up in time; skip opening a browser

    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    lan_url = network_info.get_lan_url()
    hostname_url = network_info.get_hostname_url()
    local_url = f"http://localhost:{PORT}"

    print("=" * 60)
    print("  Kayock Support Desk")
    print("=" * 60)
    print(f"  On this computer:   {local_url}")
    print(f"  On your network:    {lan_url}")
    print(f"  Friendly hostname:  {hostname_url}  (if supported by your network)")
    print("  A QR code for the network URL is on the Settings page once it's running.")
    print("=" * 60)
    print("  Press Ctrl+C to stop.")
    print()

    if OPEN_BROWSER:
        threading.Thread(
            target=_wait_for_server_and_open_browser, args=(local_url,), daemon=True
        ).start()

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False, log_level="info")


if __name__ == "__main__":
    sys.exit(main())
