import time

from app.services import llm_client
from tests.fakes import fake_chat_e24, fake_detect_chat, fake_embed_texts


def _wait_for_status(client, document_id, timeout=10):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        res = client.get(f"/api/documents/{document_id}/status")
        last = res.json()
        if last["status"] in ("ready", "error"):
            return last
        time.sleep(0.1)
    return last


def test_upload_and_index_txt_manual(client, monkeypatch, sample_txt_bytes):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)

    res = client.post("/api/devices", data={"name": "Kitchen Dishwasher"})
    device_id = res.json()["id"]

    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    assert res.status_code == 200
    doc_id = res.json()["document_ids"][0]

    status = _wait_for_status(client, doc_id)
    assert status["status"] == "ready"
    assert status["chunk_count"] > 0


def test_ask_question_answers_from_manual(client, monkeypatch, sample_txt_bytes):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(llm_client, "chat", fake_chat_e24)

    res = client.post("/api/devices", data={"name": "Kitchen Dishwasher 2"})
    device_id = res.json()["id"]

    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    status = _wait_for_status(client, doc_id)
    assert status["status"] == "ready"

    res = client.post(f"/api/devices/{device_id}/ask", json={"question": "What does error E24 mean?"})
    assert res.status_code == 200
    data = res.json()
    assert data["not_found"] is False
    assert "drain pump" in data["answer"].lower()
    assert len(data["sources"]) >= 1
    assert data["sources"][0]["filename"] == "manual.txt"


def test_ask_question_with_no_manuals_says_so(client):
    res = client.post("/api/devices", data={"name": "Bare Device"})
    device_id = res.json()["id"]

    res = client.post(f"/api/devices/{device_id}/ask", json={"question": "How do I reset it?"})
    assert res.status_code == 200
    data = res.json()
    assert data["not_found"] is True


def test_detect_device_info_from_upload(client, monkeypatch, sample_txt_bytes):
    monkeypatch.setattr(llm_client, "chat", fake_detect_chat)

    res = client.post("/api/detect", files={"file": ("manual.txt", sample_txt_bytes, "text/plain")})
    assert res.status_code == 200
    data = res.json()
    assert data["manufacturer"] == "Bosch"
    assert data["model_number"] == "SHPM88Z75N"


def test_remove_document(client, monkeypatch, sample_txt_bytes):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)

    res = client.post("/api/devices", data={"name": "Device To Trim"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    _wait_for_status(client, doc_id)

    res = client.delete(f"/api/documents/{doc_id}")
    assert res.status_code == 200

    res = client.get(f"/api/documents/{doc_id}/status")
    assert res.status_code == 404
