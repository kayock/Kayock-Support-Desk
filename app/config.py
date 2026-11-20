"""
Central configuration for Kayock Support Desk.

Everything that varies between installations (data location, network port,
etc.) lives here. Per-install *settings* that a user can change from the
Settings page (LLM endpoint, model names, API key) are stored in the
database instead -- see app/services/settings_service.py.
"""
import os
from pathlib import Path

# Root of the project (the folder containing this file's parent's parent)
BASE_DIR = Path(__file__).resolve().parent.parent

# All persistent, user-specific data lives under DATA_DIR so that an
# installation can be backed up / moved by simply copying this one folder.
DATA_DIR = Path(os.environ.get("KAYOCK_DATA_DIR", BASE_DIR / "data")).resolve()
DOCUMENTS_DIR = DATA_DIR / "documents"
CHROMA_DIR = DATA_DIR / "chroma"
DB_PATH = DATA_DIR / "kayock.db"
LOGS_DIR = DATA_DIR / "logs"

# Web server
HOST = os.environ.get("KAYOCK_HOST", "0.0.0.0")
PORT = int(os.environ.get("KAYOCK_PORT", "8765"))

# Chroma collection name (one collection, documents are filtered by device_id)
CHROMA_COLLECTION = "manuals"

# Chunking defaults
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200

# Retrieval defaults
TOP_K = 6
# Retrieval reranking (see app.services.vehicle.rerank_hits): pull a wider
# semantic candidate set from Chroma, then locally boost exact phrase/code
# matches before trimming back down to TOP_K. Keeps this a cheap local
# rerank pass, not a second search system.
RERANK_CANDIDATE_MULTIPLIER = 3
# Hybrid retrieval: a keyword/BM25 candidate set (SQLite FTS5, with a LIKE
# fallback when FTS5 is unavailable) is merged with the semantic candidates
# by reciprocal rank fusion (see app.services.keyword_store). This is how
# many extra keyword hits to fetch alongside TOP_K * RERANK_CANDIDATE_MULTIPLIER.
KEYWORD_CANDIDATES = 24

# Managed llama-server chat process settings
LLAMA_SERVER_CONTEXT_SIZE = 4096

# Default local-first LLM settings (Ollama's OpenAI-compatible endpoint).
# These are only used the very first time the app runs, to seed the
# settings table. After that, the Settings page (or the managed AI
# start-up flow) is the source of truth.
DEFAULT_SETTINGS = {    "llm_base_url": "http://localhost:11434/v1",
    "llm_api_key": "ollama",
    "llm_model": "llama3.1",
    "embedding_base_url": "http://localhost:11434/v1",
    "embedding_model": "nomic-embed-text",
    # --- Managed local AI (see app/services/ai_manager.py) ---
    # "managed": Kayock launches/owns local model processes for the user.
    # "external": the classic advanced mode -- the fields above are used
    # as-is, and the user is responsible for running their own server.
    "ai_mode": "managed",
    "ai_setup_complete": "0",
    "chat_model_path": "",
    "llama_server_path": "",
    "ollama_path": "",
    "embedding_backend": "ollama",  # only backend implemented for now
    # --- Optional online automotive sources ---
    "online_sources_enabled": "1",
    "online_vin_lookup_enabled": "1",
    "online_recalls_enabled": "1",
    # Auto-unload the answering model after N minutes of inactivity.
    # "0" = Never (default). Embedding/manual-search is never auto-unloaded.
    "ai_idle_unload_minutes": "0",
    # --- Managed llama-server launch tuning (Advanced) ---
    # Context window for the managed answering model. Defaults to the same
    # value Kayock has always used; raising it is safe on machines with
    # enough memory. Never silently reduced.
    "llm_context_size": str(LLAMA_SERVER_CONTEXT_SIZE),
    # Extra command-line flags for managed llama-server (e.g. GPU options
    # like --n-gpu-layers). Parsed shell-style; empty = defaults only.
    "llama_server_extra_args": "",
    # --- Conversational Q&A ---
    # How many previous chat messages are included as conversation history
    # for follow-up questions. Keeps meaning across turns while retrieval
    # and verification still run fresh on every turn.
    "chat_history_max_messages": "8",
    # --- Interface ---
    # "auto" follows the OS preference; "light"/"dark" override it.
    "ui_theme": "auto",
    "ui_compact": "0",
    # --- Document source preference for spec lookups across multiple manuals ---
    # Comma-separated filename keywords (lowercase) in priority order; a
    # document whose filename matches an earlier keyword is preferred when
    # the same specification appears in several manuals.
    "spec_document_preference": "service, factory, repair, workshop, owner, user, manual",
}

# Managed llama-server chat process settings
LLAMA_SERVER_HOST = "127.0.0.1"
LLAMA_SERVER_STARTUP_TIMEOUT = 120  # seconds -- GGUF loads can be slow

# Managed Ollama embedding process settings
OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11434
OLLAMA_EMBEDDING_MODEL = "nomic-embed-text"
OLLAMA_STARTUP_TIMEOUT = 30
OLLAMA_PULL_TIMEOUT = 900  # a first-time model download can take a while

# --- Optional online automotive sources (see app/services/automotive_sources.py) ---
# Both are official, free, public NHTSA endpoints -- no API key, no
# registration, no mandatory paid dependency.
NHTSA_VPIC_BASE_URL = "https://vpic.nhtsa.dot.gov/api"
NHTSA_RECALLS_BASE_URL = "https://api.nhtsa.gov/recalls/recallsByVehicle"
ONLINE_LOOKUP_TIMEOUT = 6  # seconds -- keep short so a slow public API never freezes the page

# Cache lifetimes (seconds). VIN decode is effectively static (the VIN
# doesn't change); recalls are checked periodically since new campaigns
# get filed over time; generic DTC definitions are a local dictionary and
# aren't cached at all (no network call to cache).
VIN_DECODE_CACHE_SECONDS = 365 * 24 * 60 * 60  # ~1 year
RECALLS_CACHE_SECONDS = 7 * 24 * 60 * 60  # 1 week


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
