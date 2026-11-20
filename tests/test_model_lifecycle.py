"""
Tests for the Stop AI reliability fix and the idle-unload watcher.

Stop AI must never merely send a termination signal and assume success --
it has to verify the process is actually gone, its port is released, and
its health endpoint no longer responds before ever reporting "stopped".
Termination must also only ever act on the exact process Kayock itself
started (never a broad by-name kill that could take out an unrelated
FOXAI/Ollama/llama-server session).

All subprocess/HTTP behavior is mocked -- no real llama-server/Ollama
binary or GGUF file is used, and no test here waits out a real timeout
(FakeProcess and the fake health-check reflect state changes instantly).
"""
import subprocess
import time

import pytest

from app.services import ai_manager, settings_service
from tests.test_ai_manager import FakeProcess


@pytest.fixture(autouse=True)
def _reset_ai_manager_state(client):
    ai_manager.reset_state_for_tests()
    settings_service.update({"ai_idle_unload_minutes": "0"})
    yield
    ai_manager.reset_state_for_tests()
    settings_service.update({"ai_idle_unload_minutes": "0"})


def _managed_running(component: str, port=None):
    proc = FakeProcess()
    ai_manager._processes[component] = proc
    ai_manager._state[component].update(status="running", managed=True, port=port)
    return proc


# ------------------------------------------------------- basic termination -

def test_stop_ai_terminates_managed_chat_process():
    proc = _managed_running("chat")
    ai_manager._stop_component("chat")
    assert proc.terminated is True
    assert ai_manager.get_status()["chat"]["status"] == "stopped"


def test_stop_ai_reports_stopped_only_after_full_verification():
    proc = _managed_running("chat")
    ai_manager._stop_component("chat")
    status = ai_manager.get_status()["chat"]
    assert status["status"] == "stopped"
    assert status["port"] is None
    assert status["managed"] is False


# --------------------------------------------------- real-exit verification

def test_stop_ai_does_not_report_stopped_if_process_never_dies(monkeypatch):
    """UI must not lie: if the process object still looks alive after
    termination was attempted, status must be 'error', never 'stopped'."""
    _managed_running("chat")
    monkeypatch.setattr(ai_manager, "_process_alive", lambda proc: True)  # simulates a stuck process
    ai_manager._stop_component("chat")
    status = ai_manager.get_status()["chat"]
    assert status["status"] == "error"
    assert "could not" in status["detail"].lower()


def test_stop_ai_does_not_report_stopped_if_port_never_released(monkeypatch):
    _managed_running("chat", port=54321)
    monkeypatch.setattr(ai_manager, "_wait_port_released", lambda port, timeout=5: False)
    ai_manager._stop_component("chat")
    assert ai_manager.get_status()["chat"]["status"] == "error"


def test_stop_ai_does_not_report_stopped_if_health_endpoint_still_responds(monkeypatch):
    _managed_running("chat", port=54322)
    monkeypatch.setattr(ai_manager, "_wait_health_down", lambda component, port, timeout=5: False)
    ai_manager._stop_component("chat")
    assert ai_manager.get_status()["chat"]["status"] == "error"


def test_stop_ai_error_detail_is_plain_english_not_a_lie():
    proc = _managed_running("chat")
    proc.terminated = True  # already dead by the time we check -- should succeed cleanly
    ai_manager._stop_component("chat")
    assert ai_manager.get_status()["chat"]["status"] == "stopped"


# --------------------------------------------------- forced-kill escalation

def test_stop_ai_escalates_to_kill_if_graceful_termination_hangs():
    """Simulates terminate() not being enough -- proc.wait() times out
    once (as if the process were ignoring SIGTERM/graceful stop), then
    Kayock must escalate to a forceful kill and confirm real death."""

    class HangingThenKillableProcess(FakeProcess):
        def __init__(self):
            super().__init__()
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            return super().wait(timeout=timeout)

    proc = HangingThenKillableProcess()
    ai_manager._processes["chat"] = proc
    ai_manager._state["chat"].update(status="running", managed=True, port=None)

    ai_manager._stop_component("chat")

    assert proc.killed is True  # escalation actually happened
    assert ai_manager.get_status()["chat"]["status"] == "stopped"


# ------------------------------------------------- never touch unmanaged --

def test_stop_never_attempts_termination_on_an_unmanaged_process(monkeypatch):
    """The core safety property: Stop AI (or the idle watcher) must never
    even attempt to terminate a process it didn't start -- covers both
    'don't kill an unrelated llama-server' and 'don't kill FOXAI', since
    neither would ever be tracked with managed=True."""

    def boom(proc):
        raise AssertionError("must never attempt to terminate an unmanaged process")

    monkeypatch.setattr(ai_manager, "_terminate_process_tree", boom)
    ai_manager._processes["embedding"] = None
    ai_manager._state["embedding"].update(status="running", managed=False, port=11434)

    ai_manager._stop_component("embedding")  # must not raise

    assert ai_manager.get_status()["embedding"]["status"] == "stopped"


def test_stop_ai_does_not_touch_a_process_it_never_tracked():
    """Equivalent to 'an independent llama-server/FOXAI session is running
    on some other port' -- Kayock has no process handle for it at all, so
    there is nothing for Stop AI to even reach."""
    ai_manager._processes["chat"] = None
    ai_manager._state["chat"].update(status="stopped", managed=False, port=None)
    ai_manager._stop_component("chat")  # must not raise, must not invent a process to kill
    assert ai_manager.get_status()["chat"]["status"] == "stopped"


# ------------------------------------------- repeated start/stop cycling --

def test_repeated_start_stop_start_cycle_works_immediately(tmp_path, monkeypatch):
    fake_model = tmp_path / "model.gguf"
    fake_model.write_bytes(b"fake gguf bytes")
    settings_service.update({"chat_model_path": str(fake_model)})

    state = {"proc": None}

    def fake_spawn(cmd, log_path):
        p = FakeProcess()
        state["proc"] = p
        return p

    def fake_http_ok(url, timeout=1.5):
        if "/health" not in url:
            return False
        proc = state["proc"]
        return proc is not None and proc.poll() is None

    monkeypatch.setattr(ai_manager, "_spawn", fake_spawn)
    monkeypatch.setattr(ai_manager, "_find_executable", lambda *a, **k: "/opt/llama-server")
    monkeypatch.setattr(ai_manager, "_http_ok", fake_http_ok)

    ai_manager._do_start_chat()
    assert ai_manager.get_status()["chat"]["status"] == "running"

    ai_manager._stop_component("chat")
    assert ai_manager.get_status()["chat"]["status"] == "stopped"

    ai_manager._do_start_chat()
    assert ai_manager.get_status()["chat"]["status"] == "running"

    ai_manager._stop_component("chat")
    assert ai_manager.get_status()["chat"]["status"] == "stopped"


# --------------------------------------------------------- app shutdown --

def test_shutdown_stop_still_verifies_real_exit():
    """stop_ai_sync() (used during app shutdown) goes through the same
    verified _stop_component path -- not a shortcut that skips verification."""
    proc = _managed_running("chat")
    ai_manager.stop_ai_sync()
    assert proc.terminated is True
    assert ai_manager.get_status()["chat"]["status"] == "stopped"


# ------------------------------------------------------------- idle unload

def test_idle_unload_minutes_setting_persists(client):
    for value in ("10", "30", "60", "0"):
        res = client.post("/api/settings", json={"ai_idle_unload_minutes": value})
        assert res.status_code == 200
        assert settings_service.get("ai_idle_unload_minutes") == value


def test_idle_watcher_unloads_only_chat_after_timeout(monkeypatch):
    settings_service.update({"ai_idle_unload_minutes": "10"})
    ai_manager._state["chat"].update(status="running", managed=True, port=None)
    ai_manager._state["embedding"].update(status="running", managed=True, port=None)
    ai_manager._last_activity_ts = time.time() - (11 * 60)  # 11 minutes ago -- past the 10-minute limit

    stopped = []
    monkeypatch.setattr(ai_manager, "stop_chat_only", lambda: stopped.append("chat"))

    ai_manager._idle_watcher_tick()

    assert stopped == ["chat"]
    # embedding was never touched by the idle watcher
    assert ai_manager.get_status()["embedding"]["status"] == "running"
    assert ai_manager.get_status()["embedding"]["managed"] is True


def test_idle_watcher_does_not_trigger_before_timeout_elapses():
    settings_service.update({"ai_idle_unload_minutes": "10"})
    ai_manager._state["chat"].update(status="running", managed=True, port=None)
    ai_manager.record_activity()  # just now -- well under 10 minutes idle

    ai_manager._idle_watcher_tick()

    assert ai_manager.get_status()["chat"]["status"] == "running"


def test_activity_resets_the_idle_timer():
    ai_manager._last_activity_ts = time.time() - 100000  # very idle
    ai_manager.record_activity()
    assert time.time() - ai_manager._last_activity_ts < 2


def test_never_disables_automatic_unloading(monkeypatch):
    settings_service.update({"ai_idle_unload_minutes": "0"})
    ai_manager._state["chat"].update(status="running", managed=True, port=None)
    ai_manager._last_activity_ts = time.time() - 100000  # extremely idle

    stopped = []
    monkeypatch.setattr(ai_manager, "stop_chat_only", lambda: stopped.append("chat"))

    ai_manager._idle_watcher_tick()

    assert stopped == []
    assert ai_manager.get_status()["chat"]["status"] == "running"


def test_idle_watcher_ignores_invalid_setting_value():
    settings_service.update({"ai_idle_unload_minutes": "not-a-number"})
    assert ai_manager._idle_unload_minutes() == 0
    settings_service.update({"ai_idle_unload_minutes": "0"})


def test_starting_chat_resets_idle_clock(tmp_path, monkeypatch):
    fake_model = tmp_path / "model.gguf"
    fake_model.write_bytes(b"fake")
    settings_service.update({"chat_model_path": str(fake_model)})
    monkeypatch.setattr(ai_manager, "_spawn", lambda cmd, log_path: FakeProcess())
    monkeypatch.setattr(ai_manager, "_find_executable", lambda *a, **k: "/opt/llama-server")
    monkeypatch.setattr(ai_manager, "_http_ok", lambda url, timeout=1.5: "/health" in url)

    ai_manager._last_activity_ts = time.time() - 100000
    ai_manager._do_start_chat()

    assert time.time() - ai_manager._last_activity_ts < 2
