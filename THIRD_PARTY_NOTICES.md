# Third-Party Notices

This file documents third-party code and assets that ship **with** Kayock
Support Desk, and the Python packages that are installed **at runtime**
(they are not bundled in this repository or its release archive).

Kayock Support Desk itself is licensed under the MIT License — see
[`LICENSE`](LICENSE). Nothing in this file relicenses any third-party work;
each component keeps its own license and copyright.

---

## 1. Components bundled in this repository

### PDF.js (Mozilla)

- **Location:** `app/static/vendor/pdfjs/pdf.min.js`, `pdf.worker.min.js`
- **Version bundled:** pdfjs-dist 3.11.174
- **License:** Apache License 2.0
- **Copyright:** Copyright © Mozilla and individual contributors
- **Source project:** <https://github.com/mozilla/pdf.js>
- **License copy:** the complete, unmodified Apache-2.0 license text that
  ships with pdfjs-dist is preserved at
  `app/static/vendor/pdfjs/LICENSE.pdfjs.txt` — it must remain next to the
  bundled files in any redistribution.
- **Why bundled:** the manual viewer must work fully offline (no CDN).
- Apache-2.0 requires keeping the license text and notice with the
  redistributable files; the files are otherwise unmodified upstream
  builds.

### Project icons and screenshots

- `app/static/icons/icon-192.png`, `icon-512.png`, and the images under
  `docs/screenshots/` were generated for this project (drawn with Pillow /
  captured from this application's own interface). They are part of the
  project's MIT-licensed assets; no third-party font, icon set, or artwork
  is bundled.

No other third-party JavaScript, fonts, or binary assets are included.

---

## 2. Python packages installed at runtime (not bundled)

These are declared in `requirements.txt` (and, optionally,
`requirements-ocr.txt`) and are downloaded from PyPI when the user (or the
launcher) installs Kayock. They are **not** distributed with this project,
so their licenses are obligations between the user and those projects, not
redistribution obligations of this repository. Licenses listed here were
read from each package's own metadata/license file at audit time.

### Core application

| Package | Role | License |
|---|---|---|
| fastapi | web framework | MIT |
| starlette | ASGI toolkit (FastAPI dependency) | BSD-3-Clause |
| uvicorn | ASGI server | BSD-3-Clause |
| jinja2 | HTML templating | BSD-3-Clause |
| python-multipart | multipart form parsing | Apache-2.0 |
| pypdf | PDF text extraction | BSD-3-Clause |
| chromadb | local vector database | Apache-2.0 |
| langchain-text-splitters | text chunking helper | MIT |
| openai | OpenAI-compatible API client | Apache-2.0 |
| qrcode | QR code generation (LAN access) | BSD-3-Clause |
| pillow | image processing (icons, QR PNGs) | MIT-CMU |
| python-dotenv | `.env` support | BSD-3-Clause |

### Optional OCR extras (`requirements-ocr.txt`, install separately)

| Package | Role | License |
|---|---|---|
| pytesseract | Python wrapper for tesseract | Apache-2.0 |
| pypdfium2 | PDF rendering for OCR | BSD-3-Clause / Apache-2.0 (bundled binaries carry their own dependency licenses) |

OCR additionally requires the **Tesseract** engine binary (Apache-2.0,
<https://github.com/tesseract-ocr/tesseract>) installed by the user — on
Ubuntu: `sudo apt install tesseract-ocr`. Kayock never bundles it.

### Test-only packages (`requirements-dev.txt`, not a runtime dependency)

| Package | License |
|---|---|
| pytest | MIT |
| httpx | BSD-3-Clause |
| fpdf2 | LGPL-3.0 (used only by test fixtures to build sample PDFs; the running application does not depend on it) |

### Note on transitive dependencies

The packages above pull in further transitive dependencies (e.g. pydantic,
anyio, numpy, onnxruntime for Chroma), each under its own license (MIT /
PSF-2.0 / BSD-3-Clause / Apache-2.0 family). Because they are resolved at
install time rather than pinned-and-distributed here, consult
`pip show <package>` for the exact license of whatever version is
installed.

---

## 3. Model weights, manuals, and data

- **Model weights** (GGUF chat models, Ollama's `nomic-embed-text`) are
  **never** bundled or distributed with this project; the user supplies
  them and they remain governed by their own licenses/terms.
- **Manuals and user data** stay in the local `data/` folder and are the
  user's property. No copyrighted factory manual, model weight, or real
  user database is included in this repository or its release archive.
