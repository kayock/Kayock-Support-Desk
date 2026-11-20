import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Point the app at a throwaway data directory BEFORE any app module is imported,
# so tests never touch the real ./data used by a running installation.
TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="kayock_test_"))
os.environ["KAYOCK_DATA_DIR"] = str(TEST_DATA_DIR)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def cleanup_data_dir():
    yield
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def sample_pdf_bytes() -> bytes:
    """A minimal but valid single-page PDF containing extractable text,
    built with pypdf so the test suite needs no external fixture files."""
    from pypdf import PdfWriter
    import io

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture()
def sample_txt_bytes() -> bytes:
    text = (
        "Bosch Dishwasher SHPM88Z75N Owner's Manual\n\n"
        "Troubleshooting\n"
        "Error code E24: The drain pump is blocked or the drain hose is kinked. "
        "Turn off the dishwasher, check the drain hose for kinks, and clean the drain pump filter.\n\n"
        "Error code E15: Water leak detected in the base of the unit. Contact service.\n"
    )
    return text.encode("utf-8")


def _make_text_pdf_bytes(text: str) -> bytes:
    """Builds a real single-page PDF with actual extractable text, using
    fpdf2 (a tiny pure-Python PDF generator, dev/test dependency only --
    not something the running app depends on)."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in text.splitlines():
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


@pytest.fixture()
def encrypted_pdf_no_password_bytes() -> bytes:
    """An AES-256 encrypted PDF with an EMPTY user password -- i.e. it
    opens/reads fine without prompting anyone for a password (this is
    what tripped up 'cryptography>=3.1 is required for AES algorithm'
    before pypdf[crypto] was installed), but is still genuinely encrypted
    on disk, same as many factory service-manual PDFs."""
    import io
    from pypdf import PdfReader, PdfWriter

    plain = _make_text_pdf_bytes(
        "Cylinder 2 Misfire Detected (P0302)\n"
        "Cause: Faulty ignition coil, spark plug, or fuel injector on cylinder 2.\n"
        "Check spark plug condition and ignition coil resistance before replacing parts."
    )
    reader = PdfReader(io.BytesIO(plain))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password="", owner_password="ownersecret123", algorithm="AES-256")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture()
def encrypted_pdf_password_required_bytes() -> bytes:
    """An AES-256 encrypted PDF that genuinely requires a (non-empty) user
    password to open -- Kayock can't and shouldn't guess it."""
    import io
    from pypdf import PdfReader, PdfWriter

    plain = _make_text_pdf_bytes("This content requires a password to read.")
    reader = PdfReader(io.BytesIO(plain))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password="realpassword", owner_password="ownersecret456", algorithm="AES-256")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
