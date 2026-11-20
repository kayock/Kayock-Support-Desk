"""
Structured local logging: a rotating file handler writing to
data/logs/app.log, shared by app code (indexing failures, job outcomes,
AI lifecycle, retrieval stats) so diagnosing problems doesn't require
watching the console the launcher opened. Rotation keeps the log useful
without growing forever (5 MB x 3 files).

The launcher-run console output is untouched -- this is additive file
logging for the app itself; managed llama-server/Ollama keep their own
logs (chat_server.log / embedding_server.log) as before.
"""
from __future__ import annotations

import logging
import logging.handlers

from app.config import LOGS_DIR, ensure_data_dirs

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_configured = False
_log_lock = __import__("threading").Lock()


def _configure() -> None:
    global _configured
    with _log_lock:
        if _configured:
            return
        ensure_data_dirs()
        handler = logging.handlers.RotatingFileHandler(
            LOGS_DIR / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(_FORMAT))
        root = logging.getLogger("kayock")
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        root.propagate = False
        _configured = True


def get_logger(name: str) -> logging.Logger:
    """Returns a 'kayock.<name>' logger writing to data/logs/app.log."""
    _configure()
    return logging.getLogger(f"kayock.{name}")
