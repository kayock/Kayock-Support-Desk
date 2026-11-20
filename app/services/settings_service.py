"""
Per-installation settings (LLM endpoint, model names, API key) stored as
simple key/value rows in SQLite. Seeded with local-first defaults
(Ollama's OpenAI-compatible API) the first time the app starts.
"""
from app.config import DEFAULT_SETTINGS
from app.db import db_cursor


def ensure_defaults() -> None:
    with db_cursor() as cur:
        cur.execute("SELECT key FROM settings")
        existing = {row["key"] for row in cur.fetchall()}
        for key, value in DEFAULT_SETTINGS.items():
            if key not in existing:
                cur.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)", (key, value)
                )


def get_all() -> dict:
    with db_cursor() as cur:
        cur.execute("SELECT key, value FROM settings")
        rows = {row["key"]: row["value"] for row in cur.fetchall()}
    # Fill in any missing keys with defaults (e.g. after an upgrade adds a new setting)
    for key, value in DEFAULT_SETTINGS.items():
        rows.setdefault(key, value)
    return rows


def get(key: str) -> str:
    with db_cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
    if row is not None:
        return row["value"]
    return DEFAULT_SETTINGS.get(key, "")


def update(values: dict) -> None:
    with db_cursor() as cur:
        for key, value in values.items():
            if key not in DEFAULT_SETTINGS:
                continue
            cur.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value or ""),
            )


def embedding_signature() -> str:
    """A stable string identifying 'which embedding model produced the
    current index'. Anything that could change the resulting vectors
    (server + model) is included, so a change to either is detected."""
    s = get_all()
    return f"{s['embedding_base_url'].strip()}::{s['embedding_model'].strip()}"
