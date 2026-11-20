"""
Detects the machine's LAN IP address (best-effort, no external network
calls) and generates a QR code PNG pointing at the app's LAN URL, so it's
easy to open the app from a phone/tablet.
"""
import io
import socket

import qrcode

from app.config import PORT


def get_lan_ip() -> str:
    """Best-effort LAN IP detection. Opens a UDP 'connection' to a
    public address (no packets are actually sent for UDP) purely so the
    OS tells us which local interface/IP would be used to reach the
    network -- this works even with no internet access."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def get_lan_url() -> str:
    return f"http://{get_lan_ip()}:{PORT}"


def get_hostname_url() -> str:
    """A friendly hostname-based URL, when the OS advertises one
    (e.g. mDNS '.local' names on macOS/Linux/Windows with Bonjour)."""
    hostname = socket.gethostname()
    if not hostname.endswith(".local"):
        hostname = f"{hostname}.local"
    return f"http://{hostname}:{PORT}"


def generate_qr_png(url: str) -> bytes:
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
