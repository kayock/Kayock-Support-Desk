"""
Manages local AI model processes so the user never has to.

Two components, each tracked independently:

- **chat** ("Answering Model"): a `llama-server` process running the
  user's chosen GGUF file directly. Kayock picks a free local port,
  launches it, waits for its /health endpoint, then points the existing
  OpenAI-compatible chat client (app.services.llm_client) at it by
  writing the usual llm_base_url/llm_model settings.

- **embedding** ("Manual Search"): a managed Ollama instance serving
  `nomic-embed-text`. Kayock starts `ollama serve` if nothing is already
  listening on Ollama's default port, verifies the embedding model is
  pulled (pulling it if not), then points the existing embedding client
  at it the same way.

Deliberately reuses the *existing* settings table / embedding_signature /
reindex machinery instead of adding a parallel configuration system --
starting AI just means "write the usual settings, the usual way". See
README for why embeddings use Ollama rather than a second llama-server.

All state here is in-memory (per running app process) and guarded by a
lock; nothing about *which port got picked this run* needs to persist.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.config import (
    LLAMA_SERVER_CONTEXT_SIZE,
    LLAMA_SERVER_HOST,
    LLAMA_SERVER_STARTUP_TIMEOUT,
    LOGS_DIR,
    OLLAMA_EMBEDDING_MODEL,
    OLLAMA_HOST,
    OLLAMA_PORT,
    OLLAMA_PULL_TIMEOUT,
    OLLAMA_STARTUP_TIMEOUT,
    ensure_data_dirs,
)
from app.services import indexing, settings_service

_lock = threading.RLock()

_state = {
    "chat": {
        "status": "stopped",  # stopped | starting | running | error
        "detail": "",
        "model_name": "",
        "port": None,
        "managed": False,
    },
    "embedding": {
        "status": "stopped",  # stopped | starting | pulling | running | error
        "detail": "",
        "model_name": OLLAMA_EMBEDDING_MODEL,
        "port": None,
        "managed": False,
    },
}

# Process handles are kept out of _state (not JSON-serializable / not for the UI).
_processes: dict[str, subprocess.Popen | None] = {"chat": None, "embedding": None}
_busy = {"chat": False, "embedding": False}


# --------------------------------------------------------------- status ----

def get_status() -> dict:
    with _lock:
        return {
            "chat": dict(_state["chat"]),
            "embedding": dict(_state["embedding"]),
            "overall": _overall(),
        }


def _overall() -> str:
    chat, emb = _state["chat"]["status"], _state["embedding"]["status"]
    if chat == "running" and emb == "running":
        return "ready"
    if chat == "error" or emb == "error":
        return "attention"
    if chat in ("starting",) or emb in ("starting", "pulling"):
        return "starting"
    return "offline"


def _set(component: str, **fields) -> None:
    with _lock:
        _state[component].update(fields)


# ------------------------------------------------------------- helpers -----

def friendly_model_name(path: str) -> str:
    """Best-effort, non-magical display name from a GGUF filename.
    'Qwen3.5-4B-Q4_K_M.gguf' -> 'Qwen3.5 4B'. Falls back to the raw stem
    if nothing looks like a quantization suffix to trim."""
    stem = Path(path).stem
    tokens = stem.split("-")
    quant_pattern = re.compile(r"^(q\d.*|iq\d.*|f16|f32|bf16|gguf)$", re.IGNORECASE)
    while tokens and quant_pattern.match(tokens[-1]):
        tokens.pop()
    return " ".join(t for t in tokens if t) if tokens else stem


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _http_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _http_json(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        import json

        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def _find_executable(configured_path: str, *candidates: str) -> str:
    """Returns a usable executable path: the explicitly configured one if
    set, otherwise the first of `candidates` found on PATH, otherwise ''."""
    if configured_path and Path(configured_path).exists():
        return configured_path
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    return ""


def find_executable(component: str) -> str:
    """Public discovery helper (used by the diagnostics panel): where the
    given component's executable was/would be found ('' = not found)."""
    s = settings_service.get_all()
    if component == "chat":
        return _find_executable(s.get("llama_server_path", ""), "llama-server", "llama-server.exe")
    if component == "embedding":
        return _find_executable(s.get("ollama_path", ""), "ollama", "ollama.exe")
    return ""


def chat_context_size() -> int:
    """The context window passed to managed llama-server. Defaults to the
    same value Kayock has always used (LLAMA_SERVER_CONTEXT_SIZE); a user
    may raise it from Settings. It is clamped to a small floor only to
    avoid launching with an unusably tiny context -- never silently
    reduced from the configured value otherwise."""
    raw = settings_service.get("llm_context_size") or ""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return LLAMA_SERVER_CONTEXT_SIZE
    if value <= 0:
        return LLAMA_SERVER_CONTEXT_SIZE
    return max(value, 512)


def _parse_extra_args(raw: str) -> list[str]:
    """Splits the user's llama_server_extra_args setting (shell-style
    quoting via shlex) into argv tokens. Bad quoting yields no extra args
    rather than a broken launch."""
    if not (raw or "").strip():
        return []
    try:
        import shlex

        return shlex.split(raw)
    except ValueError:
        return []


def _popen_kwargs(log_file) -> dict:
    kwargs = {"stdout": log_file, "stderr": subprocess.STDOUT}
    if sys.platform == "win32":
        # Avoid flashing a console window on Windows.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def _spawn(cmd: list[str], log_path: Path) -> subprocess.Popen:
    """Isolated so tests can monkeypatch this single choke point instead
    of mocking subprocess.Popen calls scattered through the module.
    Spawned in its own process group/session on POSIX so a later stop
    can signal the whole tree the server started, not just its own PID --
    llama-server/Ollama don't typically fork helper processes, but this
    makes cleanup correct if a given build ever does."""
    ensure_data_dirs()
    log_file = open(log_path, "ab", buffering=0)
    kwargs = _popen_kwargs(log_file)
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def _log_tail(log_path: Path, max_chars: int = 4000) -> str:
    try:
        data = log_path.read_bytes()[-max_chars:]
        return data.decode("utf-8", errors="ignore").strip()
    except OSError:
        return ""


def _log_hint(log_path: Path) -> str:
    """A short, single-line hint from the end of the log -- enough to
    orient a diagnosis without dumping a stack trace into the UI. The
    full log always stays in data/logs/ for anyone who needs it."""
    tail = _log_tail(log_path)
    if not tail:
        return ""
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    if not lines:
        return ""
    return lines[-1][:160]


def _startup_failure_detail(friendly_prefix: str, log_path: Path) -> str:
    hint = _log_hint(log_path)
    detail = friendly_prefix
    if hint:
        detail += f" ({hint})"
    detail += f" See {log_path.name} in the data/logs folder for details."
    return detail


def _process_alive(proc: subprocess.Popen | None) -> bool:
    return proc is not None and proc.poll() is None


# ---------------------------------------------------------------- start ----

def start_ai() -> None:
    """Kicks off both components in a background thread. Returns
    immediately -- callers should poll get_status()."""
    if _busy["chat"] or _busy["embedding"]:
        return  # a start (or stop) is already in progress
    threading.Thread(target=_start_ai_blocking, daemon=True).start()


def _start_ai_blocking() -> None:
    _start_chat_blocking()
    _start_embedding_blocking()


def _start_chat_blocking() -> None:
    with _lock:
        if _busy["chat"]:
            return
        _busy["chat"] = True
    try:
        _do_start_chat()
    finally:
        with _lock:
            _busy["chat"] = False


def _do_start_chat() -> None:
    s = settings_service.get_all()
    model_path = s.get("chat_model_path", "")

    if not model_path:
        _set("chat", status="error", detail="No answering model has been selected yet.")
        return
    if not Path(model_path).exists():
        _set(
            "chat",
            status="error",
            detail="The selected answering model could not be found.",
            model_name=friendly_model_name(model_path),
        )
        return

    # Already running and healthy under our management? Don't relaunch.
    proc = _processes["chat"]
    if _process_alive(proc) and _state["chat"]["port"] and _http_ok(
        f"http://{LLAMA_SERVER_HOST}:{_state['chat']['port']}/health"
    ):
        _set("chat", status="running")
        return

    server_path = _find_executable(s.get("llama_server_path", ""), "llama-server", "llama-server.exe")
    if not server_path:
        _set(
            "chat",
            status="error",
            detail="llama-server executable not found. Set its path in Advanced AI Settings.",
        )
        return

    port = find_free_port(LLAMA_SERVER_HOST)
    log_path = LOGS_DIR / "chat_server.log"
    _set("chat", status="starting", detail="Starting answering model...", model_name=friendly_model_name(model_path))

    cmd = [
        server_path,
        "-m", model_path,
        "--port", str(port),
        "--host", LLAMA_SERVER_HOST,
        "-c", str(chat_context_size()),
        # No GPU flags are ever assumed here -- llama-server uses whatever
        # acceleration its build provides (CPU-only builds work identically).
        # Users can add e.g. --n-gpu-layers or --flash-attn via this setting.
        *_parse_extra_args(s.get("llama_server_extra_args", "")),
    ]
    try:
        proc = _spawn(cmd, log_path)
    except OSError as exc:
        _set("chat", status="error", detail=f"Could not start llama-server: {exc}")
        return
    _processes["chat"] = proc

    deadline = time.time() + LLAMA_SERVER_STARTUP_TIMEOUT
    health_url = f"http://{LLAMA_SERVER_HOST}:{port}/health"
    while time.time() < deadline:
        if not _process_alive(proc):
            _set(
                "chat",
                status="error",
                detail=_startup_failure_detail("The answering model exited unexpectedly while starting.", log_path),
            )
            return
        if _http_ok(health_url):
            settings_service.update(
                {"llm_base_url": f"http://{LLAMA_SERVER_HOST}:{port}/v1", "llm_model": "local-model", "llm_api_key": "not-needed"}
            )
            _set("chat", status="running", detail="", port=port, managed=True)
            record_activity()  # idle-unload clock starts fresh from a successful start
            return
        time.sleep(1)

    _set("chat", status="error", detail="The answering model took too long to start.")


def _start_embedding_blocking() -> None:
    with _lock:
        if _busy["embedding"]:
            return
        _busy["embedding"] = True
    try:
        _do_start_embedding()
    finally:
        with _lock:
            _busy["embedding"] = False


def _do_start_embedding() -> None:
    s = settings_service.get_all()
    base = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}"
    version_url = f"{base}/api/version"

    _set("embedding", status="starting", detail="Starting manual search...")

    already_running = _http_ok(version_url)
    if not already_running:
        ollama_path = _find_executable(s.get("ollama_path", ""), "ollama", "ollama.exe")
        if not ollama_path:
            _set(
                "embedding",
                status="error",
                detail="Ollama could not be found. Install Ollama or set its path in Advanced AI Settings.",
            )
            return

        log_path = LOGS_DIR / "embedding_server.log"
        try:
            proc = _spawn([ollama_path, "serve"], log_path)
        except OSError as exc:
            _set("embedding", status="error", detail=f"Could not start Ollama: {exc}")
            return
        _processes["embedding"] = proc

        deadline = time.time() + OLLAMA_STARTUP_TIMEOUT
        while time.time() < deadline:
            if not _process_alive(proc):
                _set(
                    "embedding",
                    status="error",
                    detail=_startup_failure_detail("Ollama exited unexpectedly while starting.", log_path),
                )
                return
            if _http_ok(version_url):
                break
            time.sleep(1)
        else:
            _set("embedding", status="error", detail="Ollama took too long to start.")
            return
        managed = True
    else:
        managed = False  # something else (or an earlier Kayock run) is already serving it

    # Make sure the embedding model is actually pulled.
    try:
        tags = _http_json(f"{base}/api/tags")
        have_model = any(
            m.get("name", "").split(":")[0] == OLLAMA_EMBEDDING_MODEL for m in tags.get("models", [])
        )
    except Exception:
        have_model = False

    if not have_model:
        _set("embedding", status="pulling", detail=f"Downloading {OLLAMA_EMBEDDING_MODEL}...")
        ollama_path = _find_executable(s.get("ollama_path", ""), "ollama", "ollama.exe")
        try:
            result = subprocess.run(
                [ollama_path or "ollama", "pull", OLLAMA_EMBEDDING_MODEL],
                capture_output=True,
                text=True,
                timeout=OLLAMA_PULL_TIMEOUT,
                **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if sys.platform == "win32" else {}),
            )
            if result.returncode != 0:
                _set(
                    "embedding",
                    status="error",
                    detail=f"Could not download {OLLAMA_EMBEDDING_MODEL}. "
                    f"Check your internet connection or run 'ollama pull {OLLAMA_EMBEDDING_MODEL}' manually.",
                )
                return
        except (OSError, subprocess.TimeoutExpired) as exc:
            _set("embedding", status="error", detail=f"Could not download {OLLAMA_EMBEDDING_MODEL}: {exc}")
            return

    settings_service.update({"embedding_base_url": f"{base}/v1", "embedding_model": OLLAMA_EMBEDDING_MODEL})
    indexing.mark_stale_ready_documents()
    _set("embedding", status="running", detail="", port=OLLAMA_PORT, managed=managed, model_name=OLLAMA_EMBEDDING_MODEL)


# ----------------------------------------------------------------- stop ----

def stop_ai() -> None:
    """Stops only the processes Kayock itself started this session.
    Never touches an Ollama instance that was already running before
    Kayock connected to it. Runs in a background thread so the API
    endpoint returns immediately -- use stop_ai_sync() instead for
    application shutdown, where a detached thread might not get to run
    before the process exits."""
    threading.Thread(target=_stop_ai_blocking, daemon=True).start()


def stop_ai_sync() -> None:
    """Blocking version of stop_ai(), for use during application
    shutdown. Runs on the caller's thread/event loop so it's guaranteed
    to complete (or hit its own internal timeouts) before the process
    exits, rather than racing a background thread against interpreter
    shutdown."""
    _stop_ai_blocking()


def _stop_ai_blocking() -> None:
    for component in ("chat", "embedding"):
        with _lock:
            if _busy[component]:
                continue
            _busy[component] = True
        try:
            _stop_component(component)
        finally:
            with _lock:
                _busy[component] = False


def _stop_component(component: str) -> None:
    if not _state[component]["managed"]:
        _set(component, status="stopped", detail="", port=None)
        return

    proc = _processes.get(component)
    port = _state[component].get("port")

    if _process_alive(proc):
        _terminate_process_tree(proc)

    # --- Verify real exit -- never report "stopped" on faith. ------------
    still_alive = _process_alive(proc)
    port_released = _wait_port_released(port, timeout=5) if port else True
    health_endpoint_down = _wait_health_down(component, port, timeout=5)

    if still_alive or not port_released or not health_endpoint_down:
        detail = (
            "Kayock could not fully stop the answering model."
            if component == "chat"
            else "Kayock could not fully stop the manual-search model."
        )
        _log_stop_failure(component, proc, still_alive, port_released, health_endpoint_down)
        # Keep the process handle around (if it's still alive) so a
        # subsequent Stop AI attempt can try again on the *same* process
        # rather than losing track of it.
        if not still_alive:
            _processes[component] = None
        _set(component, status="error", detail=detail)
        return

    _processes[component] = None
    _set(component, status="stopped", detail="", port=None, managed=False)


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """Graceful termination first, escalating to a forceful kill of the
    *entire process tree Kayock itself started* if needed -- never a
    broad by-name kill (e.g. `taskkill /IM llama-server.exe`), which
    could take down an unrelated FOXAI/Ollama/llama-server session the
    user is running independently. Only ever acts on this exact PID
    (and, on escalation, its children), never on processes matched by
    name alone."""
    if sys.platform == "win32":
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=6)
            return
        except subprocess.TimeoutExpired:
            pass
        # Escalate: kill this exact PID's process tree via taskkill /PID
        # ... /T /F -- scoped to the tree rooted at our PID, unlike /IM
        # which would match every process with that executable name.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        try:
            proc.wait(timeout=6)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass


def _wait_port_released(port: int | None, timeout: float = 5, host: str = "127.0.0.1") -> bool:
    """Polls until nothing answers on `port` (connection refused), or
    times out. Confirms the OS has actually freed the port for reuse,
    not just that our process object looks dead."""
    if not port:
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        try:
            s.connect((host, port))
        except OSError:
            return True  # refused / unreachable -- nothing listening anymore
        else:
            s.close()
            time.sleep(0.2)
        finally:
            try:
                s.close()
            except OSError:
                pass
    return False


def _wait_health_down(component: str, port: int | None, timeout: float = 5) -> bool:
    """Confirms the component's health endpoint has actually stopped
    responding -- belt-and-suspenders alongside the port check, since a
    port can briefly linger in a half-closed state while still serving."""
    if component == "chat":
        if not port:
            return True
        url = f"http://{LLAMA_SERVER_HOST}:{port}/health"
    else:
        url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/version"

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _http_ok(url, timeout=0.5):
            return True
        time.sleep(0.2)
    return not _http_ok(url, timeout=0.5)


def _log_stop_failure(component: str, proc, still_alive: bool, port_released: bool, health_down: bool) -> None:
    log_path = LOGS_DIR / ("chat_server.log" if component == "chat" else "embedding_server.log")
    try:
        with open(log_path, "ab") as f:
            f.write(
                f"\n[kayock] stop verification failed for {component}: "
                f"still_alive={still_alive} port_released={port_released} health_down={health_down}\n".encode()
            )
    except OSError:
        pass


def stop_chat_only() -> None:
    """Stops just the managed answering model, leaving manual-search
    (embedding) running. Used by the idle-unload watcher, and available
    for any future UI that wants to unload just the large model without
    a full Stop AI. Runs in a background thread, same pattern as stop_ai()."""
    threading.Thread(target=lambda: _stop_component_guarded("chat"), daemon=True).start()


def _stop_component_guarded(component: str) -> None:
    with _lock:
        if _busy[component]:
            return
        _busy[component] = True
    try:
        _stop_component(component)
    finally:
        with _lock:
            _busy[component] = False


# ------------------------------------------------------------ idle unload --
# A lightweight background timer -- not a scheduler. Every 30s it checks
# whether the "auto-unload answering model after inactivity" setting is
# on and the chat model has been idle longer than the configured window;
# if so it stops just the chat component (never embedding). Any real use
# of the answering model (asking a question, running OBD diagnosis, or
# simply Start AI completing) resets the idle clock via record_activity().

IDLE_UNLOAD_CHOICES = ("0", "10", "30", "60")  # minutes; "0" = Never
_IDLE_CHECK_INTERVAL_SECONDS = 30

_last_activity_ts = time.time()
_idle_watcher_started = False


def record_activity() -> None:
    """Marks the answering model as just having been used, resetting the
    idle-unload countdown. Safe to call often and from any thread."""
    global _last_activity_ts
    _last_activity_ts = time.time()


def ensure_idle_watcher_started() -> None:
    """Starts the background idle-check loop once per process. Safe to
    call repeatedly (e.g. on every app startup or Start AI click)."""
    global _idle_watcher_started
    with _lock:
        if _idle_watcher_started:
            return
        _idle_watcher_started = True
    threading.Thread(target=_idle_watcher_loop, daemon=True).start()


def _idle_watcher_loop() -> None:
    while True:
        time.sleep(_IDLE_CHECK_INTERVAL_SECONDS)
        try:
            _idle_watcher_tick()
        except Exception:  # noqa: BLE001 -- a watcher hiccup must never crash the app
            pass


def _idle_watcher_tick() -> None:
    minutes = _idle_unload_minutes()
    if minutes <= 0:
        return
    if _state["chat"]["status"] != "running":
        return
    if time.time() - _last_activity_ts >= minutes * 60:
        stop_chat_only()


def _idle_unload_minutes() -> int:
    raw = settings_service.get("ai_idle_unload_minutes") or "0"
    try:
        value = int(raw)
    except ValueError:
        return 0
    return value if str(value) in IDLE_UNLOAD_CHOICES else 0


def reset_state_for_tests() -> None:
    """Test-only helper to reset module-level state between tests."""
    global _last_activity_ts, _idle_watcher_started
    with _lock:
        _state["chat"] = {"status": "stopped", "detail": "", "model_name": "", "port": None, "managed": False}
        _state["embedding"] = {
            "status": "stopped",
            "detail": "",
            "model_name": OLLAMA_EMBEDDING_MODEL,
            "port": None,
            "managed": False,
        }
        _processes["chat"] = None
        _processes["embedding"] = None
        _busy["chat"] = False
        _busy["embedding"] = False
        _last_activity_ts = time.time()
