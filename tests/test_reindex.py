"""
Tests for the "embedding model changed" safety net:
- changing the embedding server/model marks previously-indexed manuals as
  needing reindexing
- a stale manual is excluded from search (never silently queried with a
  mismatched embedding)
- the Reindex Manuals action rebuilds the index from the saved file and
  makes the manual searchable again
- changing unrelated settings (e.g. the chat model) does NOT mark manuals
  stale
"""
import time

from app.services import llm_client
from tests.fakes import fake_chat_e24, fake_embed_texts


def _set_embedding_settings(client, base_url: str, model: str):
    res = client.post(
        "/api/settings",
        json={"embedding_base_url": base_url, "embedding_model": model},
    )
    assert res.status_code == 200
    return res.json()


def _wait_for_status(client, document_id, timeout=10):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        res = client.get(f"/api/documents/{document_id}/status")
        last = res.json()
        if last["status"] in ("ready", "error", "needs_reindex"):
            return last
        time.sleep(0.1)
    return last


def test_embedding_change_marks_document_needs_reindex(client, monkeypatch, sample_txt_bytes):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)

    _set_embedding_settings(client, "http://fake-server:11434/v1", "embed-model-a")

    res = client.post("/api/devices", data={"name": "Reindex Test Device A"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    status = _wait_for_status(client, doc_id)
    assert status["status"] == "ready"

    # Switch to a different embedding model -- the existing index is now stale.
    result = _set_embedding_settings(client, "http://fake-server:11434/v1", "embed-model-b")
    assert result["documents_marked_needs_reindex"] >= 1

    status = _wait_for_status(client, doc_id)
    assert status["status"] == "needs_reindex"


def test_stale_document_is_excluded_from_search(client, monkeypatch, sample_txt_bytes):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)

    _set_embedding_settings(client, "http://fake-server:11434/v1", "embed-model-a")

    res = client.post("/api/devices", data={"name": "Reindex Test Device B"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    _wait_for_status(client, doc_id)

    # Confirm it's answerable before the embedding model changes.
    monkeypatch.setattr(llm_client, "chat", fake_chat_e24)
    res = client.post(f"/api/devices/{device_id}/ask", json={"question": "What does error E24 mean?"})
    assert res.json()["not_found"] is False

    # Now change the embedding model -- the manual should no longer be searchable,
    # even though its old vectors are still sitting in Chroma.
    _set_embedding_settings(client, "http://fake-server:11434/v1", "embed-model-b")
    _wait_for_status(client, doc_id)

    res = client.post(f"/api/devices/{device_id}/ask", json={"question": "What does error E24 mean?"})
    data = res.json()
    assert data["not_found"] is True
    assert "upload a manual" in data["answer"].lower()


def test_reindex_action_restores_search(client, monkeypatch, sample_txt_bytes):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(llm_client, "chat", fake_chat_e24)

    _set_embedding_settings(client, "http://fake-server:11434/v1", "embed-model-a")

    res = client.post("/api/devices", data={"name": "Reindex Test Device C"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    _wait_for_status(client, doc_id)

    _set_embedding_settings(client, "http://fake-server:11434/v1", "embed-model-b")
    status = _wait_for_status(client, doc_id)
    assert status["status"] == "needs_reindex"

    res = client.post(f"/api/devices/{device_id}/reindex")
    assert res.status_code == 200
    assert doc_id in res.json()["document_ids"]

    # Indexing now runs through the persistent job queue (async), so the
    # document may legitimately still read 'needs_reindex' for a moment
    # after the endpoint returns. Poll for the final outcome only.
    deadline = time.time() + 15
    status = None
    while time.time() < deadline:
        status = client.get(f"/api/documents/{doc_id}/status").json()
        if status["status"] in ("ready", "error"):
            break
        time.sleep(0.1)
    assert status["status"] == "ready"
    assert status["chunk_count"] > 0

    res = client.post(f"/api/devices/{device_id}/ask", json={"question": "What does error E24 mean?"})
    data = res.json()
    assert data["not_found"] is False
    assert "drain pump" in data["answer"].lower()


def test_reindex_with_nothing_stale_is_a_no_op(client):
    res = client.post("/api/devices", data={"name": "Reindex Test Device D"})
    device_id = res.json()["id"]

    res = client.post(f"/api/devices/{device_id}/reindex")
    assert res.status_code == 200
    assert res.json()["document_ids"] == []


def test_unrelated_settings_change_does_not_mark_documents_stale(client, monkeypatch, sample_txt_bytes):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)

    _set_embedding_settings(client, "http://fake-server:11434/v1", "embed-model-stable")

    res = client.post("/api/devices", data={"name": "Reindex Test Device E"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    _wait_for_status(client, doc_id)

    # Changing only the chat model (not embeddings) must not touch the index.
    result = client.post("/api/settings", json={"llm_model": "some-other-chat-model"}).json()
    assert result["documents_marked_needs_reindex"] == 0

    status = _wait_for_status(client, doc_id)
    assert status["status"] == "ready"
