# Kayock Support Desk — Implementation & Release Report

**Release date:** 2026-11-20 · **Platform tested:** Linux x86_64 (Ubuntu-family), Python 3.12

This report covers (1) the eight-stage application upgrade that brought Kayock
Support Desk to its current feature set, (2) the targeted conversational-retrieval
repairs, and (3) the open-source release preparation. Historical context: the
original development pass reported 181 passing tests before any upgrade work;
each stage below was verified against the suite as it landed.

## 1. Application upgrade (eight stages) — implemented & tested

| # | Stage | Result |
|---|---|---|
| 1 | Linux support | Hardened `Start-Linux.sh` (version/venv/tkinter/display checks; no system installs or service changes), `doctor.py` CLI + `/api/diagnostics`, configurable `llm_context_size` (default unchanged at 4096, never silently reduced) and `llama_server_extra_args` (explicit GPU flags only — none assumed). Windows/macOS launchers untouched. |
| 2 | Hybrid retrieval | `text_index.py`: chunks table + SQLite FTS5/BM25 (LIKE fallback), reciprocal-rank fusion with Chroma, exact-phrase reranking on top; embedding outage degrades to keyword-only answers (metric-tracked); section- and table-aware chunking; one-time backfill from Chroma for pre-upgrade documents (no re-upload/re-embed). |
| 3 | Citations | Bundled pdf.js viewer (Apache-2.0 license preserved, no CDN), click-to-page citations with best-effort highlighting and explicit "never proves a value" disclaimer; manuals stay local. |
| 4 | Conversations | Per-device SQLite chat sessions with citations per message; follow-up wording resolved via the previous turn while retrieval/verification run fresh; structured diagnostic log (symptoms/steps/readings/ruled-out/outcomes). |
| 5 | Structured specs | `spec_values` extraction (torque/fluid/capacity/fuse/schedule) with document/page/section/excerpt on every row; verified-vs-candidate separation using the answer pipeline's evidence bar; configurable multi-manual preference; additive re-extraction. |
| 6 | Reliability | Persistent SQLite job queue (atomic claims, progress, retries, cooperative cancellation restoring prior document status, crash recovery re-queueing 'running' jobs); errored documents retryable without re-upload; rotating `app.log`; metrics; Settings diagnostics/jobs/metrics panels. |
| 7 | Interface | Dark mode (auto/light/dark), compact technician layout, keyboard shortcuts, answer+citations Markdown export, PWA with honest offline policy (cached pages banner-labeled; API/AI never cached or faked). |
| 8 | Extensions | OBD adapter interface with honest NullAdapter; optional OCR hook (pytesseract + pypdfium2 + tesseract) — live-tested with real tesseract on an image-only PDF through the full pipeline. |

## 2. Conversational-retrieval repairs (this release)

Two targeted bugs, found by reviewing the retrieval data flow and confirmed by
new input-capturing regression tests:

1. **`chat_service.ask_in_session` stored the user's question before building
   the retrieval context.** The "previous user message" lookup then saw the
   current question itself, so follow-up context resolution silently never
   happened. Fix: the session is snapshotted before any write; the retrieval
   query and history are built from that pre-insert snapshot; the question is
   recorded exactly once per turn.
2. **`query_service.answer_question` embedded the raw question for semantic
   search while keyword search used the contextualized query.** Fix: both
   surfaces now receive the same contextualized retrieval query; the original
   question still drives reranking, prompts, and verification.
3. Hardening included in the fix: retrieval-context resolution no longer
   depends on the model history budget (a small `chat_history_max_messages`
   no longer breaks follow-ups — the budget bounds only what the model sees),
   and the heading detector no longer misclassifies digit-bearing value lines
   as section headings (exposed by OCR testing).

**Regression tests** (`tests/test_chat_retrieval_regression.py`) assert the
actual retrieval inputs — embedded text, vector-query device scoping, keyword
query strings — not just answer wording: prior-turn context present exactly
once, current question never duplicated, first-turn unchanged, device/session
isolation, compliant-model unverified-spec rejection plus citation gating
under a prompt-violating model, and keyword-only retrieval during an
embedding outage with the contextualized query.

## 3. Open-source release preparation

- **LICENSE:** MIT, with the copyright line set to "Kayock Support Desk
  project contributors" — a placeholder the owner must confirm (no personal
  names or repository URL were invented).
- **THIRD_PARTY_NOTICES.md:** bundled pdf.js (Apache-2.0, unmodified license
  preserved at `app/static/vendor/pdfjs/LICENSE.pdfjs.txt`) documented
  separately from runtime-installed Python packages, whose licenses were read
  from installed package metadata (MIT / BSD-3-Clause / Apache-2.0 /
  MIT-CMU / PSF-2.0 / LGPL-3.0 [test-only fpdf2]); icons/screenshots are
  project-generated; model weights and manuals are never bundled.
- **Documentation:** rewritten `README.md` (screenshot gallery with alt text,
  per-OS setup, honest privacy/network behavior, limitations), detailed
  operations write-up preserved at `docs/OPERATIONS.md`,
  `docs/LINUX_SETUP.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  `SECURITY.md` (no invented private contact — flagged for the owner to add
  one), `CHANGELOG.md`, GitHub issue/PR templates, and a reviewed
  `.gitignore` (excludes `data/`, `.venv/`, model files like `*.gguf`,
  `.env`; keeps source, tests, docs, and screenshots).
- **Screenshots:** ten real captures from the running application
  (`docs/screenshots/01…10`, consistent 1280×900 desktop viewport plus one
  375 px mobile shot), optimized (780 KB → 320 KB total, palette-quantized,
  legibility preserved). Synthetic demo data only (fake vehicle + appliance,
  no real VINs, IPs, or paths in frame). Captured via headless browser
  automation against the real app + `scripts/fake_model_server.py`.
- **Release archive:** `kayock-support-desk-open-source.zip` contains the
  complete source, tests, launchers, docs, license, notices, and screenshots;
  `.venv/`, `data/`, caches, model files, and real user data are excluded.
  A clean-extraction install test was performed (fresh venv from the
  archive, full suite re-run inside it).

## 4. Verification (this build)

- `python -m pytest -q`: **225 passed, 2 skipped** (the two skips are
  OCR-unavailable-path tests that conditionally skip because the OCR extras
  are installed in this environment; those paths were verified before the
  extras were installed, and the OCR-available path was live-tested with
  real tesseract).
- `python scripts/smoke_test.py`: **36/36 checks passed** — real server
  process: first-run setup and honest "AI needs attention" state, upload →
  job queue → index, clean embedding-failure message → configure model
  server → reindex recovery, keyword search (P0302 / FUSE 15), specs,
  viewer, multi-turn chat, diagnostic log, diagnostics/metrics/PWA
  endpoints, SIGTERM + SIGINT shutdown with port release, restart
  persistence (sessions/specs/document status/jobs), doctor CLI.
- Clean-extraction test of the release archive: fresh extract → fresh venv →
  install → full pytest suite inside it (results below).
- Headless-browser UI verification: dashboard, device page panels, viewer
  with highlighting, dark mode, mobile 375 px (no horizontal overflow).

## 5. Not live-tested (no hardware/software available here) — verified via real-HTTP fakes and graceful failure paths

- **Real llama-server + GGUF inference** and **real Ollama + nomic-embed-text**
  (binaries/GPU not present in the build environment). The managed lifecycle
  logic (launch, health polling, stop, idle unload) is exercised by the
  offline test suite; the fake model server exercises the external
  OpenAI-compatible path over real HTTP.
- **Native GUI file dialogs** (headless environment; the text-field fallback
  was tested for real).
- **Windows and macOS platforms** — launchers are unchanged from the
  previously verified versions; only Linux was smoke-tested here.
- **Real NHTSA network calls** — the offline DTC dictionary path is tested;
  online calls use the same isolated, fail-closed client.
- **PWA installed-app experience on a real phone** — service worker and
  manifest verified as served and correctly cached; no physical device test.

## 6. Items requiring owner confirmation

1. **LICENSE copyright holder:** currently "Kayock Support Desk project
   contributors" — replace with the actual legal name(s) the owner chooses.
2. **SECURITY.md private contact:** add a real private channel (GitHub
   Private Vulnerability Reporting is recommended) when the repository goes
   public.
3. **Repository URL:** none was invented; add it when the repository exists
   (README clone command, changelog links, issue templates).
4. **GitHub repository description (suggested):** *"Local-first technical
   manual and diagnostic assistant — ask plain-language questions about your
   devices and vehicles, with verified, source-backed answers from your own
   manuals. FastAPI + SQLite + Chroma + llama-server/Ollama. Windows/Linux/macOS."*
   **Suggested topics:** `self-hosted`, `local-first`, `rag`, `pdf`, `ollama`,
   `llama-cpp`, `fastapi`, `sqlite`, `chromadb`, `diagnostics`, `manuals`,
   `offline`.
