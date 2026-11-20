"""
Modular extension boundaries for future hardware/input integrations.

Each submodule here is an isolated seam the core app can call without
taking on a hard dependency:

- obd: vehicle OBD adapter interface (serial/BT/USB scanners). No adapter
  ships yet -- get_adapter() returns NullAdapter and the app behaves as
  before (manual code entry only), but a real adapter can be dropped in
  behind the same interface.
- ocr: scanned-PDF OCR hook. Off unless the OPTIONAL dependencies are
  installed (pypdfium2 + pytesseract + the tesseract binary); the core
  indexing pipeline calls it only when a PDF has no text layer, and
  degrades to the existing clear error message otherwise.

Ground rules for anything added here: optional dependency only, no cloud
services, no accounts, no community packs as prerequisites, and a clean
"not available" path that the UI presents honestly.
"""
