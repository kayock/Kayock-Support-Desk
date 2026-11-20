"""
Opens a native "choose a file" dialog on the machine running Kayock, so
the first-run setup screen's Browse buttons actually hand back a real
filesystem path instead of asking the user to type one.

This only makes sense because Kayock is a locally-hosted app: the
browser and the server are (in the normal use case) the same computer.
No file is uploaded or copied here -- only the path the user picked is
returned.

Uses tkinter's built-in file dialog (ships with the standard Windows
Python installer, no extra dependency). If tkinter isn't available, or a
dialog genuinely can't be opened (headless server, no display, some
Linux configurations, etc.), this degrades cleanly: callers get back
`available: False` and the setup page falls back to its plain text
field with a short explanation, rather than the app crashing or hanging.
"""
from __future__ import annotations

import sys

FILE_KINDS = {
    "chat_model": {
        "title": "Choose an answering model (GGUF file)",
        "filetypes": [("GGUF models", "*.gguf"), ("All files", "*.*")],
    },
    "llama_server": {
        "title": "Choose the llama-server executable",
        "filetypes": ([("Executable files", "*.exe"), ("All files", "*.*")] if sys.platform == "win32" else [("All files", "*.*")]),
    },
    "ollama": {
        "title": "Choose the Ollama executable",
        "filetypes": ([("Executable files", "*.exe"), ("All files", "*.*")] if sys.platform == "win32" else [("All files", "*.*")]),
    },
}


def open_file_dialog(kind: str) -> dict:
    """Returns {"available": bool, "path": str | None, "error": str | None}.

    - available=True, path=<str>: the user picked a file.
    - available=True, path=None: the dialog opened fine but the user
      cancelled -- not an error, just nothing chosen.
    - available=False: no dialog could be opened at all (no tkinter, no
      display, etc.) -- the caller should fall back to a text field.
    """
    spec = FILE_KINDS.get(kind)
    if spec is None:
        return {"available": False, "path": None, "error": f"Unknown picker kind: {kind}"}

    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        return {"available": False, "path": None, "error": f"File picker not available on this system ({exc})."}

    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass  # best-effort; not all platforms support this attribute
        chosen = filedialog.askopenfilename(title=spec["title"], filetypes=spec["filetypes"])
        root.destroy()
    except Exception as exc:  # noqa: BLE001 -- any GUI/display failure degrades cleanly
        return {"available": False, "path": None, "error": f"Could not open the file picker ({exc})."}

    return {"available": True, "path": chosen or None, "error": None}
