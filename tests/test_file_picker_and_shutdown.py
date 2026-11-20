"""
Tests for:
- the host-side native file picker (app.services.file_picker + the
  /api/ai/pick-file endpoint) that lets Setup's Browse buttons return a
  real filesystem path instead of asking the user to type one
- Kayock's application shutdown stopping only the AI processes it
  itself started
"""
from fastapi.testclient import TestClient

from app.main import app
from app.services import ai_manager, file_picker
from tests.test_ai_manager import FakeProcess


# ------------------------------------------------------------ file picker --

def test_pick_file_returns_selected_absolute_path(client, monkeypatch):
    monkeypatch.setattr(
        file_picker,
        "open_file_dialog",
        lambda kind: {"available": True, "path": "/models/Qwen3.5-4B-Q4_K_M.gguf", "error": None},
    )
    res = client.post("/api/ai/pick-file", json={"kind": "chat_model"})
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is True
    assert data["path"] == "/models/Qwen3.5-4B-Q4_K_M.gguf"
    assert data["error"] is None


def test_pick_file_cancel_is_harmless(client, monkeypatch):
    # The dialog opened fine, but the user closed it / clicked Cancel.
    monkeypatch.setattr(
        file_picker, "open_file_dialog", lambda kind: {"available": True, "path": None, "error": None}
    )
    res = client.post("/api/ai/pick-file", json={"kind": "chat_model"})
    assert res.status_code == 200  # not an error -- cancelling is a normal outcome
    data = res.json()
    assert data["available"] is True
    assert data["path"] is None
    assert data["error"] is None


def test_pick_file_unavailable_falls_back_cleanly(client, monkeypatch):
    monkeypatch.setattr(
        file_picker,
        "open_file_dialog",
        lambda kind: {"available": False, "path": None, "error": "File picker not available on this system."},
    )
    res = client.post("/api/ai/pick-file", json={"kind": "chat_model"})
    assert res.status_code == 200  # a clean, informative response -- not a 500
    data = res.json()
    assert data["available"] is False
    assert data["path"] is None
    assert "not available" in data["error"].lower()


def test_pick_file_rejects_unknown_kind(client):
    res = client.post("/api/ai/pick-file", json={"kind": "something_else"})
    assert res.status_code == 400


def test_pick_file_works_for_llama_server_and_ollama_kinds(client, monkeypatch):
    monkeypatch.setattr(
        file_picker,
        "open_file_dialog",
        lambda kind: {"available": True, "path": f"/opt/{kind}", "error": None},
    )
    for kind in ("llama_server", "ollama"):
        res = client.post("/api/ai/pick-file", json={"kind": kind})
        assert res.status_code == 200
        assert res.json()["path"] == f"/opt/{kind}"


def test_real_open_file_dialog_degrades_cleanly_without_a_display():
    """No monkeypatching -- calls the real implementation. This sandbox has
    no display (and often no tkinter at all), which is exactly the
    'headless server' case the fallback exists for: it must never raise."""
    result = file_picker.open_file_dialog("chat_model")
    assert isinstance(result, dict)
    assert set(result.keys()) == {"available", "path", "error"}
    if not result["available"]:
        assert result["path"] is None
        assert result["error"]  # some explanation, not blank


# ---------------------------------------------------------------- shutdown --

def test_shutdown_stops_managed_chat_process():
    ai_manager.reset_state_for_tests()
    fake_proc = FakeProcess()
    with TestClient(app):
        ai_manager._processes["chat"] = fake_proc
        ai_manager._state["chat"].update(status="running", managed=True, port=1234)
    # The `with` block exited -- ASGI lifespan shutdown ran, which should
    # have called ai_manager.stop_ai_sync() synchronously.
    assert fake_proc.terminated is True
    assert ai_manager.get_status()["chat"]["status"] == "stopped"
    ai_manager.reset_state_for_tests()


def test_shutdown_stops_managed_embedding_process():
    ai_manager.reset_state_for_tests()
    fake_proc = FakeProcess()
    with TestClient(app):
        ai_manager._processes["embedding"] = fake_proc
        ai_manager._state["embedding"].update(status="running", managed=True, port=11434)
    assert fake_proc.terminated is True
    assert ai_manager.get_status()["embedding"]["status"] == "stopped"
    ai_manager.reset_state_for_tests()


def test_shutdown_does_not_stop_external_unmanaged_process():
    """An Ollama instance that was already running before Kayock connected
    to it (managed=False, no process handle of our own) must be left
    completely alone on shutdown."""
    ai_manager.reset_state_for_tests()
    with TestClient(app):
        # We never created a process handle for it -- exactly like the
        # real "Ollama was already running" code path.
        ai_manager._processes["embedding"] = None
        ai_manager._state["embedding"].update(status="running", managed=False, port=11434)

    # Nothing to terminate, nothing touched -- just our own tracked status
    # resets to reflect that Kayock is no longer using it.
    assert ai_manager._processes["embedding"] is None
    assert ai_manager.get_status()["embedding"]["status"] == "stopped"
    ai_manager.reset_state_for_tests()


def test_shutdown_with_nothing_running_does_not_raise():
    ai_manager.reset_state_for_tests()
    with TestClient(app):
        pass  # AI was never started this session
    assert ai_manager.get_status()["chat"]["status"] == "stopped"
    assert ai_manager.get_status()["embedding"]["status"] == "stopped"
    ai_manager.reset_state_for_tests()
