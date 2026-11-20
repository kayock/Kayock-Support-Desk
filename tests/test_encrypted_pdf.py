"""
Regression tests for the encrypted-PDF fix. Before pypdf[crypto] was a
runtime dependency, an AES-encrypted PDF (common in factory service
manuals) would fail indexing with a raw dependency error:
'cryptography>=3.1 is required for AES algorithm'. These tests build real
encrypted PDFs at test time (see tests/conftest.py) and confirm:

- an AES-encrypted PDF that needs no user password indexes successfully
- a PDF that genuinely requires a password fails with a clean, plain-
  English message -- never a raw Python/dependency error
- ordinary unencrypted PDFs continue to behave exactly as before
"""
import time

from app.services import indexing, llm_client
from tests.fakes import fake_embed_texts


def _wait_for_status(client, document_id, timeout=10):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        res = client.get(f"/api/documents/{document_id}/status")
        last = res.json()
        if last["status"] in ("ready", "error"):
            return last
        time.sleep(0.05)
    return last


def test_aes_encrypted_pdf_without_password_indexes_successfully(
    client, monkeypatch, encrypted_pdf_no_password_bytes
):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)

    res = client.post("/api/devices", data={"name": "Encrypted PDF Test Vehicle", "device_type": "Car"})
    device_id = res.json()["id"]

    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("factory_manual.pdf", encrypted_pdf_no_password_bytes, "application/pdf")},
    )
    assert res.status_code == 200
    doc_id = res.json()["document_ids"][0]

    status = _wait_for_status(client, doc_id)
    assert status["status"] == "ready", status
    assert status["chunk_count"] > 0


def test_aes_encrypted_pdf_requiring_password_fails_gracefully(client, encrypted_pdf_password_required_bytes):
    res = client.post("/api/devices", data={"name": "Password Required Test Vehicle", "device_type": "Car"})
    device_id = res.json()["id"]

    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("locked_manual.pdf", encrypted_pdf_password_required_bytes, "application/pdf")},
    )
    doc_id = res.json()["document_ids"][0]

    status = _wait_for_status(client, doc_id)
    assert status["status"] == "error"
    # Plain-English, actionable message -- never a raw dependency/exception dump.
    assert "password" in status["error"].lower()
    assert "cryptography" not in status["error"].lower()
    assert "traceback" not in status["error"].lower()


def test_extract_pages_raises_indexing_error_for_password_required_pdf(tmp_path, encrypted_pdf_password_required_bytes):
    pdf_path = tmp_path / "locked.pdf"
    pdf_path.write_bytes(encrypted_pdf_password_required_bytes)
    try:
        indexing.extract_pages(pdf_path)
        assert False, "expected IndexingError"
    except indexing.IndexingError as exc:
        assert "password" in str(exc).lower()


def test_extract_pages_succeeds_for_owner_only_encrypted_pdf(tmp_path, encrypted_pdf_no_password_bytes):
    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_bytes(encrypted_pdf_no_password_bytes)
    pages = indexing.extract_pages(pdf_path)
    assert len(pages) >= 1
    full_text = " ".join(text for _, text in pages)
    assert "P0302" in full_text or "Misfire" in full_text


def test_ordinary_unencrypted_pdf_still_works(client, monkeypatch):
    """Existing (blank, unencrypted) PDF fixture behavior must be unchanged --
    still reports 'no extractable text' cleanly rather than erroring."""
    from pypdf import PdfWriter
    import io

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    blank_pdf_bytes = buf.getvalue()

    res = client.post("/api/devices", data={"name": "Ordinary PDF Test Device"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("blank.pdf", blank_pdf_bytes, "application/pdf")},
    )
    doc_id = res.json()["document_ids"][0]
    status = _wait_for_status(client, doc_id)
    assert status["status"] == "error"
    assert "no extractable text" in status["error"].lower()
