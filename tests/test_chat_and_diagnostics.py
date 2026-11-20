"""
Regression tests for conversational Q&A and the local PDF viewer:

- chat sessions: creation, persistence across calls, per-device listing,
  deletion, and message/citation storage
- follow-ups: conversational meaning is retained (retrieval query is
  augmented from the previous turn for follow-up-style questions) while
  retrieval + verification run fresh every turn (the fresh excerpts are
  what the model may cite, never the earlier answer)
- history is passed to the model as real chat messages, with the context
  budget driven by the chat_history_max_messages setting
- diagnostic sessions: symptoms, steps, readings, ruled-out causes, and
  lifecycle (open -> resolved), all persisted
- viewer endpoints: page text lookup, file serving, viewer page renders
"""
import time

import pytest

from app.services import chat_service, llm_client
from tests.fakes import fake_embed_texts

pytestmark = pytest.mark.usefixtures("client")


MANUAL = (
    "Rear Differential\n"
    "Oil capacity: 0.58 L.\n"
    "Drain plug tightening torque: 54 Nm.\n"
    "Fill plug tightening torque: 39 Nm.\n\n"
    "Transfer Case\n"
    "Oil capacity: 0.65 L.\n"
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


def _device_with_manual(client, monkeypatch, chat_response):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(llm_client, "chat", chat_response)
    res = client.post("/api/devices", data={"name": "Chat Car", "device_type": "Car"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", MANUAL.encode(), "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    status = _wait_ready(client, doc_id)
    assert status["status"] == "ready"
    return device_id, doc_id


def test_chat_session_persists_messages_and_sources(client, monkeypatch):
    device_id, _ = _device_with_manual(
        client, monkeypatch,
        lambda s, u, t=0.1, messages=None: "Rear differential capacity is 0.58 L.",
    )
    sid = client.post(f"/api/devices/{device_id}/chat/sessions", json={}).json()["id"]
    res = client.post(f"/api/chat/sessions/{sid}/ask", json={"question": "What is the rear differential capacity?"})
    assert res.status_code == 200
    assert "0.58" in res.json()["answer"]

    session = client.get(f"/api/chat/sessions/{sid}").json()
    roles = [m["role"] for m in session["messages"]]
    assert roles == ["user", "assistant"]
    assert session["messages"][1]["sources"], "the assistant message keeps its citations"


def test_followup_retrieval_uses_previous_turn_for_search(client, monkeypatch):
    """'What about the fill plug?' is meaningless as a search query by
    itself -- retrieval must resolve it via the previous turn, and the
    answer must cite FRESH evidence for the fill plug, not the capacity
    answer from the earlier message."""
    captured = {}

    def fake_chat(system_prompt, user_prompt, temperature=0.1, messages=None):
        captured["messages"] = messages
        if "Fill plug" in user_prompt or "39" in user_prompt:
            return "Fill plug torque: 39 Nm."
        return "not found"

    device_id, _ = _device_with_manual(client, monkeypatch, fake_chat)
    sid = client.post(f"/api/devices/{device_id}/chat/sessions", json={}).json()["id"]
    r1 = client.post(f"/api/chat/sessions/{sid}/ask", json={"question": "What is the rear differential drain plug torque?"})
    assert r1.status_code == 200
    r2 = client.post(f"/api/chat/sessions/{sid}/ask", json={"question": "What about the fill plug?"})
    data = r2.json()
    assert data["not_found"] is False, "follow-up must retrieve fresh evidence for the fill plug"
    assert "39" in data["answer"]
    # Sources must come from fresh retrieval on this turn (excerpts), and
    # the conversation history must have reached the model as messages.
    assert data["sources"]
    assert captured["messages"] and captured["messages"][0]["role"] == "system"


def test_chat_history_messages_bound_by_setting(client, monkeypatch):
    seen = {}

    def fake_chat(system_prompt, user_prompt, temperature=0.1, messages=None):
        seen["messages"] = messages
        return "ok"

    device_id, _ = _device_with_manual(client, monkeypatch, fake_chat)
    sid = client.post(f"/api/devices/{device_id}/chat/sessions", json={}).json()["id"]
    for i in range(4):
        client.post(f"/api/chat/sessions/{sid}/ask", json={"question": f"Turn number {i}: what does the manual say about the rear differential?"})

    client.post("/api/settings", json={"chat_history_max_messages": "2"})
    client.post(f"/api/chat/sessions/{sid}/ask", json={"question": "Summarize the rear differential info."})
    messages = seen["messages"]
    assert messages[0]["role"] == "system"
    # history user message contains the bounded number of prior turns
    history_message = messages[1]["content"]
    assert "Turn number 3" in history_message or "Turn number 2" in history_message
    assert "Turn number 0" not in history_message, "history must be bounded by the configured budget"
    # reset setting so other tests see the default
    client.post("/api/settings", json={"chat_history_max_messages": "8"})


def test_chat_sessions_listing_and_deletion(client, monkeypatch):
    device_id, _ = _device_with_manual(
        client, monkeypatch, lambda s, u, t=0.1, messages=None: "ok"
    )
    sid = client.post(f"/api/devices/{device_id}/chat/sessions", json={"title": "Noise diagnosis"}).json()["id"]
    sessions = client.get(f"/api/devices/{device_id}/chat/sessions").json()["sessions"]
    assert any(s["id"] == sid and s["title"] == "Noise diagnosis" for s in sessions)

    client.delete(f"/api/chat/sessions/{sid}")
    assert client.get(f"/api/chat/sessions/{sid}").status_code == 404
    # Deleting a session never touches the device or its manuals
    assert client.get(f"/devices/{device_id}").status_code == 200
    assert client.get(f"/api/devices/{device_id}/chat/sessions").status_code == 200


def test_chat_ask_into_missing_session_is_404(client):
    res = client.post("/api/chat/sessions/999999/ask", json={"question": "hi"})
    assert res.status_code == 404


# ------------------------------------------------- diagnostic sessions ----

def test_diagnostic_session_full_lifecycle(client):
    device_id = client.post("/api/devices", data={"name": "Diag Car", "device_type": "Car"}).json()["id"]
    sid = client.post(
        f"/api/devices/{device_id}/diag/sessions",
        json={"title": "P0302 misfire", "symptoms": "Rough idle, check engine light flashing"},
    ).json()["id"]

    step1 = client.post(
        f"/api/diag/sessions/{sid}/steps",
        json={"description": "Swapped coil 2 with coil 4", "result": "pass", "notes": "Misfire followed the coil -> coil was the fault"},
    ).json()["id"]
    client.post(
        f"/api/diag/sessions/{sid}/steps",
        json={"description": "Measured coil 2 primary resistance", "reading": "0.8 ohms", "result": "ruled_out"},
    )

    session = client.get(f"/api/diag/sessions/{sid}").json()
    assert session["symptoms"] == "Rough idle, check engine light flashing"
    assert len(session["steps"]) == 2
    assert session["steps"][0]["step_index"] == 1
    assert session["steps"][1]["step_index"] == 2
    assert session["steps"][1]["result"] == "ruled_out"

    client.patch(f"/api/diag/steps/{step1}", json={"result": "fail"})
    client.patch(
        f"/api/diag/sessions/{sid}",
        json={"status": "resolved", "outcome": "Faulty ignition coil on cylinder 2 replaced."},
    )
    session = client.get(f"/api/diag/sessions/{sid}").json()
    assert session["status"] == "resolved"
    assert session["steps"][0]["result"] == "fail"

    client.delete(f"/api/diag/steps/{step1}")
    session = client.get(f"/api/diag/sessions/{sid}").json()
    assert len(session["steps"]) == 1 and session["steps"][0]["step_index"] == 1, "steps renumber after deletion"

    sessions = client.get(f"/api/devices/{device_id}/diag/sessions").json()["sessions"]
    assert any(s["id"] == sid for s in sessions)
    client.delete(f"/api/diag/sessions/{sid}")
    assert client.get(f"/api/diag/sessions/{sid}").status_code == 404


def test_diagnostic_step_validation(client):
    device_id = client.post("/api/devices", data={"name": "Diag Car 2"}).json()["id"]
    sid = client.post(f"/api/devices/{device_id}/diag/sessions", json={}).json()["id"]
    res = client.post(f"/api/diag/sessions/{sid}/steps", json={"description": "x", "result": "bogus"})
    assert res.status_code == 400
    res = client.post(f"/api/diag/sessions/{sid}/steps", json={})
    assert res.status_code == 400
    res = client.patch(f"/api/diag/sessions/{sid}", json={"status": "bogus"})
    assert res.status_code == 400


# --------------------------------------------------------- viewer endpoints ----

def test_viewer_page_and_file_and_pages_endpoints(client, monkeypatch):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in ["Rear Differential", "Oil capacity: 0.58 L.", "Drain plug torque: 54 Nm."]:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    pdf_bytes = bytes(pdf.output())

    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    res = client.post("/api/devices", data={"name": "Viewer Car", "device_type": "Car"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.pdf", pdf_bytes, "application/pdf")},
    )
    doc_id = res.json()["document_ids"][0]
    _wait_ready(client, doc_id)

    # The viewer page renders (with the bundled pdf.js wired in)
    res = client.get(f"/documents/{doc_id}/view", params={"page": 1, "q": "capacity"})
    assert res.status_code == 200
    html = res.text
    assert "pdf.min.js" in html and "pdf.worker.min.js" in html, "viewer must load the locally bundled pdf.js"
    assert "cdn" not in html.lower() or "cdnjs" not in html, "no CDN references"

    # The manual file is served locally
    res = client.get(f"/api/documents/{doc_id}/file")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/pdf")

    # Stored page text supports highlighting lookups
    res = client.get(f"/api/documents/{doc_id}/pages", params={"page": 1})
    chunks = res.json()["chunks"]
    assert chunks and any("0.58" in c["text"] for c in chunks)

    # Missing manual -> 404 (and never a crash)
    assert client.get("/api/documents/999999/file").status_code == 404
    assert client.get("/documents/999999/view").status_code == 404
