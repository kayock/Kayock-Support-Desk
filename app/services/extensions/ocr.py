"""
Scanned-PDF OCR hook (optional, off by default).

Many factory manuals are pure image scans with no text layer; the core
pipeline currently rejects those with a clear "no extractable text"
error. This module lets the pipeline attempt real OCR *only when* the
user has installed the optional pieces -- nothing new is mandatory:

- the `pytesseract` Python package
- Google's `tesseract` binary on PATH (Ubuntu: `sudo apt install tesseract-ocr`)
- `pypdfium2` (renders PDF pages to images for tesseract)

ocr_available() reports exactly which piece is missing, and the failure
message shown to users names the missing piece and the fix. When the
pieces are present, ocr_pages() renders each page and returns recognized
text per page -- the pipeline treats it exactly like an embedded text
layer (same chunking, same citations, same page numbers).
"""
from __future__ import annotations

import shutil

OCR_PAGE_SCALE = 3.0  # render at ~216 DPI -- decent accuracy/speed trade-off


def ocr_available() -> tuple[bool, str]:
    """Returns (available, missing_description). Never raises and never
    installs anything."""
    missing = []
    try:
        import pytesseract  # noqa: F401

        _ = pytesseract.get_tesseract_version
    except Exception:  # noqa: BLE001
        missing.append("the 'pytesseract' package (pip install pytesseract)")
    if shutil.which("tesseract") is None:
        missing.append("the 'tesseract' binary (Ubuntu: sudo apt install tesseract-ocr)")
    try:
        import pypdfium2  # noqa: F401
    except Exception:  # noqa: BLE001
        missing.append("the 'pypdfium2' package (pip install pypdfium2)")
    if missing:
        return False, "; missing: " + "; ".join(missing)
    return True, ""


def ocr_pages(pdf_path) -> list[tuple[int, str]]:
    """OCR every page of a scanned PDF. Returns [(page_number, text)].
    Raises RuntimeError with a user-safe message when OCR isn't available
    or a page fails (the caller surfaces it as-is -- these messages are
    written to be shown to a user, never raw tracebacks); the caller
    degrades to the existing error path."""
    ok, missing = ocr_available()
    if not ok:
        raise RuntimeError(
            "This PDF has no extractable text (it is likely a scan), and OCR could not run: "
            f"{missing}. Install the missing pieces and upload the manual again, or use a "
            "text-based PDF instead."
        )
    try:
        import pypdfium2 as pdfium
        import pytesseract
        from PIL import ImageOps
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"OCR could not start (its dependencies failed to load): {exc}"
        ) from exc

    pages: list[tuple[int, str]] = []
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=OCR_PAGE_SCALE)
            image = bitmap.to_pil().convert("L")
            image = ImageOps.autocontrast(image)
            try:
                text = pytesseract.image_to_string(image) or ""
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"OCR failed on page {i + 1}: {exc}") from exc
            pages.append((i + 1, text))
    finally:
        pdf.close()
    return pages
