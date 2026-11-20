"""
Per-device chat sessions with persisted conversation history.

Follow-ups keep conversational meaning ("what about the fill plug?")
while retrieval, reranking, and spec verification run fresh on every
turn -- the model is never allowed to rely on stale evidence, it only
gets context for resolving the language of the follow-up. Every user and
assistant message is stored (with the citations that backed the answer),
so a session survives restarts exactly like everything else in SQLite.

Context budget is configurable, not silently restrictive:
- chat_history_max_messages (Settings) controls how many prior messages
  are sent to the model (default 8; raise it for longer memory).
- the managed llama-server context window is set by llm_context_size
  (default unchanged at 4096; raising it is safe on bigger machines).
"""
from __future__ import annotations

import json

from app.db import db_cursor
from app.services import query_service, settings_service


# ------------------------------------------------------------- sessions ----

def create_session(device_id: int, title: str = "") -> int:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO chat_sessions (device_id, title) VALUES (?, ?)",
            (device_id, (title or "New conversation").strip()[:120] or "New conversation"),
        )
        return cur.lastrowid


def list_sessions(device_id: int) -> list[dict]:
    with db_cursor() as cur:
        cur.execute(
            """SELECT s.*, COUNT(m.id) AS message_count
               FROM chat_sessions s LEFT JOIN chat_messages m ON m.session_id = s.id
               WHERE s.device_id = ?
               GROUP BY s.id ORDER BY s.updated_at DESC, s.id DESC""",
            (device_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_session(session_id: int) -> dict | None:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        if row is None:
            return None
        session = dict(row)
        cur.execute(
            "SELECT id, role, content, sources_json, created_at FROM chat_messages "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        )
        messages = []
        for m in cur.fetchall():
            msg = dict(m)
            try:
                msg["sources"] = json.loads(msg.pop("sources_json") or "[]")
            except json.JSONDecodeError:
                msg["sources"] = []
            messages.append(msg)
        session["messages"] = messages
        return session


def delete_session(session_id: int) -> None:
    with db_cursor() as cur:
        cur.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))


def rename_session(session_id: int, title: str) -> None:
    _touch_session(session_id)
    with db_cursor() as cur:
        cur.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = datetime('now') WHERE id = ?",
            ((title or "").strip()[:120] or "New conversation", session_id),
        )


def _touch_session(session_id: int) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?", (session_id,)
        )


def _add_message(session_id: int, role: str, content: str, sources: list[dict] | None = None) -> None:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO chat_messages (session_id, role, content, sources_json) VALUES (?, ?, ?, ?)",
            (session_id, role, content, json.dumps(sources or [])),
        )
    _touch_session(session_id)


# ------------------------------------------------------------- asking ----

_FOLLOWUP_MARKERS = (
    " it ", " that ", " this ", " there ", " they ", " them ", " those ", " also ",
    "what about", "how about", "and the", "same for", "as well", "too",
)


def _needs_previous_context(question: str) -> bool:
    """Whether a question is a follow-up whose wording depends on the
    previous turn (short questions, pronouns, 'what about ...'). The
    retrieval query for such questions is augmented with the previous
    user message so the *search* still targets the right component --
    but retrieval and verification themselves stay fresh every turn."""
    q = " " + (question or "").lower().strip() + " "
    significant = [w for w in q.split() if w.isalnum()]
    if len(significant) <= 4:
        return True
    return any(marker in q for marker in _FOLLOWUP_MARKERS)


def _history_limit() -> int:
    raw = settings_service.get("chat_history_max_messages") or "8"
    try:
        value = int(raw)
    except ValueError:
        return 8
    return max(0, min(value, 100))


def _build_retrieval_query(question: str, prior_messages: list[dict]) -> str:
    """Fresh retrieval query for this turn, built from the PRE-INSERT
    snapshot of the session (never from the database after the current
    question was stored -- that would make the 'previous turn' the current
    question itself). For follow-up-style questions, the previous user
    message is prepended so keyword/semantic search targets the right
    component even when the question says only 'it'. The current question
    is never duplicated: it appears exactly once, at the end."""
    if not _needs_previous_context(question):
        return question
    previous = next(
        ((m.get("content") or "").strip() for m in reversed(prior_messages)
         if m.get("role") == "user"),
        "",
    )
    if not previous or previous.lower() == question.lower():
        return question
    return f"{previous} {question}".strip()[:1000]


def _history_block(messages: list[dict]) -> str:
    """A compact, clearly-labeled transcript of earlier turns, included
    only so the model can resolve conversational language. It is NOT
    evidence: the fresh excerpts below remain the only citable sources."""
    if not messages:
        return ""
    lines = []
    for m in messages:
        who = "User" if m["role"] == "user" else "Assistant"
        content = " ".join((m["content"] or "").split())[:600]
        lines.append(f"{who}: {content}")
    return (
        "[Earlier conversation -- context for wording only. Do not reuse any values, "
        "specifications, or citations from it; rely solely on the fresh excerpts below.]\n"
        + "\n".join(lines)
    )


def ask_in_session(device: dict, session_id: int, question: str) -> dict:
    """Asks a question inside a chat session: retrieves fresh evidence
    (with follow-up wording resolved via the PREVIOUS turn, computed from
    the pre-insert snapshot so the new question is never treated as
    history), answers with conversation history for meaning only, and
    then persists the user's message and the assistant's answer with its
    citations."""
    # Snapshot the session BEFORE writing anything: both the conversation
    # history sent to the model and the retrieval query must be built from
    # prior messages only.
    session = get_session(session_id)
    if session is None:
        raise ValueError("Session not found")

    prior = session["messages"]
    # Retrieval resolution always sees the recent conversation (a tiny
    # model-history budget must not silently break "what about the fill
    # plug?" follow-ups); the budget bounds only the model's framing.
    retrieval_query = _build_retrieval_query(question, prior)
    history_for_model = prior[-_history_limit():] if _history_limit() > 0 else []

    # Only now record the user's turn.
    _add_message(session_id, "user", question)

    result = query_service.answer_question(device, question, retrieval_query=retrieval_query, history=history_for_model)

    _add_message(session_id, "assistant", result["answer"], result["sources"])
    return result
