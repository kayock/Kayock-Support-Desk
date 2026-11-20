"""
Structured diagnostic session tracking: what symptom started the session,
which steps were tested, what was measured, and which causes got ruled
out. Plain structured data kept in SQLite -- deliberately independent of
the LLM, so a technician's record of a diagnosis stays factual, editable,
and persistent no matter what any model said. Sessions can reference
manual pages in step notes, and the UI can show them next to chat/Q&A.
"""
from __future__ import annotations

from app.db import db_cursor

STEP_RESULTS = ("", "pass", "fail", "ruled_out")
SESSION_STATUSES = ("open", "resolved", "parked")


def create_session(device_id: int, title: str = "", symptoms: str = "") -> int:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO diagnostic_sessions (device_id, title, symptoms) VALUES (?, ?, ?)",
            (device_id, (title or "").strip()[:160], (symptoms or "").strip()),
        )
        return cur.lastrowid


def list_sessions(device_id: int) -> list[dict]:
    with db_cursor() as cur:
        cur.execute(
            """SELECT s.*, COUNT(st.id) AS step_count,
                      SUM(CASE WHEN st.result = 'ruled_out' THEN 1 ELSE 0 END) AS ruled_out_count
               FROM diagnostic_sessions s
               LEFT JOIN diagnostic_steps st ON st.session_id = s.id
               WHERE s.device_id = ?
               GROUP BY s.id ORDER BY s.updated_at DESC, s.id DESC""",
            (device_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_session(session_id: int) -> dict | None:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM diagnostic_sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        if row is None:
            return None
        session = dict(row)
        cur.execute(
            "SELECT * FROM diagnostic_steps WHERE session_id = ? ORDER BY step_index, id",
            (session_id,),
        )
        session["steps"] = [dict(r) for r in cur.fetchall()]
        return session


def update_session(session_id: int, title: str | None = None, symptoms: str | None = None,
                   status: str | None = None, outcome: str | None = None) -> None:
    fields, values = [], []
    if title is not None:
        fields.append("title = ?")
        values.append(title.strip()[:160])
    if symptoms is not None:
        fields.append("symptoms = ?")
        values.append(symptoms.strip())
    if status is not None:
        if status not in SESSION_STATUSES:
            raise ValueError(f"status must be one of {SESSION_STATUSES}")
        fields.append("status = ?")
        values.append(status)
    if outcome is not None:
        fields.append("outcome = ?")
        values.append(outcome.strip())
    if not fields:
        return
    fields.append("updated_at = datetime('now')")
    values.append(session_id)
    with db_cursor() as cur:
        cur.execute(f"UPDATE diagnostic_sessions SET {', '.join(fields)} WHERE id = ?", values)


def delete_session(session_id: int) -> None:
    with db_cursor() as cur:
        cur.execute("DELETE FROM diagnostic_sessions WHERE id = ?", (session_id,))


def add_step(session_id: int, description: str, reading: str = "", result: str = "", notes: str = "") -> int:
    if result not in STEP_RESULTS:
        raise ValueError(f"result must be one of {STEP_RESULTS}")
    with db_cursor() as cur:
        cur.execute(
            "SELECT COALESCE(MAX(step_index), 0) AS n FROM diagnostic_steps WHERE session_id = ?",
            (session_id,),
        )
        next_index = cur.fetchone()["n"] + 1
        cur.execute(
            """INSERT INTO diagnostic_steps (session_id, step_index, description, reading, result, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, next_index, description.strip(), (reading or "").strip(), result, (notes or "").strip()),
        )
        step_id = cur.lastrowid
    _touch(session_id)
    return step_id


def update_step(step_id: int, description: str | None = None, reading: str | None = None,
                result: str | None = None, notes: str | None = None) -> None:
    fields, values = [], []
    if description is not None:
        fields.append("description = ?")
        values.append(description.strip())
    if reading is not None:
        fields.append("reading = ?")
        values.append(reading.strip())
    if result is not None:
        if result not in STEP_RESULTS:
            raise ValueError(f"result must be one of {STEP_RESULTS}")
        fields.append("result = ?")
        values.append(result)
    if notes is not None:
        fields.append("notes = ?")
        values.append(notes.strip())
    if not fields:
        return
    values.append(step_id)
    with db_cursor() as cur:
        cur.execute(
            f"UPDATE diagnostic_steps SET {', '.join(fields)} WHERE id = ?", values
        )
        cur.execute(
            """UPDATE diagnostic_sessions SET updated_at = datetime('now')
               WHERE id = (SELECT session_id FROM diagnostic_steps WHERE id = ?)""",
            (step_id,),
        )


def delete_step(step_id: int) -> None:
    # Single transaction: read, delete, renumber, touch -- nested cursors
    # would deadlock on SQLite's write lock.
    with db_cursor() as cur:
        cur.execute("SELECT session_id FROM diagnostic_steps WHERE id = ?", (step_id,))
        row = cur.fetchone()
        if row is None:
            return
        session_id = row["session_id"]
        cur.execute("DELETE FROM diagnostic_steps WHERE id = ?", (step_id,))
        cur.execute(
            "SELECT id FROM diagnostic_steps WHERE session_id = ? ORDER BY step_index, id",
            (session_id,),
        )
        for i, r in enumerate(cur.fetchall(), start=1):
            cur.execute("UPDATE diagnostic_steps SET step_index = ? WHERE id = ?", (i, r["id"]))
        cur.execute(
            "UPDATE diagnostic_sessions SET updated_at = datetime('now') WHERE id = ?",
            (session_id,),
        )


def _renumber(session_id: int) -> None:
    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM diagnostic_steps WHERE session_id = ? ORDER BY step_index, id",
            (session_id,),
        )
        ids = [r["id"] for r in cur.fetchall()]
        for i, step_id in enumerate(ids, start=1):
            cur.execute("UPDATE diagnostic_steps SET step_index = ? WHERE id = ?", (i, step_id))


def _touch(session_id: int) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE diagnostic_sessions SET updated_at = datetime('now') WHERE id = ?", (session_id,)
        )
