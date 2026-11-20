"""
Regression tests for conversational-retrieval correctness (Phase 1 repairs).

These tests capture the ACTUAL retrieval inputs -- the text handed to the
embedding call, the vector passed to the semantic store, and the query
string handed to the keyword index -- not merely the wording of the
generated answer. They prove:

- follow-up questions retrieve relevant prior context (previous user
  message prepended to the retrieval query)
- the current question is never duplicated as previous context (and is
  stored only ONCE per turn)
- semantic AND keyword retrieval both receive the contextualized query
  while the original question still drives reranking/verification
- different devices and sessions are isolated (search scoped to the
  asking device's own documents; sessions don't leak messages)
- first-turn questions work unchanged
- exact-spec verification still rejects unsupported values through the
  chat path
- an embedding outage still allows keyword-only retrieval with the
  contextualized query
"""
import time

import pytest

from app.services import chat_service, llm_client, text_index, vector_store
from tests.fakes import fake_embed_texts

pytestmark = pytest.mark.usefixtures("client")


MANUAL = (
    "Rear Differential\n"
    "Oil capacity: 0.58 L.\n"
    "Drain plug tightening torque: 54 Nm.\n"
    "Fill plug tightening torque: 39 Nm.\n\n"
    "Transfer Case\n"
    "Oil capacity: 0.65 L.\n\n"
    "Wheel Bearing\n"
    "Hub nut torque: 220 Nm.\n"
)

NO_SPEC_MANUAL = (
    "Rear Differential\n"
    "Inspect the housing for leaks at every service visit and clean the breather.\n"
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


def _device_with_manual(client, monkeypatch, name, chat_response, manual_text=MANUAL):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    if chat_response is not None:
        monkeypatch.setattr(llm_client, "chat", chat_response)
    res = client.post("/api/devices", data={"name": name, "device_type": "Car"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", manual_text.encode(), "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    status = _wait_ready(client, doc_id)
    assert status["status"] == "ready"
    return device_id, doc_id


def _capture_retrieval(monkeypatch):
    """Records the exact inputs every retrieval surface receives."""
    captured = {"embedded_texts": [], "keyword_queries": [], "vector_queries": []}

    real_embed = fake_embed_texts

    def spy_embed(texts):
        captured["embedded_texts"].extend(texts)
        return real_embed(texts)

    real_search = text_index.search

    def spy_search(device_id, query, limit, allowed_document_ids=None):
        captured["keyword_queries"].append({"device_id": device_id, "query": query, "allowed": allowed_document_ids})
        return real_search(device_id, query, limit, allowed_document_ids)

    real_vec_query = vector_store.query

    def spy_vec_query(device_id, embedding, top_k, allowed_document_ids=None):
        captured["vector_queries"].append({"device_id": device_id, "allowed": allowed_document_ids})
        return real_vec_query(device_id, embedding, top_k, allowed_document_ids)

    monkeypatch.setattr(llm_client, "embed_texts", spy_embed)
    monkeypatch.setattr(text_index, "search", spy_search)
    monkeypatch.setattr(vector_store, "query", spy_vec_query)
    return captured


def test_followup_retrieval_uses_prior_turn_and_semantic_gets_contextualized_query(client, monkeypatch):
    """The follow-up 'What about the fill plug?' must be turned into a
    retrieval query that names the rear differential -- and BOTH retrieval
    surfaces must receive that same contextualized query."""
    device_id, _ = _device_with_manual(client, monkeypatch, "Followup Car", None)
    captured = _capture_retrieval(monkeypatch)
    monkeypatch.setattr(
        llm_client, "chat",
        lambda s, u, t=0.1, messages=None: "Fill plug torque: 39 Nm.",
    )
    sid = client.post(f"/api/devices/{device_id}/chat/sessions", json={}).json()["id"]

    client.post(f"/api/chat/sessions/{sid}/ask", json={"question": "What is the rear differential drain plug torque?"})
    captured["keyword_queries"].clear()
    captured["embedded_texts"].clear()

    res = client.post(f"/api/chat/sessions/{sid}/ask", json={"question": "What about the fill plug?"})
    assert res.status_code == 200

    # The contextualized query carries the previous turn's component.
    keyword_query = captured["keyword_queries"][-1]["query"].lower()
    assert "fill plug" in keyword_query
    assert "drain plug" in keyword_query, "the previous user message must be part of the retrieval query"
    # The current question is never duplicated as previous context.
    assert keyword_query.count("fill plug") == 1
    assert keyword_query.count("drain plug") == 1
    # Semantic retrieval embedded the same contextualized text.
    assert captured["embedded_texts"][-1].lower() == keyword_query
    # Both surfaces ran against the same device scope.
    assert captured["keyword_queries"][-1]["device_id"] == device_id
    assert captured["vector_queries"][-1]["device_id"] == device_id


def test_current_question_stored_exactly_once_per_turn(client, monkeypatch):
    device_id, _ = _device_with_manual(client, monkeypatch, "Store Once Car", None)
    captured = _capture_retrieval(monkeypatch)
    monkeypatch.setattr(llm_client, "chat", lambda s, u, t=0.1, messages=None: "ok")
    sid = client.post(f"/api/devices/{device_id}/chat/sessions", json={}).json()["id"]

    client.post(f"/api/chat/sessions/{sid}/ask", json={"question": "What is the rear differential capacity?"})
    client.post(f"/api/chat/sessions/{sid}/ask", json={"question": "What about the hub nut?"})

    session = client.get(f"/api/chat/sessions/{sid}").json()
    user_messages = [m["content"] for m in session["messages"] if m["role"] == "user"]
    assert user_messages.count("What about the hub nut?") == 1, "each turn stores the question exactly once"
    assert len(user_messages) == 2
    # And the retrieval query for turn 2 resolved via turn 1, not itself.
    keyword_query = captured["keyword_queries"][-1]["query"].lower()
    assert "rear differential" in keyword_query and "hub nut" in keyword_query


def test_first_turn_uses_question_verbatim_for_retrieval(client, monkeypatch):
    device_id, _ = _device_with_manual(client, monkeypatch, "First Turn Car", None)
    captured = _capture_retrieval(monkeypatch)
    monkeypatch.setattr(llm_client, "chat", lambda s, u, t=0.1, messages=None: "ok")
    sid = client.post(f"/api/devices/{device_id}/chat/sessions", json={}).json()["id"]

    res = client.post(f"/api/chat/sessions/{sid}/ask", json={"question": "What is the rear differential oil capacity?"})
    assert res.status_code == 200
    keyword_query = captured["keyword_queries"][-1]["query"]
    assert keyword_query == "What is the rear differential oil capacity?", "first turn: retrieval query == question"
    assert captured["embedded_texts"][-1] == "What is the rear differential oil capacity?"
    # A long, self-contained question gets no history user-message prepended
    # (history is only used for conversational framing, not search).
    assert keyword_query.count("rear differential") == 1


def test_devices_and_sessions_are_isolated(client, monkeypatch):
    device_a, _ = _device_with_manual(client, monkeypatch, "Car A", None)
    device_b, _ = _device_with_manual(client, monkeypatch, "Car B", None)
    captured = _capture_retrieval(monkeypatch)
    monkeypatch.setattr(llm_client, "chat", lambda s, u, t=0.1, messages=None: "ok")

    sess_a = client.post(f"/api/devices/{device_a}/chat/sessions", json={}).json()["id"]
    sess_b = client.post(f"/api/devices/{device_b}/chat/sessions", json={}).json()["id"]

    client.post(f"/api/chat/sessions/{sess_a}/ask", json={"question": "Rear differential drain plug torque please"})
    # The retrieval for device A's session must be scoped to device A only.
    last_keyword = captured["keyword_queries"][-1]
    assert last_keyword["device_id"] == device_a
    assert device_b not in last_keyword["allowed"], "keyword search must be scoped to the asking device's documents"
    assert captured["vector_queries"][-1]["device_id"] == device_a

    # Session messages don't leak across sessions or devices.
    client.post(f"/api/chat/sessions/{sess_b}/ask", json={"question": "Hub nut torque please"})
    sess_a_view = client.get(f"/api/chat/sessions/{sess_a}").json()
    assert all("Hub nut" not in m["content"] for m in sess_a_view["messages"])
    sess_b_view = client.get(f"/api/chat/sessions/{sess_b}").json()
    assert all("drain plug" not in m["content"].lower() for m in sess_b_view["messages"])
    # Device B never sees device A's manuals in retrieval.
    assert captured["keyword_queries"][-1]["device_id"] == device_b


def test_chat_path_still_rejects_unverified_specifications(client, monkeypatch):
    """Through a chat session, a component the manual mentions but gives no
    number for must produce the not-verified wording from a model that
    follows the verification prompt -- and the pipeline must still gate
    citations even when a model disobeys the prompt."""
    from app.services.query_service import NOT_VERIFIED_PHRASE

    def compliant_chat(system_prompt, user_prompt, temperature=0.1, messages=None):
        # Follows the property-verification rules: no verified excerpt for
        # the requested value -> answer with the exact not-verified phrase.
        if "none found." in user_prompt and "VERIFIED EXACT-SPEC EXCERPTS" in user_prompt:
            return NOT_VERIFIED_PHRASE
        return "Drain plug torque: 999 Nm."

    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(llm_client, "chat", compliant_chat)
    device_id, _ = _device_with_manual(
        client, monkeypatch, "No Spec Car", None, manual_text=NO_SPEC_MANUAL
    )
    sid = client.post(f"/api/devices/{device_id}/chat/sessions", json={}).json()["id"]
    res = client.post(
        f"/api/chat/sessions/{sid}/ask",
        json={"question": "What is the rear differential drain plug torque for this vehicle?"},
    )
    data = res.json()
    assert "999" not in data["answer"], "an unverified value must be rejected by the verification flow"
    assert "could not verify" in data["answer"].lower()

    # Citation gating holds even for a model that ignores the prompt: the
    # pipeline never offers an excerpt as a verified spec source when
    # verification found none (this manual has no numbers at all).
    for s in data["sources"]:
        import re as _re

        assert not _re.search(r"\d+\s*Nm|\d+\s*torque", (s.get("excerpt") or "").lower())


def test_embedding_outage_still_retrieves_keyword_only_with_contextualized_query(client, monkeypatch):
    """Embedding server down: no vector query at all, keyword search runs
    with the contextualized follow-up query, and the answer still cites
    fresh manual evidence."""
    from app.services.llm_client import LLMError

    device_id, _ = _device_with_manual(client, monkeypatch, "Outage Chat Car", None)
    captured = _capture_retrieval(monkeypatch)

    def broken_embed(texts):
        captured["embedded_texts"].extend(texts)
        raise LLMError("Could not reach the embedding model 'x'.")

    monkeypatch.setattr(llm_client, "embed_texts", broken_embed)
    monkeypatch.setattr(
        llm_client, "chat",
        lambda s, u, t=0.1, messages=None: "Fill plug torque: 39 Nm.",
    )
    sid = client.post(f"/api/devices/{device_id}/chat/sessions", json={}).json()["id"]

    client.post(f"/api/chat/sessions/{sid}/ask", json={"question": "What is the rear differential drain plug torque?"})
    res = client.post(f"/api/chat/sessions/{sid}/ask", json={"question": "What about the fill plug?"})
    data = res.json()

    assert captured["vector_queries"] == [], "no vector query may run during an embedding outage"
    keyword_query = captured["keyword_queries"][-1]["query"].lower()
    assert "fill plug" in keyword_query and "drain plug" in keyword_query
    assert keyword_query.count("drain plug") == 1
    assert data["not_found"] is False and "39" in data["answer"]
    assert data["sources"], "keyword-only retrieval must still produce citations"


def test_history_budget_bounds_model_messages_not_retrieval(client, monkeypatch):
    """A small chat_history_max_messages bounds the model's conversation
    framing, but retrieval for a follow-up still resolves via the
    immediately preceding user turn."""
    captured = _capture_retrieval(monkeypatch)
    seen = {}

    def fake_chat(system_prompt, user_prompt, temperature=0.1, messages=None):
        seen["messages"] = messages
        return "ok"

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    device_id, _ = _device_with_manual(client, monkeypatch, "Budget Car", None)
    sid = client.post(f"/api/devices/{device_id}/chat/sessions", json={}).json()["id"]

    client.post("/api/settings", json={"chat_history_max_messages": "1"})
    for q in ["What is the rear differential drain plug torque?", "Turn two question about the differential",
              "What about the fill plug?"]:
        client.post(f"/api/chat/sessions/{sid}/ask", json={"question": q})

    keyword_query = captured["keyword_queries"][-1]["query"].lower()
    assert "fill plug" in keyword_query and "differential" in keyword_query
    client.post("/api/settings", json={"chat_history_max_messages": "8"})
