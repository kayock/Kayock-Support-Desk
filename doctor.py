#!/usr/bin/env python3
"""
Kayock Support Desk -- local environment doctor.

Prints the same diagnostics the Settings page shows (Python/venv, data
folder, GUI/file-picker availability, llama-server and Ollama discovery,
GPU presence, logs, AI status) and exits non-zero when something
required is missing, so it's usable in scripts:

    python doctor.py            # human-readable output
    python doctor.py --json     # machine-readable

Read-only: it never installs anything, never restarts services, never
changes settings.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import init_db  # noqa: E402
from app.services import diagnostics, settings_service  # noqa: E402


def main() -> int:
    # Fresh installs may not have run the app yet -- ensure the schema and
    # default settings exist (additive, same as app startup) so the AI
    # section can read settings. No user data is created or changed.
    init_db()
    settings_service.ensure_defaults()
    report = diagnostics.collect_diagnostics()
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
        return _exit_code(report)

    py = report.get("python", {})
    data = report.get("data", {})
    gui = report.get("gui", {})
    ai = report.get("ai", {})
    gpu = report.get("gpu", {})
    logs = report.get("logs", {})

    print("=" * 62)
    print("  Kayock Support Desk -- environment diagnostics")
    print("=" * 62)
    print(f"Python:            {py.get('python_version', '?')} ({'venv' if py.get('running_in_venv') else 'NO VENV'})")
    print(f"Platform:          {py.get('platform', '?')}")
    print(f"Data folder:       {data.get('data_dir', '?')} -- "
          f"{'writable' if data.get('data_dir_writable') else 'NOT WRITABLE'}"
          + (f", {data.get('disk_free_gb')} GB free" if data.get('disk_free_gb') is not None else ""))
    print(f"Logs folder:       {logs.get('log_dir', '?')}")
    for log_file in logs.get("log_files", []):
        print(f"  - {log_file['name']}: {log_file['size_bytes']} bytes")

    print(f"File picker:       {'native dialogs available' if gui.get('native_file_picker_available') else 'falls back to text fields'}")
    if not gui.get("native_file_picker_available"):
        if not gui.get("tkinter_available"):
            print("                   (install python3-tk to enable Browse... dialogs)")
        elif not gui.get("display_detected"):
            print("                   (no display detected -- headless session?)")

    print(f"llama-server:      {'FOUND' if ai.get('llama_server_found') else 'not found (set path on the AI setup page)'}")
    print(f"Ollama:            {'FOUND' if ai.get('ollama_found') else 'not found (install from ollama.com or set path)'}")
    print(f"Answering context: {ai.get('chat_context_size', '?')} tokens (configurable in Settings)")

    gpus = gpu.get("gpus_detected", [])
    if gpus:
        names = ", ".join(g.get("name") or g.get("type", "?") for g in gpus)
        print(f"GPU:               {names}")
    else:
        print("GPU:               none detected -- CPU mode is fine, just slower on big models")
    print("(Kayock never assumes a GPU; llama-server uses whatever its build provides.)")

    status = ai.get("status", {})
    overall = status.get("overall", "unknown")
    print(f"AI status:         {overall}")
    for component in ("chat", "embedding"):
        comp = status.get(component, {})
        print(f"  {component:<10} {comp.get('status', '?'):<8} {comp.get('detail', '')}")

    print("=" * 62)
    return _exit_code(report)


def _exit_code(report: dict) -> int:
    """0 when everything required is present, 1 otherwise (so scripts can
    rely on it). Missing optional pieces (tkinter, GPU) do not fail."""
    py = report.get("python", {})
    data = report.get("data", {})
    ai = report.get("ai", {})
    problems = []
    if not py.get("running_in_venv"):
        problems.append("not running inside the project's .venv")
    if not data.get("data_dir_writable"):
        problems.append("data folder is not writable")
    if not ai.get("llama_server_found"):
        problems.append("llama-server not found")
    if not ai.get("ollama_found"):
        problems.append("ollama not found")
    if problems:
        if "--json" not in sys.argv:
            print("\nProblems found:")
            for problem in problems:
                print(f"  - {problem}")
            print("\nSee docs/LINUX_SETUP.md for the setup steps.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
