"""
Tests for app.services.ai_manager: the component that owns the lifecycle
of the managed llama-server (chat) and Ollama (embedding) processes.

Real subprocesses and real model servers are never used here -- `_spawn`,
`_http_ok`, `_http_json`, and `_find_executable` are the module's single
choke points for those, and are monkeypatched instead. This keeps the
suite fast and independent of any actual GGUF file or Ollama install.
"""
import time

import pytest

from app.db import db_cursor
from app.services import ai_manager, indexing, settings_service


class FakeProcess:
    """Stands in for subprocess.Popen. Starts 'alive' (poll() -> None)
    until terminate()/kill() is called, then looks exited."""

    def __init__(self):
        self.terminated = False
        self.killed = False
        self.pid = 4242

    def poll(self):
        return None if not (self.terminated or self.killed) else 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        if not (self.terminated or self.killed):
            raise Exception("wait() called on a process that was never asked to stop")
        return 0


@pytest.fixture(autouse=True)
def _reset_ai_manager():
    ai_manager.reset_state_for_tests()
    yield
    ai_manager.reset_state_for_tests()


def _wait_until_stable(component: str, timeout: float = 5.0) -> dict:
    """Polls get_status() until the given component leaves a transitional
    state (starting/pulling), mirroring how the real UI polls."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        status = ai_manager.get_status()
        last = status[component]
        if last["status"] not in ("starting", "pulling"):
            return last
        time.sleep(0.02)
    return last


# ------------------------------------------------------- saved paths -------

def test_saved_model_paths_persist(client):
    res = client.post(
        "/api/ai/setup",
        json={
            "chat_model_path": "/models/Qwen3.5-4B-Q4_K_M.gguf",
            "llama_server_path": "/opt/llama-server",
            "ollama_path": "/usr/bin/ollama",
            "start": False,
        },
    )
    assert res.status_code == 200

    s = settings_service.get_all()
    assert s["chat_model_path"] == "/models/Qwen3.5-4B-Q4_K_M.gguf"
    assert s["llama_server_path"] == "/opt/llama-server"
    assert s["ollama_path"] == "/usr/bin/ollama"
    assert s["ai_setup_complete"] == "1"
    assert s["ai_mode"] == "managed"


def test_skip_setup_marks_complete_without_paths(client):
    settings_service.update({"chat_model_path": ""})
    res = client.post("/api/ai/skip-setup")
    assert res.status_code == 200
    assert settings_service.get("ai_setup_complete") == "1"
    assert settings_service.get("chat_model_path") == ""


# --------------------------------------------------- missing model file ----

def test_missing_model_file_produces_clear_error():
    settings_service.update({"chat_model_path": "/does/not/exist/model.gguf"})
    ai_manager._do_start_chat()
    status = ai_manager.get_status()["chat"]
    assert status["status"] == "error"
    assert "could not be found" in status["detail"].lower()


def test_no_model_selected_produces_clear_error():
    settings_service.update({"chat_model_path": ""})
    ai_manager._do_start_chat()
    status = ai_manager.get_status()["chat"]
    assert status["status"] == "error"
    assert "no answering model" in status["detail"].lower()


# ------------------------------------------------ successful chat startup --

def test_managed_chat_server_becomes_ready(tmp_path, monkeypatch):
    fake_model = tmp_path / "model.gguf"
    fake_model.write_bytes(b"fake gguf bytes")
    settings_service.update({"chat_model_path": str(fake_model)})

    spawn_calls = []

    def fake_spawn(cmd, log_path):
        spawn_calls.append(cmd)
        return FakeProcess()

    monkeypatch.setattr(ai_manager, "_spawn", fake_spawn)
    monkeypatch.setattr(ai_manager, "_find_executable", lambda *a, **k: "/opt/llama-server")
    monkeypatch.setattr(ai_manager, "_http_ok", lambda url, timeout=1.5: "/health" in url)

    ai_manager._do_start_chat()

    status = ai_manager.get_status()["chat"]
    assert status["status"] == "running"
    assert status["managed"] is True
    assert len(spawn_calls) == 1

    s = settings_service.get_all()
    assert s["llm_base_url"].startswith("http://127.0.0.1:")
    assert s["llm_model"] == "local-model"


def test_start_ai_does_not_launch_duplicate_chat_process(tmp_path, monkeypatch):
    fake_model = tmp_path / "model.gguf"
    fake_model.write_bytes(b"fake gguf bytes")
    settings_service.update({"chat_model_path": str(fake_model)})

    spawn_calls = []

    def fake_spawn(cmd, log_path):
        spawn_calls.append(cmd)
        return FakeProcess()

    monkeypatch.setattr(ai_manager, "_spawn", fake_spawn)
    monkeypatch.setattr(ai_manager, "_find_executable", lambda *a, **k: "/opt/llama-server")
    monkeypatch.setattr(ai_manager, "_http_ok", lambda url, timeout=1.5: "/health" in url)

    # Simulate two rapid "Start AI" clicks.
    ai_manager._do_start_chat()
    ai_manager._do_start_chat()

    assert len(spawn_calls) == 1
    assert ai_manager.get_status()["chat"]["status"] == "running"


def test_chat_process_crash_reports_error_without_raising(tmp_path, monkeypatch):
    fake_model = tmp_path / "model.gguf"
    fake_model.write_bytes(b"fake gguf bytes")
    settings_service.update({"chat_model_path": str(fake_model)})

    dead_process = FakeProcess()
    dead_process.terminated = True  # looks exited the moment we check it

    monkeypatch.setattr(ai_manager, "_spawn", lambda cmd, log_path: dead_process)
    monkeypatch.setattr(ai_manager, "_find_executable", lambda *a, **k: "/opt/llama-server")
    monkeypatch.setattr(ai_manager, "_http_ok", lambda url, timeout=1.5: False)

    ai_manager._do_start_chat()  # must not raise

    status = ai_manager.get_status()["chat"]
    assert status["status"] == "error"
    assert status["detail"]  # some useful message, not blank


def test_llama_server_executable_not_found_reports_error(tmp_path, monkeypatch):
    fake_model = tmp_path / "model.gguf"
    fake_model.write_bytes(b"fake gguf bytes")
    settings_service.update({"chat_model_path": str(fake_model), "llama_server_path": ""})
    monkeypatch.setattr(ai_manager, "_find_executable", lambda *a, **k: "")

    ai_manager._do_start_chat()
    status = ai_manager.get_status()["chat"]
    assert status["status"] == "error"
    assert "llama-server" in status["detail"].lower()


# ------------------------------------------------- embedding backend -------

def test_embedding_backend_ready_when_ollama_already_running(monkeypatch):
    monkeypatch.setattr(ai_manager, "_http_ok", lambda url, timeout=1.5: True)  # already serving
    monkeypatch.setattr(
        ai_manager, "_http_json", lambda url, timeout=3.0: {"models": [{"name": "nomic-embed-text:latest"}]}
    )

    ai_manager._do_start_embedding()

    status = ai_manager.get_status()["embedding"]
    assert status["status"] == "running"
    assert status["managed"] is False  # we didn't start it -- don't claim we did

    s = settings_service.get_all()
    assert s["embedding_base_url"] == "http://127.0.0.1:11434/v1"
    assert s["embedding_model"] == "nomic-embed-text"


def test_embedding_backend_pulls_missing_model(monkeypatch):
    monkeypatch.setattr(ai_manager, "_http_ok", lambda url, timeout=1.5: True)
    monkeypatch.setattr(ai_manager, "_http_json", lambda url, timeout=3.0: {"models": []})

    pull_calls = []

    class FakeResult:
        returncode = 0

    def fake_run(cmd, **kwargs):
        pull_calls.append(cmd)
        return FakeResult()

    monkeypatch.setattr(ai_manager.subprocess, "run", fake_run)

    ai_manager._do_start_embedding()

    assert len(pull_calls) == 1
    assert "pull" in pull_calls[0]
    assert ai_manager.get_status()["embedding"]["status"] == "running"


def test_embedding_backend_launch_failure_reports_error(monkeypatch):
    monkeypatch.setattr(ai_manager, "_http_ok", lambda url, timeout=1.5: False)  # nothing running
    monkeypatch.setattr(ai_manager, "_find_executable", lambda *a, **k: "")  # ollama not found

    ai_manager._do_start_embedding()
    status = ai_manager.get_status()["embedding"]
    assert status["status"] == "error"
    assert "ollama" in status["detail"].lower()


# ------------------------------------------------------------ stop AI ------

def test_stop_ai_stops_only_managed_processes(monkeypatch):
    chat_proc = FakeProcess()
    ai_manager._processes["chat"] = chat_proc
    ai_manager._state["chat"].update(status="running", managed=True, port=1234)
    # Embedding was already running externally -- we never spawned anything for it.
    ai_manager._processes["embedding"] = None
    ai_manager._state["embedding"].update(status="running", managed=False, port=11434)

    ai_manager._stop_ai_blocking()

    assert chat_proc.terminated is True
    status = ai_manager.get_status()
    assert status["chat"]["status"] == "stopped"
    assert status["embedding"]["status"] == "stopped"
    # The embedding process handle was never touched/created -- nothing to leak or kill.
    assert ai_manager._processes["embedding"] is None


def test_stop_ai_endpoint_returns_status(client, monkeypatch):
    res = client.post("/api/ai/stop")
    assert res.status_code == 200
    data = res.json()
    assert "chat" in data and "embedding" in data


# ------------------------------------------------------ reindex interplay --

def _make_ready_document(client, monkeypatch, sample_txt_bytes):
    from app.services import llm_client
    from tests.fakes import fake_embed_texts

    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    res = client.post("/api/devices", data={"name": "AI Manager Test Device"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    deadline = time.time() + 5
    while time.time() < deadline:
        status = client.get(f"/api/documents/{doc_id}/status").json()
        if status["status"] in ("ready", "error"):
            break
        time.sleep(0.05)
    return device_id, doc_id


def test_starting_chat_model_does_not_mark_manuals_stale(client, monkeypatch, sample_txt_bytes, tmp_path):
    device_id, doc_id = _make_ready_document(client, monkeypatch, sample_txt_bytes)
    status_before = client.get(f"/api/documents/{doc_id}/status").json()
    assert status_before["status"] == "ready"

    fake_model = tmp_path / "model.gguf"
    fake_model.write_bytes(b"fake")
    settings_service.update({"chat_model_path": str(fake_model)})
    monkeypatch.setattr(ai_manager, "_spawn", lambda cmd, log_path: FakeProcess())
    monkeypatch.setattr(ai_manager, "_find_executable", lambda *a, **k: "/opt/llama-server")
    monkeypatch.setattr(ai_manager, "_http_ok", lambda url, timeout=1.5: "/health" in url)

    ai_manager._do_start_chat()
    assert ai_manager.get_status()["chat"]["status"] == "running"

    status_after = client.get(f"/api/documents/{doc_id}/status").json()
    assert status_after["status"] == "ready"


def test_starting_embedding_backend_marks_ready_manuals_needs_reindex(client, monkeypatch, sample_txt_bytes):
    # Make sure the document gets indexed under a signature that's
    # different from where the managed backend will end up, regardless of
    # what earlier tests in this session left in the settings table.
    settings_service.update({"embedding_base_url": "http://old-server:9999/v1", "embedding_model": "old-model"})
    device_id, doc_id = _make_ready_document(client, monkeypatch, sample_txt_bytes)
    status_before = client.get(f"/api/documents/{doc_id}/status").json()
    assert status_before["status"] == "ready"

    # The managed embedding backend always ends up at this fixed local
    # address/model -- different from whatever the test document's
    # embedding_signature was recorded under -- so this should flag it stale.
    monkeypatch.setattr(ai_manager, "_http_ok", lambda url, timeout=1.5: True)
    monkeypatch.setattr(
        ai_manager, "_http_json", lambda url, timeout=3.0: {"models": [{"name": "nomic-embed-text:latest"}]}
    )

    ai_manager._do_start_embedding()

    status_after = client.get(f"/api/documents/{doc_id}/status").json()
    assert status_after["status"] == "needs_reindex"


# --------------------------------------------------- external/advanced -----

def test_external_mode_ask_flow_unaffected_by_ai_manager(client, monkeypatch, sample_txt_bytes):
    """With ai_mode='external', the classic manual-settings flow must work
    exactly as before -- ai_manager should never be invoked implicitly."""
    from app.services import llm_client
    from tests.fakes import fake_chat_e24, fake_embed_texts

    client.post("/api/ai/mode", json={"mode": "external"})
    assert settings_service.get("ai_mode") == "external"

    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(llm_client, "chat", fake_chat_e24)

    res = client.post("/api/devices", data={"name": "External Mode Device"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    deadline = time.time() + 5
    while time.time() < deadline:
        status = client.get(f"/api/documents/{doc_id}/status").json()
        if status["status"] in ("ready", "error"):
            break
        time.sleep(0.05)
    assert status["status"] == "ready"

    res = client.post(f"/api/devices/{device_id}/ask", json={"question": "What does error E24 mean?"})
    data = res.json()
    assert data["not_found"] is False
    assert "drain pump" in data["answer"].lower()


# ----------------------------------------------------------- misc/status ---

def test_ai_status_endpoint_shape(client):
    res = client.get("/api/ai/status")
    assert res.status_code == 200
    data = res.json()
    assert set(data.keys()) == {"chat", "embedding", "overall"}
    assert data["overall"] == "offline"


def test_friendly_model_name_strips_quant_suffix():
    assert ai_manager.friendly_model_name("Qwen3.5-4B-Q4_K_M.gguf") == "Qwen3.5 4B"
    assert ai_manager.friendly_model_name("llama-3.1-8b-instruct-f16.gguf") == "llama 3.1 8b instruct"


def test_validate_path_endpoint(client, tmp_path):
    real_file = tmp_path / "model.gguf"
    real_file.write_bytes(b"x" * 1024)

    res = client.post("/api/ai/validate-path", json={"path": str(real_file)})
    data = res.json()
    assert data["exists"] is True
    assert data["is_file"] is True
    assert data["size_bytes"] == 1024

    res = client.post("/api/ai/validate-path", json={"path": str(tmp_path / "nope.gguf")})
    data = res.json()
    assert data["exists"] is False
