"""
Lightweight persistent job queue for background work (indexing,
reindexing), backed by the same SQLite database as everything else.

Why: indexing previously ran as FastAPI BackgroundTasks -- invisible,
untracked, and lost on restart/crash mid-index. This queue gives every
job a row with status, progress, attempts, cancellation, and crash
recovery (jobs found 'running' after a restart are re-queued, never
silently dropped), plus an API so the UI can show what's happening.

Design notes:
- One worker thread, FIFO by job id -- matches the existing "single
  background worker" behavior; uploads still return immediately.
- Handlers are looked up by job_type so new job kinds can be added
  without touching the runner.
- Cancellation is cooperative: long-running handlers receive a cancel
  check callback and bail out at safe points.
- No new dependency, no separate broker process -- plain SQLite.
"""
from __future__ import annotations

import json
import threading
import time

from app.db import db_cursor
from app.services.logging_service import get_logger

POLL_INTERVAL_SECONDS = 0.25


log = get_logger("jobs")


class JobCancelled(RuntimeError):
    """Raised inside a handler when the user asked to cancel its job."""


# ------------------------------------------------------------------ core ----

def enqueue(job_type: str, payload: dict, total: int = 0, max_attempts: int = 2) -> int:
    """Adds a job and returns its id. The worker picks it up shortly."""
    with db_cursor() as cur:
        cur.execute(
            """INSERT INTO jobs (job_type, payload_json, status, total, max_attempts)
               VALUES (?, ?, 'queued', ?, ?)""",
            (job_type, json.dumps(payload), total, max_attempts),
        )
        return cur.lastrowid


def get_job(job_id: int) -> dict | None:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_jobs(limit: int = 50) -> list[dict]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)
        )
        return [dict(row) for row in cur.fetchall()]


def request_cancel(job_id: int) -> bool:
    """Marks a job as cancel-requested. Only jobs still queued/running
    accept the request; returns whether it took effect."""
    with db_cursor() as cur:
        cur.execute(
            """UPDATE jobs SET cancel_requested = 1, updated_at = datetime('now')
               WHERE id = ? AND status IN ('queued', 'running')""",
            (job_id,),
        )
        return cur.rowcount > 0


def is_cancel_requested(job_id: int) -> bool:
    with db_cursor() as cur:
        cur.execute("SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        return bool(row and row["cancel_requested"])


def update_progress(job_id: int, progress: int, total: int) -> None:
    with db_cursor() as cur:
        cur.execute(
            """UPDATE jobs SET progress = ?, total = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (progress, total, job_id),
        )


def recover_stale_running_jobs() -> int:
    """On startup, any job still marked 'running' is from a previous
    process that died mid-run (crash, power loss, forced kill). Re-queue
    them so they retry under the normal attempts logic instead of being
    stuck forever. Returns how many were re-queued."""
    with db_cursor() as cur:
        cur.execute(
            """UPDATE jobs SET status = 'queued', updated_at = datetime('now')
               WHERE status = 'running'"""
        )
        return cur.rowcount


def wait_until_idle(timeout: float = 30.0) -> bool:
    """Blocks until no queued/running jobs remain (or timeout). Mainly a
    test/admin helper -- the app itself never waits on the queue."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with db_cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE status IN ('queued', 'running')"
            )
            if cur.fetchone()["c"] == 0:
                return True
        time.sleep(0.1)
    return False


# --------------------------------------------------------------- handlers ----

_HANDLERS: dict[str, callable] = {}


def register_handler(job_type: str, fn: callable) -> None:
    """Registers the function that runs a given job_type. fn receives
    (payload: dict, progress_cb: callable, cancel_cb: callable)."""
    _HANDLERS[job_type] = fn


def _handle_index_document(payload: dict, progress_cb, cancel_cb) -> None:
    from pathlib import Path

    from app.services import indexing

    indexing.index_document(
        payload["document_id"],
        payload["device_id"],
        Path(payload["file_path"]),
        payload["original_filename"],
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
        prior_status=payload.get("prior_status", ""),
    )


register_handler("index_document", _handle_index_document)


# ----------------------------------------------------------------- worker ----

_worker_started = False
_worker_lock = threading.Lock()


def ensure_worker_started() -> None:
    """Starts the single background worker thread once per process."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_worker_loop, name="kayock-jobs", daemon=True).start()


def _worker_loop() -> None:
    while True:
        try:
            worked = _run_next_job()
        except Exception:  # noqa: BLE001 -- the worker must never die
            worked = False
        time.sleep(POLL_INTERVAL_SECONDS if not worked else 0.0)


def _claim_next_job() -> dict | None:
    """Atomically claims the oldest queued job, if any. BEGIN IMMEDIATE
    serializes claimers for the whole select-then-update, so even several
    worker processes can never double-run the same job."""
    from app.db import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("SELECT id FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            return None
        job_id = row["id"]
        cur.execute(
            """UPDATE jobs SET status = 'running', started_at = datetime('now'),
               attempts = attempts + 1, updated_at = datetime('now')
               WHERE id = ? AND status = 'queued'""",
            (job_id,),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return None
        cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        job = dict(cur.fetchone())
        conn.commit()
        return job
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _run_next_job() -> bool:
    job = _claim_next_job()
    if job is None:
        return False
    job_id = job["id"]
    payload = json.loads(job["payload_json"] or "{}")
    handler = _HANDLERS.get(job["job_type"])

    def progress_cb(done: int, total: int) -> None:
        update_progress(job_id, done, total)

    def cancel_cb() -> None:
        if is_cancel_requested(job_id):
            raise JobCancelled()

    try:
        if is_cancel_requested(job_id):
            raise JobCancelled()
        if handler is None:
            raise RuntimeError(f"No handler registered for job type '{job['job_type']}'")
        handler(payload, progress_cb, cancel_cb)
    except JobCancelled:
        _finish_job(job_id, "cancelled", error="Cancelled by user.")
        _on_job_cancelled(job["job_type"], payload)
        log.info("job %s cancelled", job_id)
    except Exception as exc:  # noqa: BLE001 -- failed jobs retry or surface as errors, never crash the worker
        if job["attempts"] < job["max_attempts"]:
            with db_cursor() as cur:
                cur.execute(
                    """UPDATE jobs SET status = 'queued', error = ?,
                       updated_at = datetime('now') WHERE id = ?""",
                    (str(exc)[:500], job_id),
                )
        else:
            _finish_job(job_id, "error", error=str(exc)[:500])
            _on_job_failed(job["job_type"], payload, str(exc))
            log.error("job %s (%s) failed after %s attempts: %s", job_id, job["job_type"], job["max_attempts"], exc)
    else:
        _finish_job(job_id, "done", error="")
        log.info("job %s (%s) done", job_id, job["job_type"])
    return True


def _finish_job(job_id: int, status: str, error: str) -> None:
    with db_cursor() as cur:
        cur.execute(
            """UPDATE jobs SET status = ?, error = ?, finished_at = datetime('now'),
               updated_at = datetime('now') WHERE id = ?""",
            (status, error, job_id),
        )


def _on_job_cancelled(job_type: str, payload: dict) -> None:
    """Post-cancel cleanup per job type: restore the document to the
    status it had before indexing started, so nothing is left stuck in
    'indexing'."""
    if job_type != "index_document":
        return
    prior = payload.get("prior_status") or "needs_reindex"
    with db_cursor() as cur:
        cur.execute(
            """UPDATE documents SET status = ?, error = 'Indexing was cancelled before finishing.'
               WHERE id = ?""",
            (prior, payload.get("document_id")),
        )


def _on_job_failed(job_type: str, payload: dict, error: str) -> None:
    # index_document already records its own document-level errors; other
    # job types can add their own finalization here later.
    return


def process_pending_now(timeout: float = 30.0) -> int:
    """Synchronously runs any pending jobs (drain the queue). Used by
    tests and admin tooling -- the normal path is the worker thread."""
    count = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _run_next_job():
            break
        count += 1
    return count
