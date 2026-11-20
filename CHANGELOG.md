# Changelog

All notable changes to Kayock Support Desk are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-11-20

First public open-source release (MIT). Includes everything below, built on
the original single-pass application.

### Added

- **Linux support (Ubuntu-first):** hardened `Start-Linux.sh`, `doctor.py`
  environment diagnostics (CLI and Settings page), full
  [Linux setup guide](docs/LINUX_SETUP.md). Windows and macOS launchers
  unchanged.
- **Hybrid retrieval:** SQLite FTS5/BM25 keyword index over a text mirror
  of indexed chunks (LIKE fallback when FTS5 is unavailable), fused with
  Chroma semantic search by reciprocal rank fusion; embedding-server
  outages degrade to keyword-only retrieval instead of failing questions.
- **Section- and table-aware PDF chunking:** heading-path attribution on
  chunks and citations; fuse/spec tables never split mid-row.
- **Local PDF viewer:** bundled pdf.js (Apache-2.0, no CDN), click-to-page
  citations with best-effort text highlighting.
- **Saved chat sessions** per device in SQLite, with follow-up context
  resolution (previous user turn feeds the retrieval query), a
  configurable history budget, and fresh retrieval/verification every turn.
- **Structured diagnostic log:** symptoms, tested steps, measured readings,
  ruled-out causes, outcomes.
- **Structured specifications:** torque, fluid, capacity, fuse, and
  schedule extraction into searchable tables with verified-vs-candidate
  separation and configurable document preference across manuals.
- **Persistent job queue** for indexing/reindexing: progress, retries,
  cancellation, and crash recovery on restart; errored documents are
  retryable without re-uploading.
- **Structured local logging** (`data/logs/app.log`), usage metrics, and a
  system diagnostics snapshot in Settings.
- **Interface:** dark mode (auto/light/dark), compact technician layout,
  keyboard navigation, answer+citations Markdown export, installable PWA
  with clearly labeled cached pages.
- **Optional OCR** for scanned PDFs (`requirements-ocr.txt` + tesseract).
- **Extension seams** for future OBD adapters and additional AI backends
  (`app/services/extensions/`).
- **Open-source release files:** MIT `LICENSE`, `THIRD_PARTY_NOTICES.md`,
  `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue and pull
  request templates, and an end-to-end smoke test
  (`scripts/smoke_test.py`) with a demo model server
  (`scripts/fake_model_server.py`).

### Fixed

- Conversational retrieval no longer treats the current question as
  "previous context": the retrieval query and conversation history are
  built from the pre-insert session snapshot, so each turn stores the
  question exactly once and follow-ups resolve against the real prior turn.
- Semantic (vector) retrieval now embeds the same contextualized retrieval
  query that keyword search uses, instead of the raw question.
- Retrieval-context resolution no longer depends on the model history
  budget (a small budget no longer breaks "what about the fill plug?"
  follow-ups).
- Heading detection no longer misclassifies digit-bearing value lines
  (exposed by OCR'd manuals) as section headings, which had produced empty
  sections.

[1.0.0]: https://keepachangelog.com/en/1.1.0/
