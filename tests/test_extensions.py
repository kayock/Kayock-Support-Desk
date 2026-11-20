"""
Tests for the extension boundaries:

- OBD adapter interface: without a registered adapter, get_adapter()
  returns NullAdapter (honest "no scanner connected" behavior); a
  registered adapter is returned and used.
- OCR hook: unavailable by default with an actionable missing-dependency
  message; when available, scanned PDFs are OCR'd through the same
  pipeline (chunking/citations unchanged); failures degrade to the
  standard clean indexing error, never a crash.
"""
import pytest

from app.services import indexing
from app.services.extensions import obd, ocr


# ----------------------------------------------------------------- OBD ----

def test_no_adapter_registered_returns_null():
    adapter = obd.get_adapter()
    assert adapter.is_available() is False
    assert adapter.connect() is False
    assert adapter.read_dtcs() == []
    assert adapter.read_live_data() == {}
    assert adapter.clear_dtcs() is False
    adapter.disconnect()  # must not raise


def test_registered_adapter_is_used():
    class FakeAdapter:
        name = "fake-bt"

        def is_available(self):
            return True

        def connect(self):
            return True

        def read_dtcs(self):
            return [{"code": "P0302", "status": "stored"}]

        def read_live_data(self, pids=None):
            return {"rpm": 800}

        def clear_dtcs(self):
            return True

        def disconnect(self):
            return None

    obd.register_adapter(FakeAdapter())
    try:
        adapter = obd.get_adapter()
        assert adapter.name == "fake-bt"
        assert adapter.read_dtcs()[0]["code"] == "P0302"
    finally:
        obd.register_adapter(None)
    assert obd.get_adapter().name == "none"


# ----------------------------------------------------------------- OCR ----

def test_ocr_availability_reports_missing_pieces_cleanly():
    ok, missing = ocr.ocr_available()
    if ok:
        pytest.skip("OCR optional deps are installed in this environment")
    assert ok is False
    assert "pytesseract" in missing or "tesseract" in missing or "pypdfium2" in missing


def test_scanned_pdf_without_ocr_gives_actionable_error(client, monkeypatch):
    """A text-less PDF with OCR unavailable indexes to a clean error that
    names what to install -- not a raw dependency traceback."""
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)  # no text
    buf = io.BytesIO()
    writer.write(buf)
    scanned = buf.getvalue()

    ok, _missing = ocr.ocr_available()
    if ok:
        pytest.skip("OCR optional deps are installed in this environment")

    res = client.post("/api/devices", data={"name": "Scanned Device"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("scan.pdf", scanned, "application/pdf")},
    )
    doc_id = res.json()["document_ids"][0]
    import time

    deadline = time.time() + 15
    status = None
    while time.time() < deadline:
        status = client.get(f"/api/documents/{doc_id}/status").json()
        if status["status"] in ("ready", "error"):
            break
        time.sleep(0.05)
    assert status["status"] == "error"
    assert "scan" in status["error"].lower()
    assert "pip install" in status["error"].lower() or "apt install" in status["error"].lower()


def test_scanned_pdf_with_ocr_available_indexes(monkeypatch, tmp_path):
    """With OCR 'available', a text-less PDF goes through the normal
    pipeline: pages extracted via ocr_pages() are chunked/stored exactly
    like an embedded text layer."""
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    scanned = buf.getvalue()

    scan_path = tmp_path / "scan.pdf"
    scan_path.write_bytes(scanned)

    monkeypatch.setattr(ocr, "ocr_available", lambda: (True, ""))
    monkeypatch.setattr(
        ocr, "ocr_pages", lambda path: [(1, "Rear Differential\nDrain plug torque: 54 Nm on the differential.")]
    )
    pages = indexing.extract_pages(scan_path)
    assert pages == [(1, "Rear Differential\nDrain plug torque: 54 Nm on the differential.")]
    chunks = indexing.chunk_pages(pages, "scan.pdf")
    assert any("54 Nm" in c["text"] for c in chunks)
    assert any(c["section"] for c in chunks), "OCR text flows through section-aware chunking too"


def test_ocr_failure_degrades_to_clean_error(monkeypatch, tmp_path):
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    scan_path = tmp_path / "scan.pdf"
    scan_path.write_bytes(buf.getvalue())

    monkeypatch.setattr(ocr, "ocr_available", lambda: (True, ""))
    monkeypatch.setattr(ocr, "ocr_pages", lambda path: (_ for _ in ()).throw(RuntimeError("OCR failed on page 1: tesseract died")))
    with pytest.raises(indexing.IndexingError) as exc_info:
        indexing.extract_pages(scan_path)
    # The ocr_pages RuntimeError is a user-safe message by contract --
    # surfaced as a clean indexing error, never a raw crash.
    assert "OCR failed" in str(exc_info.value)
