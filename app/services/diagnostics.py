"""
System diagnostics for Kayock Support Desk.

Collects a plain-JSON snapshot of the local environment: Python/venv,
data-directory writability and disk space, GUI availability (tkinter /
display), managed-AI executable discovery (llama-server, Ollama), GPU
presence (reported only -- never assumed), log files, and the AI status.
Used by the Settings page's diagnostics panel and by doctor.py on the CLI.

Deliberately read-only: it never installs anything, never restarts
services, and never changes settings. GPU detection is informational --
Kayock never passes GPU-specific flags to llama-server unless the user
adds them via the llama_server_extra_args setting, so CPU-only and GPU
machines work with identical configuration.
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
import sysconfig
from pathlib import Path

from app.config import DATA_DIR, DOCUMENTS_DIR, LLAMA_SERVER_CONTEXT_SIZE, LOGS_DIR, ensure_data_dirs
from app.services import ai_manager


def _python_info() -> dict:
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    return {
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "running_in_venv": in_venv,
        "platform": f"{platform.system()} {platform.release()}",
    }


def _data_dir_info() -> dict:
    ensure_data_dirs()
    writable = os.access(DATA_DIR, os.W_OK)
    # Actually write a probe file -- os.access alone can lie on some mounts.
    probe = DATA_DIR / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        writable = True
    except OSError:
        writable = False
    free_bytes = None
    try:
        free_bytes = shutil.disk_usage(DATA_DIR).free
    except OSError:
        pass
    return {
        "data_dir": str(DATA_DIR),
        "data_dir_writable": writable,
        "documents_dir": str(DOCUMENTS_DIR),
        "log_dir": str(LOGS_DIR),
        "disk_free_bytes": free_bytes,
        "disk_free_gb": round(free_bytes / 1e9, 1) if free_bytes is not None else None,
    }


def _gui_info() -> dict:
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    tkinter_ok = False
    try:
        import tkinter  # noqa: F401

        tkinter_ok = True
    except Exception:  # noqa: BLE001 -- ImportError and headless Tcl failures both count
        pass
    browser_tools = [t for t in ("xdg-open", "gio", "gnome-open", "kde-open", "open") if shutil.which(t)]
    return {
        "display_detected": has_display,
        "tkinter_available": tkinter_ok,
        "native_file_picker_available": tkinter_ok and has_display,
        "browser_open_tools": browser_tools,
    }


def _gpu_info() -> dict:
    """Reports what GPU runtimes exist on the machine. Purely informational:
    Kayock never hard-codes GPU assumptions -- llama-server picks its own
    acceleration, and users can pass extra flags via llama_server_extra_args."""
    gpus: list[dict] = []
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            import subprocess

            result = subprocess.run(
                [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    name, _, mem = line.partition(",")
                    gpus.append({"type": "nvidia", "name": name.strip(), "memory": mem.strip()})
        except Exception:  # noqa: BLE001
            gpus.append({"type": "nvidia", "name": "present (nvidia-smi failed to query)"})
    dri_dir = Path("/dev/dri")
    if dri_dir.exists():
        render_nodes = [p.name for p in dri_dir.iterdir() if p.name.startswith("renderD")]
        if render_nodes:
            gpus.append({"type": "drm_render_node", "nodes": render_nodes})
    return {
        "gpus_detected": gpus,
        "note": "llama-server chooses CPU/GPU itself; pass extra flags (e.g. --n-gpu-layers) "
        "via the llama_server_extra_args setting if your build needs one.",
    }


def _log_info() -> dict:
    ensure_data_dirs()
    logs = []
    for name in ("chat_server.log", "embedding_server.log", "app.log"):
        path = LOGS_DIR / name
        if path.exists():
            logs.append({"name": name, "path": str(path), "size_bytes": path.stat().st_size})
    return {"log_files": logs, "log_dir": str(LOGS_DIR)}


def collect_diagnostics() -> dict:
    """Full diagnostics snapshot. Never raises: every section fails
    independently to a best-effort value."""
    result: dict = {}
    try:
        result["python"] = _python_info()
    except Exception as exc:  # noqa: BLE001
        result["python"] = {"error": str(exc)}
    try:
        result["data"] = _data_dir_info()
    except Exception as exc:  # noqa: BLE001
        result["data"] = {"error": str(exc)}
    try:
        result["gui"] = _gui_info()
    except Exception as exc:  # noqa: BLE001
        result["gui"] = {"error": str(exc)}
    try:
        result["gpu"] = _gpu_info()
    except Exception as exc:  # noqa: BLE001
        result["gpu"] = {"error": str(exc)}
    try:
        result["ai"] = {
            "status": ai_manager.get_status(),
            "llama_server_found": ai_manager.find_executable("chat") != "",
            "ollama_found": ai_manager.find_executable("embedding") != "",
            "chat_context_size": ai_manager.chat_context_size(),
        }
    except Exception as exc:  # noqa: BLE001
        result["ai"] = {"error": str(exc)}
    try:
        result["logs"] = _log_info()
    except Exception as exc:  # noqa: BLE001
        result["logs"] = {"error": str(exc)}
    return result
