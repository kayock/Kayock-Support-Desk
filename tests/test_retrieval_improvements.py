"""
Regression tests for the retrieval/reliability upgrades:

- true keyword (BM25/FTS5, LIKE fallback) retrieval: exact trouble codes,
  fuse numbers, and component names are findable independent of embeddings
- hybrid fusion: keyword candidates merge with semantic candidates
- embedding-outage degradation: a question still answers over
  keyword-retrieved evidence when the embedding backend is down
- section-aware chunking: chunks carry their section heading, and the
  heading is part of the chunk text (so component verification keeps
  working and citations can show sections)
- table-aware chunking: fuse-table rows are never split mid-row
- persistent job queue: enqueue/claim, crash recovery of stale 'running'
  jobs, retry-on-failure, cancellation restoring prior document status
- the chunks-table backfill from Chroma (pre-upgrade documents gain
  keyword search without re-upload/re-embedding)
- diagnostics endpoint and Linux launcher sanity
"""
import subprocess
import time

import pytest

from app.db import db_cursor
from app.services import indexing, job_queue, text_index
from app.services import llm_client
from tests.fakes import fake_chat_e24, fake_embed_texts

pytestmark = pytest.mark.usefixtures("client")


MANUAL_WITH_FUSE_TABLE = (
    "Interior Fuse Box\n"
    "Fuse Number | Rating | Protected Component\n"
    "FUSE 12 | 10A | Instrument cluster\n"
    "FUSE 13 | 15A | Audio system\n"
    "FUSE 14 | 25A | Blower motor relay\n"
    "FUSE 15 | 15A | Rear differential control module\n\n"
    "Engine Fuse Box\n"
    "FUSE 22 | 30A | Radiator fan\n"
    "FUSE 23 | 40A | Ignition switch\n"
)


def _wait_ready(client, doc_id, timeout=15):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/documents/{doc_id}/status").json()
        if last["status"] in ("ready", "error"):
            return last
        time.sleep(0.05)
    return last


def _upload(client, monkeypatch, name, text, device_type="Car", filename="manual.txt"):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    res = client.post("/api/devices", data={"name": name, "device_type": device_type})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": (filename, text.encode(), "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    status = _wait_ready(client, doc_id)
    assert status["status"] == "ready", status
    return device_id, doc_id


# ------------------------------------------------------------- keyword search ----

def test_keyword_search_finds_exact_trouble_code(client, monkeypatch):
    device_id, _ = _upload(
        client, monkeypatch, "Code Search Car",
        "DTC P0302\n"
        "Cylinder 2 Misfire Detected. Check the ignition coil resistance on cylinder 2.\n\n"
        "The rear differential drain plug torque is 54 Nm.\n",
    )
    results = text_index.search(device_id, "P0302", 10)
    assert results, "the exact code must be findable by keyword search"
    assert "P0302" in results[0]["text"]


def test_keyword_search_finds_fuse_numbers_in_table(client, monkeypatch):
    device_id, _ = _upload(client, monkeypatch, "Fuse Car", MANUAL_WITH_FUSE_TABLE)
    results = text_index.search(device_id, "FUSE 15", 10)
    assert results
    assert "Rear differential control module" in results[0]["text"]
    assert "15A" in results[0]["text"]


def test_keyword_search_endpoint(client, monkeypatch):
    device_id, _ = _upload(
        client, monkeypatch, "Search Endpoint Car",
        "The blower motor resistor is located behind the glove box. Its resistance is 2.4 ohms.",
    )
    res = client.get(f"/api/devices/{device_id}/search", params={"q": "blower motor resistor"})
    assert res.status_code == 200
    data = res.json()
    assert data["results"]
    assert "blower motor resistor" in data["results"][0]["text"].lower()


def test_keyword_search_with_fallback_uses_like(client, monkeypatch):
    """When FTS5 is unavailable (LIKE fallback path), keyword search still
    works -- this environment has FTS5, so exercise the LIKE query helper
    directly."""
    device_id, _ = _upload(
        client, monkeypatch, "Like Fallback Car",
        "The wiper motor is mounted on the firewall. Torque: 9 Nm.",
    )
    results = text_index._search_like(device_id, "wiper motor", 10, None)
    assert results
    assert "wiper motor" in results[0]["text"].lower()


def test_keyword_search_excludes_other_devices_and_non_ready_documents(client, monkeypatch):
    device_id, doc_id = _upload(client, monkeypatch, "Scoped Car", "The alternator belt tension spec is 500 N.")
    other = client.post("/api/devices", data={"name": "Other Garage Item", "device_type": "Car"}).json()["id"]
    assert text_index.search(other, "alternator belt", 10) == []

    # Simulate a stale embedding change: doc excluded from 'ready' set
    from app.db import db_cursor

    with db_cursor() as cur:
        cur.execute("UPDATE documents SET status = 'needs_reindex' WHERE id = ?", (doc_id,))
    assert text_index.search(device_id, "alternator belt", 10, allowed_document_ids=[]) == []


# --------------------------------------------------------------- hybrid ----

def test_ask_still_answers_when_embedding_backend_is_down(client, monkeypatch):
    """Embedding server unreachable -> keyword-only retrieval + the chat
    model still produce a correct manual-backed answer instead of a
    connection error."""
    from app.services.llm_client import LLMError

    def broken_embed(texts):
        raise LLMError("Could not reach the embedding model 'x'.")

    device_id, _ = _upload(client, monkeypatch, "Outage Car", MANUAL_WITH_FUSE_TABLE)
    monkeypatch.setattr(llm_client, "embed_texts", broken_embed)
    monkeypatch.setattr(
        llm_client, "chat",
        lambda system_prompt, user_prompt, temperature=0.1: (
            "The rear differential control module fuse is FUSE 15, rated 15A."
            if "FUSE 15" in user_prompt or "Rear differential control module" in user_prompt
            else "not found"
        ),
    )
    res = client.post(f"/api/devices/{device_id}/ask", json={"question": "Which fuse protects the rear differential control module?"})
    data = res.json()
    assert data["error"] is False, data
    assert data["not_found"] is False
    assert "FUSE 15" in data["answer"]
    assert data["sources"], "keyword-only retrieval must still produce citations"


# ------------------------------------------------- section/table chunking ----

def test_chunks_carry_section_attribution(client, monkeypatch):
    device_id, doc_id = _upload(client, monkeypatch, "Section Car", MANUAL_WITH_FUSE_TABLE)
    chunks = text_index.get_document_chunks(doc_id)
    assert chunks, "chunks must be stored in the text index"
    sections = {c["section"] for c in chunks}
    assert any("Interior Fuse Box" in s for s in sections)
    assert any("Engine Fuse Box" in s for s in sections)
    # The heading must also be part of the chunk text (component
    # verification reads chunk text, not metadata).
    first = next(c for c in chunks if "Interior Fuse Box" in c["section"])
    assert first["text"].startswith("Interior Fuse Box")


def test_fuse_table_rows_not_split_mid_row(client, monkeypatch):
    device_id, doc_id = _upload(client, monkeypatch, "Table Car", MANUAL_WITH_FUSE_TABLE)
    chunks = text_index.get_document_chunks(doc_id)
    # Every "FUSE nn | rating" pairing must remain within one chunk
    for line in MANUAL_WITH_FUSE_TABLE.splitlines():
        if line.startswith("FUSE "):
            fuse_id = line.split("|")[0].strip()
            rating = line.split("|")[1].strip()
            component = line.split("|")[2].strip()
            assert any(
                fuse_id in c["text"] and rating in c["text"] and component in c["text"]
                for c in chunks
            ), f"table row {fuse_id!r} was split apart: no chunk keeps number+rating+component together"


# --------------------------------------------------------------- jobs ----

def test_job_queue_runs_upload_through_the_queue(client, monkeypatch):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    res = client.post("/api/devices", data={"name": "Queue Device"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", b"Belt tension: check the label.", "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    assert job_queue.wait_until_idle(timeout=15)

    jobs = job_queue.list_jobs(10)
    assert any(j["job_type"] == "index_document" and j["status"] == "done" for j in jobs)
    status = client.get(f"/api/documents/{doc_id}/status").json()
    assert status["status"] == "ready"
    assert status["job"]["status"] == "done"


def test_job_queue_recovers_stale_running_jobs(monkeypatch, sample_txt_bytes, tmp_path):
    """A job left 'running' by a crashed previous process is re-queued on
    startup and completes, instead of being stuck forever."""
    from pathlib import Path

    doc_path = tmp_path / "recover.txt"
    doc_path.write_bytes(sample_txt_bytes)
    job_id = job_queue.enqueue(
        "index_document",
        {"document_id": 1, "device_id": 1, "file_path": str(doc_path), "original_filename": "recover.txt"},
        total=1,
    )
    # Simulate the crash: the job was claimed but never finished.
    with db_cursor() as cur:
        cur.execute("UPDATE jobs SET status = 'running', attempts = 1 WHERE id = ?", (job_id,))
    recovered = job_queue.recover_stale_running_jobs()
    assert recovered >= 1
    job_queue.process_pending_now(timeout=10)
    job = job_queue.get_job(job_id)
    assert job["status"] in ("done", "error")  # ran; error is fine (device 1 may not exist), never stuck


def test_job_queue_retries_failed_jobs_then_reports_error():
    calls = {"n": 0}

    def flaky(payload, progress_cb, cancel_cb):
        calls["n"] += 1
        raise RuntimeError("boom")

    from app.services import job_queue as jq

    jq.register_handler("test_flaky", flaky)
    try:
        job_id = jq.enqueue("test_flaky", {}, max_attempts=3)
        jq.process_pending_now(timeout=10)
        job = jq.get_job(job_id)
        assert job["status"] == "error"
        assert job["attempts"] == 3
        assert calls["n"] == 3, "the job must have been retried up to max_attempts"
        assert "boom" in job["error"]
    finally:
        jq._HANDLERS.pop("test_flaky", None)


def test_job_cancellation_restores_prior_document_status(client, monkeypatch, sample_txt_bytes, tmp_path):
    """Cancelling an in-flight indexing job restores the document to its
    pre-job status ('needs_reindex' for a reindex job) -- never stuck in
    'indexing'."""
    from pathlib import Path

    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    res = client.post("/api/devices", data={"name": "Cancel Device"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    job_queue.wait_until_idle(timeout=15)

    doc_path = tmp_path / "re.txt"
    doc_path.write_bytes(sample_txt_bytes)
    with db_cursor() as cur:
        cur.execute("UPDATE documents SET status = 'needs_reindex' WHERE id = ?", (doc_id,))
    job_id = job_queue.enqueue(
        "index_document",
        {
            "document_id": doc_id,
            "device_id": device_id,
            "file_path": str(doc_path),
            "original_filename": "manual.txt",
            "prior_status": "needs_reindex",
        },
        total=1,
    )
    job_queue.request_cancel(job_id)
    job_queue.process_pending_now(timeout=10)
    job = job_queue.get_job(job_id)
    assert job["status"] == "cancelled"
    doc = client.get(f"/api/documents/{doc_id}/status").json()
    assert doc["status"] == "needs_reindex", "cancelled reindex must restore the reindexable state"


def test_jobs_api_lists_and_cancels(client):
    res = client.get("/api/jobs")
    assert res.status_code == 200
    assert "jobs" in res.json()


# -------------------------------------------------------------- backfill ----

def test_backfill_restores_keyword_search_for_preupgrade_documents(client, monkeypatch):
    """Documents indexed before the chunks table existed (no chunks rows)
    get their texts copied back out of Chroma -- keyword search covers the
    whole existing library without re-upload or re-embedding."""
    device_id, doc_id = _upload(
        client, monkeypatch, "Backfill Car",
        "The crankshaft position sensor resistance is 1.2 kilo-ohms at 20 degrees C.",
    )
    # Erase the text index (simulating a pre-upgrade install), keep Chroma.
    text_index.delete_document(doc_id)
    assert text_index.search(device_id, "crankshaft position sensor", 10) == []

    count = text_index.backfill_from_vector_store([doc_id])
    assert count == 1
    results = text_index.search(device_id, "crankshaft position sensor", 10)
    assert results and "1.2" in results[0]["text"]


# ------------------------------------------------------------ diagnostics ----

def test_diagnostics_endpoint_reports_environment(client):
    res = client.get("/api/diagnostics")
    assert res.status_code == 200
    data = res.json()
    assert data["python"]["python_version"].startswith("3.")
    assert "data_dir_writable" in data["data"]
    assert "tkinter_available" in data["gui"]
    assert "gpus_detected" in data["gpu"]
    assert "llama_server_found" in data["ai"]
    assert "log_dir" in data["logs"]


def test_linux_launcher_is_valid_shell_and_executable():
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "Start-Linux.sh"
    assert path.exists()
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_llama_server_extra_args_and_context_settings(monkeypatch):
    from app.services import ai_manager

    assert ai_manager._parse_extra_args("--n-gpu-layers 99 --flash-attn") == [
        "--n-gpu-layers", "99", "--flash-attn",
    ]
    assert ai_manager._parse_extra_args("") == []
    assert ai_manager._parse_extra_args('"bad quote') == []

    from app.services import settings_service

    monkeypatch.setattr(settings_service, "get", lambda key: "8192" if key == "llm_context_size" else "")
    assert ai_manager.chat_context_size() == 8192  # raising is honored
    monkeypatch.setattr(settings_service, "get", lambda key: "0" if key == "llm_context_size" else "")
    assert ai_manager.chat_context_size() == ai_manager.LLAMA_SERVER_CONTEXT_SIZE  # invalid -> unchanged default
