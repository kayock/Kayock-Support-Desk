"""
Local keyword/text index over indexed manual chunks.

Backs true keyword retrieval (BM25 via SQLite FTS5) alongside Chroma's
semantic search, plus device search and the PDF viewer's text lookup.
The `chunks` table is the non-vector source of truth for every indexed
chunk (text + page + section + device/document attribution); the FTS5
virtual table indexes the same rows for BM25 ranking. When the SQLite
build lacks FTS5, a plain-LIKE scanning fallback keeps keyword search
working (slower, still correct).

Keying the FTS rows on the chunks table's rowid keeps the two in lockstep
with explicit inserts/deletes -- no triggers, no sync worker.

This module never touches embeddings, so it has no interaction with the
embedding_signature / needs_reindex machinery: reindexing simply
rewrites both the vector store and this text index together.
"""
from __future__ import annotations

import re
import sqlite3

from app.db import db_cursor, get_connection

_FTS_TABLE = "fts_chunks"
_fts_available: bool | None = None


def fts5_available() -> bool:
    """Whether this Python's SQLite build includes FTS5 (it does on every
    mainstream CPython build; the LIKE fallback covers the rest)."""
    global _fts_available
    if _fts_available is None:
        try:
            conn = get_connection()
            try:
                conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS temp.probe_fts5 USING fts5(x)"
                )
                conn.execute("DROP TABLE IF EXISTS temp.probe_fts5")
                _fts_available = True
            finally:
                conn.close()
        except sqlite3.Error:
            _fts_available = False
    return _fts_available


def _ensure_fts(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5(
            text,
            section,
            chunk_id UNINDEXED
        )"""
    )


# ----------------------------------------------------------------- write ----

def store_chunks(document_id: int, device_id: int, chunks: list[dict]) -> list[int]:
    """Persists chunk dicts ({text, page, filename, section}) into the
    chunks table and the keyword index. Returns the chunk row ids, in
    order, for anything that needs to reference them."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        ids: list[int] = []
        for i, c in enumerate(chunks):
            cur.execute(
                """INSERT INTO chunks (document_id, device_id, chunk_index, page, section, text)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (document_id, device_id, i, c.get("page", 1), c.get("section", ""), c["text"]),
            )
            ids.append(cur.lastrowid)
        if fts5_available():
            _ensure_fts(conn)
            cur.executemany(
                f"""INSERT INTO {_FTS_TABLE} (rowid, text, section, chunk_id)
                    VALUES (?, ?, ?, ?)""",
                [
                    (cid, c["text"], c.get("section", ""), str(cid))
                    for cid, c in zip(ids, chunks)
                ],
            )
        conn.commit()
        return ids
    finally:
        conn.close()


def delete_document(document_id: int) -> None:
    """Removes a document's chunk rows and their keyword-index entries."""
    with db_cursor() as cur:
        cur.execute("SELECT id FROM chunks WHERE document_id = ?", (document_id,))
        ids = [row["id"] for row in cur.fetchall()]
    if not ids:
        return
    conn = get_connection()
    try:
        cur = conn.cursor()
        if fts5_available():
            _ensure_fts(conn)
            for cid in ids:
                cur.execute(f"DELETE FROM {_FTS_TABLE} WHERE rowid = ?", (cid,))
        cur.execute(
            f"DELETE FROM chunks WHERE id IN ({', '.join('?' for _ in ids)})",
            ids,
        )
        conn.commit()
    finally:
        conn.close()


def backfill_from_vector_store(document_ids: list[int] | None = None) -> int:
    """One-time migration for installations indexed before the chunks
    table existed: copies chunk texts (and their page metadata) out of
    Chroma so keyword search, the viewer, and spec extraction cover the
    existing library without any re-upload or re-embedding. Idempotent --
    documents that already have chunk rows are skipped. Returns the
    number of documents backfilled."""
    from app.services import device_service, vector_store

    with db_cursor() as cur:
        if document_ids is None:
            cur.execute(
                """SELECT id, device_id FROM documents
                   WHERE status = 'ready'
                     AND id NOT IN (SELECT DISTINCT document_id FROM chunks)"""
            )
        else:
            if not document_ids:
                return 0
            placeholders = ", ".join("?" for _ in document_ids)
            cur.execute(
                f"""SELECT id, device_id FROM documents
                   WHERE status = 'ready'
                     AND id IN ({placeholders})
                     AND id NOT IN (SELECT DISTINCT document_id FROM chunks)""",
                document_ids,
            )
        docs = [dict(row) for row in cur.fetchall()]

    count = 0
    for doc in docs:
        chunks = vector_store.get_document_chunks(doc["id"])
        if chunks:
            store_chunks(doc["id"], doc["device_id"], chunks)
            count += 1
    return count


# ----------------------------------------------------------------- query ----

_FTS_SPECIAL = re.compile(r'[^\w\s]')
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _fts_query_text(query: str) -> str:
    """Turns free user text into a safe FTS5 OR query: one quoted token
    per word (an OBD code like P0302 stays a single token). Quoting every
    token means user punctuation can never break the query syntax."""
    words = _WORD_RE.findall(query or "")
    if not words:
        return ""
    return " OR ".join(f'"{w}"' for w in words)


def search(device_id: int, query: str, limit: int, allowed_document_ids: list[int] | None = None) -> list[dict]:
    """Keyword search over a device's indexed chunks, best-matching first.
    Returns {"text", "meta", "distance": None, "score"} dicts shaped like
    vector_store.query results so retrieval code can treat both uniformly.
    `limit` caps results; ranks come from BM25 when FTS5 exists, or from
    raw LIKE substring hits otherwise."""
    query = (query or "").strip()
    if not query:
        return []
    if fts5_available():
        return _search_fts(device_id, query, limit, allowed_document_ids)
    return _search_like(device_id, query, limit, allowed_document_ids)


def _device_filter_sql(device_id: int, allowed_document_ids: list[int] | None) -> tuple[str, list]:
    if allowed_document_ids is None:
        return "c.device_id = ?", [device_id]
    if not allowed_document_ids:
        return "1 = 0", []
    placeholders = ", ".join("?" for _ in allowed_document_ids)
    return f"c.device_id = ? AND c.document_id IN ({placeholders})", [device_id, *allowed_document_ids]


def _search_fts(device_id: int, query: str, limit: int, allowed_document_ids) -> list[dict]:
    match = _fts_query_text(query)
    if not match:
        return []
    where_sql, params = _device_filter_sql(device_id, allowed_document_ids)
    sql = f"""
        SELECT c.id, c.document_id, c.device_id, c.page, c.section, c.text, d.original_filename,
               bm25({_FTS_TABLE}) AS rank
        FROM {_FTS_TABLE} f
        JOIN chunks c ON c.id = f.rowid
        JOIN documents d ON d.id = c.document_id
        WHERE {_FTS_TABLE} MATCH ? AND {where_sql}
        ORDER BY rank
        LIMIT ?
    """
    rows = _run_sql(sql, [match, *params, limit])
    return [_row_to_hit(row) for row in rows]


def _search_like(device_id: int, query: str, limit: int, allowed_document_ids) -> list[dict]:
    words = _WORD_RE.findall(query.lower())
    if not words:
        return []
    where_sql, params = _device_filter_sql(device_id, allowed_document_ids)
    # Every word must appear somewhere in the chunk (AND semantics); the
    # score is simply how many of the query's words hit, then shorter
    # chunks first as a weak relevance proxy.
    likes = " AND ".join("LOWER(c.text) LIKE ?" for _ in words)
    like_params = [f"%{w}%" for w in words]
    sql = f"""
        SELECT c.id, c.document_id, c.device_id, c.page, c.section, c.text, d.original_filename
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE {likes} AND {where_sql}
        LIMIT ?
    """
    rows = _run_sql(sql, [*like_params, *params, limit * 4])
    scored = []
    for row in rows:
        text_lower = row["text"].lower()
        score = sum(1 for w in words if w in text_lower)
        scored.append((score, row))
    scored.sort(key=lambda pair: (-pair[0], len(pair[1]["text"])))
    return [_row_to_hit(row, score=score) for score, row in scored[:limit]]


def _run_sql(sql: str, params: list) -> list[sqlite3.Row]:
    conn = get_connection()
    try:
        if fts5_available():
            _ensure_fts(conn)
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        conn.close()


def _row_to_hit(row: sqlite3.Row, score: float | None = None) -> dict:
    return {
        "text": row["text"],
        "meta": {
            "document_id": row["document_id"],
            "device_id": row["device_id"],
            "page": row["page"],
            "section": row["section"],
            "filename": row["original_filename"],
        },
        "distance": None,
        "score": float(score if score is not None else -row["rank"]) if "rank" in row.keys() else float(score or 0.0),
    }


# ------------------------------------------------------------- utilities ----

def get_document_chunks(document_id: int) -> list[dict]:
    """All stored chunks for a document, in order -- used by spec
    extraction and the viewer."""
    with db_cursor() as cur:
        cur.execute(
            """SELECT c.id, c.document_id, c.device_id, c.chunk_index, c.page, c.section, c.text,
                      d.original_filename
               FROM chunks c JOIN documents d ON d.id = c.document_id
               WHERE c.document_id = ? ORDER BY c.chunk_index""",
            (document_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return [
        {
            "text": r["text"],
            "page": r["page"],
            "section": r["section"],
            "filename": r["original_filename"],
            "chunk_id": r["id"],
        }
        for r in rows
    ]


def iter_device_chunks(device_id: int) -> list[dict]:
    with db_cursor() as cur:
        cur.execute(
            """SELECT c.id, c.document_id, c.page, c.section, c.text, d.original_filename
               FROM chunks c JOIN documents d ON d.id = c.document_id
               WHERE c.device_id = ?
               ORDER BY c.document_id, c.chunk_index""",
            (device_id,),
        )
        return [dict(r) for r in cur.fetchall()]
