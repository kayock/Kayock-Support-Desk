"""
Tiny SQLite wrapper. No ORM -- the schema is small enough that plain SQL
is easier to read, debug, and maintain than adding an ORM dependency.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import DB_PATH, ensure_data_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    manufacturer TEXT DEFAULT '',
    model_number TEXT DEFAULT '',
    device_type TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    vehicle_year TEXT DEFAULT '',
    vehicle_make TEXT DEFAULT '',
    vehicle_model TEXT DEFAULT '',
    vehicle_trim TEXT DEFAULT '',
    vehicle_engine TEXT DEFAULT '',
    vehicle_drivetrain TEXT DEFAULT '',
    vin TEXT DEFAULT '',
    answer_level TEXT NOT NULL DEFAULT 'experienced',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    page_count INTEGER DEFAULT 0,
    chunk_count INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT DEFAULT '',
    embedding_signature TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Lightweight cache for optional online automotive lookups (VIN decode,
-- recalls). No Redis/service dependency -- just SQLite, same as
-- everything else. See app/services/automotive_sources.py.
CREATE TABLE IF NOT EXISTS online_cache (
    cache_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    request_type TEXT NOT NULL,
    identifier TEXT NOT NULL,
    response_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

-- Text mirror of every indexed chunk. Chroma stores the vectors; this
-- stores the raw text with page/section attribution so keyword (BM25)
-- search, the local PDF viewer, and spec extraction have a dependable
-- non-vector source of truth. Section is the heading path detected at
-- chunking time ('' when the page had no recognizable heading).
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    device_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    page INTEGER NOT NULL DEFAULT 1,
    section TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_device ON chunks(device_id);

-- Persistent background job queue (indexing/reindexing) with progress,
-- retries and cancellation. Survives restarts; jobs abandoned mid-run by
-- a crash are re-queued on startup rather than lost or silently dropped.
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|error|cancelled
    progress INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 2,
    error TEXT NOT NULL DEFAULT '',
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

-- Per-device chat sessions with persisted conversation history.
CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'New conversation',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_device ON chat_sessions(device_id);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,  -- 'user' | 'assistant'
    content TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);

-- Structured diagnostic session tracking: what symptom started it, which
-- steps were tested, what was measured, and what got ruled out.
CREATE TABLE IF NOT EXISTS diagnostic_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    symptoms TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',  -- open|resolved|parked
    outcome TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_diag_sessions_device ON diagnostic_sessions(device_id);

CREATE TABLE IF NOT EXISTS diagnostic_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES diagnostic_sessions(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL,
    reading TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',  -- ''|pass|fail|ruled_out
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_diag_steps_session ON diagnostic_steps(session_id);

-- Structured specification values extracted from the manuals (torque,
-- fluids, capacities, fuses, schedules). 'verified' rows cleared both the
-- component and property verification layers; 'candidate' rows were found
-- by pattern but not verified for the requested component/property.
-- Nothing is ever invented here -- every row keeps its source document,
-- page and excerpt.
CREATE TABLE IF NOT EXISTS spec_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    page INTEGER NOT NULL DEFAULT 1,
    section TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,  -- torque|fluid|capacity|fuse|schedule|other
    component TEXT NOT NULL DEFAULT '',
    property TEXT NOT NULL DEFAULT '',
    value_text TEXT NOT NULL,
    value_numeric REAL,
    unit TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'candidate',  -- verified|candidate
    excerpt TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(document_id, page, category, component, property, value_text)
);
CREATE INDEX IF NOT EXISTS idx_spec_device ON spec_values(device_id);
CREATE INDEX IF NOT EXISTS idx_spec_doc ON spec_values(document_id);

-- Aggregate counters for retrieval/verification metrics (Settings page).
CREATE TABLE IF NOT EXISTS metrics (
    name TEXT PRIMARY KEY,
    value REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_connection() -> sqlite3.Connection:
    ensure_data_dirs()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Adds columns to existing installations' databases that predate a
    given feature, so upgrading never requires deleting data. Safe to run
    on every startup -- each check is a no-op once already applied."""
    doc_cols = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    if "embedding_signature" not in doc_cols:
        conn.execute(
            "ALTER TABLE documents ADD COLUMN embedding_signature TEXT NOT NULL DEFAULT ''"
        )

    device_cols = {row["name"] for row in conn.execute("PRAGMA table_info(devices)")}
    vehicle_text_columns = [
        "vehicle_year", "vehicle_make", "vehicle_model",
        "vehicle_trim", "vehicle_engine", "vehicle_drivetrain", "vin",
    ]
    for col in vehicle_text_columns:
        if col not in device_cols:
            conn.execute(f"ALTER TABLE devices ADD COLUMN {col} TEXT DEFAULT ''")
    if "answer_level" not in device_cols:
        conn.execute("ALTER TABLE devices ADD COLUMN answer_level TEXT NOT NULL DEFAULT 'experienced'")


@contextmanager
def db_cursor():
    """Context manager yielding a cursor and committing/closing automatically."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


def bump_metric(name: str, amount: float = 1.0) -> None:
    """Increments a named metric counter (retrieval/verification stats for
    the Settings diagnostics panel). Cheap, safe from any thread."""
    with db_cursor() as cur:
        cur.execute(
            """INSERT INTO metrics (name, value, updated_at) VALUES (?, ?, datetime('now'))
               ON CONFLICT(name) DO UPDATE SET
                 value = value + excluded.value, updated_at = excluded.updated_at""",
            (name, amount),
        )


def get_metrics() -> dict[str, float]:
    with db_cursor() as cur:
        cur.execute("SELECT name, value FROM metrics ORDER BY name")
        return {row["name"]: row["value"] for row in cur.fetchall()}
