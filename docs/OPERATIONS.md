# Kayock Support Desk

A simple, self-hosted support desk for your household stuff — appliances,
electronics, computers, vehicles, tools, whatever has a manual. Upload the
manual once, then ask plain-language questions and get answers pulled
straight from the document, with the source page shown.

> "What does error E24 mean?" → answered from your Bosch dishwasher's
> manual, with the page it came from.

Runs entirely on one computer on your home network. No cloud account,
no subscription, no data leaving your network (as long as you point it at
a local model — see below).

---

## What's new in this version

- **Linux is a first-class platform** (Ubuntu first): a hardened
  `Start-Linux.sh`, a `doctor.py` environment check, and a full
  [Linux setup guide](docs/LINUX_SETUP.md). Windows/macOS launchers are
  unchanged.
- **Hybrid retrieval**: exact keyword search (SQLite FTS5/BM25, with an
  automatic fallback when FTS5 is unavailable) is fused with the existing
  Chroma semantic search — trouble codes, fuse numbers, and part names now
  rank reliably — and an embedding-server outage degrades to keyword-only
  retrieval instead of failing the question.
- **Section- and table-aware PDF chunking**: chunks know their heading
  ("page 12, Rear Differential") and fuse-table rows are never split
  mid-row; citations can now include the section and a **click-to-page
  viewer** with best-effort text highlighting (bundled pdf.js, no CDN).
- **Saved chat sessions** per device with configurable history budget:
  follow-ups keep their meaning ("what about the fill plug?") while
  retrieval and verification still run fresh every turn.
- **Structured diagnostic log**: symptoms, tested steps, measured
  readings, and ruled-out causes, saved as plain local data.
- **Structured specifications**: torque, fluids, capacities, fuses and
  service intervals extracted into searchable tables with
  verified-vs-candidate separation and source-document preference.
- **Persistent job queue** with progress, retries, cancellation, and
  crash recovery for indexing/reindexing, plus structured local logging,
  usage metrics, and system diagnostics in Settings.
- **Interface upgrades**: dark mode (auto/light/dark), compact technician
  layout, keyboard shortcuts, answer-with-citations export, and an
  installable PWA with an honest offline story (cached pages are labeled;
  live AI answers are never faked offline).
- **Optional OCR for scanned PDFs** and clean extension seams for future
  OBD adapters and extra AI backends — nothing new is mandatory.

---

## 1. Quick start

1. **Unzip** this folder anywhere (Desktop, a NAS share, wherever).
2. **Run the launcher for your OS**:
   - Windows: double-click `Start-Windows.bat`
   - macOS: double-click `Start-Mac.command` (see note below about first-run security)
   - Linux: run `./Start-Linux.sh` in a terminal
3. The first run installs a few Python packages automatically — this takes
   a minute or two and only happens once.
4. A browser tab opens automatically once the server is ready. If it
   doesn't (e.g. no default browser configured), go to the URL printed in
   the terminal, e.g. `http://localhost:8765`.
5. **First time only:** you'll land on **Set up Kayock AI**. Click
   **Browse…** to open a normal file-selection window and pick your GGUF
   chat model (Kayock never uploads the file — only the path you picked is
   used), then click **Save & Start AI**.
6. Click **+ Add Device**, drop in a manual, confirm the details, and start
   asking questions.

**Requirement:** Python 3.10 or newer must already be installed
([python.org/downloads](https://www.python.org/downloads/)). The launcher
scripts will tell you if it's missing.

**macOS note:** the first time you double-click `Start-Mac.command`,
Gatekeeper may block it. Right-click the file → **Open** → **Open** to
approve it once.

### Kayock AI — no ports, no URLs, no terminal

Kayock manages its own local AI models. You never type an IP address, a
port, `/v1`, or a model ID.

**First run:** the **Set up Kayock AI** screen asks for one thing — the
path to a GGUF chat model file (e.g.
`C:\Models\Qwen3.5-4B-Q4_K_M.gguf`). Click **Save & Start AI**. Kayock:

1. launches `llama-server` for that model on a port it picks itself,
2. waits until it's actually ready to answer,
3. starts (or connects to) Ollama for manual search and makes sure
   `nomic-embed-text` is downloaded,
4. shows **🟢 AI Ready** once both are up.

**Every later run:** open the **AI** page (or the banner on the Devices
page if AI isn't running yet) and click **Start AI**. Kayock remembers
your model path — you don't pick it again.

You'll need two things already on your computer for the *first* setup:
- a **GGUF chat model file**, anywhere on disk (you supply the path)
- **llama-server** — Kayock looks for it on your system PATH first; if it
  can't find it, the setup screen has an "llama-server executable" field
  under its advanced disclosure
- **[Ollama](https://ollama.com)** installed, for manual-search
  (embedding) support — Kayock starts it and pulls `nomic-embed-text`
  automatically if it's missing

Without a chat model configured (or while AI is stopped), you can still
add devices and upload manuals — indexing and Q&A will just show a clear
"AI needs attention" state pointing you back to the **AI** page instead of
a confusing connection error.

**Prefer to run your own server?** See [§6](#6-advanced--external-ai-server)
— the classic manual URL/model configuration is still there, just moved
under **Settings → Advanced AI Settings**.

---

## 2. Using it from other devices (phone, tablet, laptop)

Open **Settings** in the browser — it shows:

- the LAN URL (e.g. `http://192.168.1.50:8765`)
- a QR code you can scan with a phone to open it instantly
- a friendly hostname URL (works on networks that support mDNS/Bonjour,
  e.g. `http://your-computer.local:8765`)

Any device on the same Wi-Fi/network can open that URL in a browser —
Windows, macOS, Linux, Android, iPhone/iPad, Chromebook, all fine, no app
to install.

Only the one computer running the launcher needs Python or the model
server; everything else is "just a browser."

---

## 3. Adding a device and a manual

1. Click **+ Add Device**.
2. Drag a manual (PDF, TXT, or MD) into the drop zone, or click to browse.
3. If a local model is configured, Kayock reads the first page or two and
   suggests a friendly name, manufacturer, model number, and device type.
   **Check and edit these** — auto-detection is a starting point, not
   gospel.
4. Add more manuals to the same device if you like (e.g. an owner's manual
   plus a service manual).
5. Click **Add Device**. Indexing happens in the background — you'll see a
   status pill (`pending` → `indexing` → `ready`) next to each manual on
   the device page.
6. Once a manual shows `ready`, ask it a question.

You can add more manuals to an existing device any time from its page, and
remove a manual or the whole device from there too.

---

## 4. Vehicle / backyard-mechanic features

Set a device's **Device type** to `Car`, `Truck`, `Vehicle`, `SUV`, or
similar and Kayock switches on a set of vehicle-specific tools on that
device's page. Everything below is additive — appliance/electronics
devices look and behave exactly as before.

### Vehicle profile (optional, no VIN)

Under **Vehicle details** (on Add Device or in Edit), you can fill in
Year, Make, Model, Trim, Engine, and Drivetrain — e.g. "2017 Hyundai
Tucson, 2.0L GDI, AWD". All optional, no VIN required. Factory manuals
often cover several engines/trims/years in one document; telling Kayock
which one you actually have lets it (a) include that context when asking
the model a question, and (b) push back rather than silently guessing
when a retrieved passage looks like it's for a different configuration
(e.g. a hybrid procedure showing up for a gasoline vehicle).

### OBD-II code lookup

The **OBD-II Code** box takes one or more trouble codes — `P0302`,
`p0302`, or `P0300, P0302, P0420` — normalizes them (case, whitespace),
and runs one structured, manual-scoped diagnostic answer per code:

> **Likely area** → **Check first** → **Factory procedure** → **Specifications** → sources

This is manual code entry only — there's no Bluetooth/USB scanner
integration in this pass. If the uploaded manuals don't define a code,
Kayock says so plainly rather than inventing a procedure — the same
refusal behavior as everywhere else in the app.

### Quick questions

A row of buttons (Trouble Code, Fuse/Relay, Torque Spec, Fluid/Capacity,
Maintenance, Removal Procedure, Electrical Test) just fill in the question
box with a starting prompt — they're a convenience, not a separate system;
the question still goes through the normal ask flow.

### Beginner / Experienced

A toggle on the vehicle page controls how technical the answer is:

- **Experienced** (default): factory terminology, exact DTC detail,
  connector names/pins, voltages/resistances, torque values, service
  limits, removal/installation sequences, page references.
- **Beginner**: plain language — what the part does, what symptom it
  addresses, cheapest/easiest checks first, likely tools needed, where the
  manual says the part is, what a result would mean, and a short plain
  warning when a step needs special equipment or carries real injury risk.

Both levels answer from the *same* uploaded documents — only the
presentation changes. Switching this never touches your manuals or their
index; it's a per-device preference, saved instantly.

### Manual-backed facts vs. general guidance

For vehicle devices, Kayock is instructed to keep facts it can point to in
your manual separate from any general automotive explanation it adds —
torque specs, fluid capacities, connector pins, voltages, fuse ratings,
service limits, and DTC procedures should always come from (and cite) your
documents; if no manual-backed value exists, it says so instead of
estimating one.

### Safety

When a question touches airbags/SRS, brakes, steering, fuel lines,
high-voltage hybrid/EV systems, vehicle lifting/support, or pressurized
systems, Kayock is instructed to surface whatever safety precautions the
manual itself states for that procedure, and to add one short caution in
Beginner mode when a step needs specialized equipment or carries real
injury risk — without turning every answer into a wall of warnings.

### Better retrieval for technical terms

See [§10](#10-architecture-summary) for how retrieval now favors exact
phrase/code matches (e.g. "blower motor", "P0302") over passages that only
share a generic word like "motor" — this benefits any technical query, not
just vehicles, but matters most for dense factory manuals.

### Technical specification accuracy (component + property verification)

A real test against an uploaded Hyundai Tucson factory manual surfaced a
dangerous class of error: asked for the **rear differential**'s fluid
capacity, Kayock retrieved the right general area of the manual but
answered with **7.1 L** — the automatic transaxle's ATF capacity, from a
nearby but different passage (`ATF Quantity A6MF1(4WD): 7.1L`). The number
was real; it just belonged to the wrong component. That's the kind of
mistake that's easy to miss and expensive to act on.

For exact technical specs — fluid type, capacity, torque, voltage,
resistance, pressure, fuse amperage, clearance, gap, service limit,
temperature, connector pin, fastener size — Kayock runs **two**
verification passes before answering:

1. **Component verification.** Extracts the component(s) actually being
   asked about (e.g. "rear differential", "transfer case" — from a
   modest built-in list of common component phrases plus a generic
   fallback, not a full ontology), then only treats a retrieved passage
   as authoritative for that component if the component's name appears
   in the passage's own heading/opening — not just anywhere in the
   passage.
2. **Property verification.** Correct component still isn't enough — a
   passage correctly headed "Rear Differential" can contain several
   different specs (a cover-bolt torque, a mounting-bolt torque, a
   drain-plug torque...). So within a component-verified passage, Kayock
   also extracts the *specific requested property* (drain plug torque vs.
   fill plug torque vs. resistance vs. capacity, from a modest alias map)
   and only accepts a number as evidence if the property's name appears
   near that number — same line, or the line right after (the common
   "label line, then value line" manual layout). A passage that mentions
   the component but not the requested property in proximity to a number
   is still shown to the model as general context, but is **never a
   citable source for an exact value**.

If nothing verifies both the component and the requested property,
Kayock is instructed to say the specification "could not be verified" —
never substitute a value from an adjacent bolt, plug, fluid, voltage, or
resistance under the same heading. Multiple requested properties (e.g.
"fluid type, capacity, drain torque, and fill torque") are verified
**independently** — one verified field never causes the others to be
treated as verified too. See §10 for the full mechanism,
`tests/test_spec_verification.py` for the original Hyundai-style
regression tests, and `tests/test_property_verification.py` for the
fastener/property-level regression tests (drain vs. fill vs. mounting vs.
cover-bolt torque, blower motor resistance vs. voltage, etc.). Ordinary
explanatory questions ("Where is the cabin air filter?") are
unaffected — this stricter path only activates for spec-shaped questions.

---

## 5. Online Automotive Sources

Optional. Kayock can supplement (never replace) your uploaded manuals with
a small set of official, free online lookups: decoding a VIN, checking
NHTSA safety recalls, and standard OBD-II code definitions. **Your
manuals remain the highest-priority source for anything vehicle-specific**
— torque specs, diagnostic procedures, connector pinouts, fluid
capacities, and service limits always come from what you uploaded first.

### What's queried online, and by whom

| Lookup | Provider | API key? | What's sent |
|---|---|---|---|
| VIN decode | [NHTSA vPIC](https://vpic.nhtsa.dot.gov/api/) | No | Only the VIN |
| Recalls | [NHTSA Recalls API](https://www.nhtsa.gov/nhtsa-datasets-and-apis) | No | Only year/make/model |
| Standard OBD-II code meaning | Kayock's own built-in offline reference | N/A — no network call | Nothing (works with no internet at all) |

Both NHTSA sources are official U.S. government APIs, free, with no
registration, no account, and no rate limit for normal personal use — they
were chosen specifically because they're free and official; see below for
why generic OBD-II lookup is different.

**What's never sent, no matter what:** your uploaded manuals, retrieved
manual excerpts, local file paths, your question history, your AI model
configuration, or any other device's information. A VIN decode sends
exactly one thing — the VIN. A recall check sends exactly three things —
year, make, model.

### Why standard OBD-II definitions are local, not online

Before building this, Kayock's development process specifically looked
for a free, public, no-API-key, stable HTTP API for generic OBD-II
trouble-code definitions (the "P0302 = Cylinder 2 Misfire Detected" kind
of standardized meaning). None was found — every option was either
paid, required an API key, or wasn't a stable documented service. Rather
than add a paid dependency or an unreliable one, Kayock ships a small
built-in dictionary of the ~120 most common generic codes behind the
exact same internal interface an online source would use
(`app/services/automotive_sources.lookup_dtc()`), so a suitable free API
could be swapped in later without touching anything else. This also means
generic code lookup always works, with zero latency, even with no
internet connection at all.

### Turning it on or off

Settings → **Online Automotive Sources**. One master toggle, plus
individual toggles for VIN decode and recalls specifically (there's
nothing to toggle for the offline OBD-II reference — it's not a network
call). Kayock works fully with everything toggled off; only the
**Online Vehicle Information** section of a vehicle's page changes.

### VIN privacy

The VIN field on a vehicle profile is completely optional — nothing else
in Kayock requires it. If you add one and click **Decode VIN**, only that
VIN is sent to NHTSA vPIC. The result is never applied automatically:
Kayock shows you what it found, highlights anything that conflicts with
what you already entered, and only saves what you explicitly choose to
apply (see §4).

### Offline behavior

Everything else in Kayock — uploaded manuals, OBD-II manual code entry
and search, local AI, Beginner/Experienced modes — works exactly the same
with no internet connection. The **Online Vehicle Information** section
simply shows a plain message ("turned off in Settings" or a short
connection-failure message) and never blocks or slows down the rest of
the page. Every online request uses a short timeout (6 seconds) so a
slow or unreachable public API can never freeze the app.

### Recall result limitations

NHTSA's recall API is matched by **year, make, and model** — there is no
officially documented VIN-specific recall lookup, so results are labeled
as potentially applicable to your vehicle rather than guaranteed for your
exact VIN. If NHTSA returns zero results, Kayock says exactly **"No
matching recalls were found in the current NHTSA data"** — not "your
vehicle has no recalls," since the absence of a match isn't the same
guarantee.

### Caching

VIN decodes and recall results are cached in the same SQLite database as
everything else (a new `online_cache` table — no Redis, no external
service). VIN decodes are cached for about a year (a VIN's identity
information doesn't change); recalls are cached for a week, since new
campaigns get filed over time. The recall card shows **Last checked** and
a **Check Again** link to bypass the cache and check immediately.

### Distinguishing sources in answers

An OBD-II diagnose result keeps the standard generic definition and your
manual's answer as visibly separate blocks — a **Standard OBD-II
meaning** tag next to the generic SAE definition, and a **From your
service manual** tag next to what Kayock found in your documents — so a
generic industry-standard definition is never presented as if it came
from your vehicle's manufacturer.

---

## 6. Advanced / external AI server

Prefer to run and point at your own server (a remote Ollama, LM Studio,
vLLM, a hosted OpenAI-compatible proxy)? On the **Settings** page, switch
**AI mode** to **External / Advanced Server**, then open **Advanced AI
Settings** and set, independently for chat and for embeddings:

- **Base URL** — any OpenAI-compatible endpoint
- **Model name** — nothing is hard-coded; use whatever the server exposes
- **API key** — optional; most local servers ignore it

If your model server runs on a different computer on the LAN, just point
the base URL at that machine's IP, like `http://192.168.1.20:11434/v1`.

Settings are saved immediately and take effect on the next question or
upload — no restart needed. Switching back to **Managed Local AI** mode
and clicking **Start AI** overwrites these fields with the managed
server's own address, so the two modes never conflict.

---

## 7. Multiple people, multiple installs

Each unzipped copy of this project is a fully independent installation —
its own devices, manuals, search index, and settings, all stored in its
own `data/` folder. If Eric and Tim each unzip their own copy (on their
own computers, or in separate folders), their libraries never mix.

There's no login and no multi-user support within a single installation —
it's meant to be one household's or one desk's app, LAN-accessible to
everyone who needs it.

---

## 8. Backups

Settings page → **Download backup (.zip)** downloads the entire `data/`
folder (device database, uploaded manuals, and the search index) as one
ZIP. To restore, stop the app, replace the `data/` folder with the
backup's contents, and start it again. This is also how you'd move an
installation to a new computer.

---

## 8.5 Upgrade features in detail

### Hybrid retrieval (semantic + keyword)

Retrieval now runs **two** searches per question and fuses them:

1. **Semantic** — Chroma vector search over embedded chunks (as before).
2. **Keyword** — SQLite **FTS5** (BM25 ranking) over a text mirror of
   every chunk (`data/kayock.db`, `chunks` table). If the Python build
   lacks FTS5, an automatic LIKE-scanning fallback keeps keyword search
   working (slower, same results shape).

The two ranked lists are merged by **reciprocal rank fusion**, then the
existing exact-phrase/OBD-code reranker runs on top. In practice this is
what makes `P0302`, `FUSE 15`, or an exact part name reliably retrieve
the right passage even when the embedding model would rather return a
semantically-adjacent-but-wrong one. If the embedding backend is
unreachable, retrieval **degrades to keyword-only** and the question
still gets answered (a metric in Settings records the fallback) instead
of erroring out.

`GET /api/devices/{id}/search?q=...` exposes keyword search directly
(the "Search manuals" panel on every device page) for exact lookups
without invoking the model at all.

### Section- and table-aware chunking

Chunking now recognizes section headings (numbered headings, ALL-CAPS
labels, short title lines) and tracks the heading path for every chunk
(`"Rear Differential"`, `"Interior Fuse Box > FUSE table"`). The heading
is part of the chunk text, so embeddings, keyword search, and the
component/property verification layers all see it. Consecutive table-like
lines (multi-space columns, pipes, tabs) are treated as table blocks and
split **only at row boundaries** — a fuse table is never cut between a
fuse number and its rating. Every chunk keeps page + filename + section
attribution, and citations display it.

### Click-to-page PDF viewer (fully local)

Every citation and spec row links into a locally served viewer at
`/documents/{id}/view?page=N&q=<excerpt>`:

- **pdf.js is bundled** in `app/static/vendor/pdfjs/` — no CDN, works
  fully offline.
- Opens straight at the cited page; the search term is highlighted on
  the page on a best-effort basis (an overlay over matching text items).
- The viewer always labels highlights as best-effort: **a highlight
  shows where text sits on the page — it never by itself proves that a
  value is correct**, and a failed highlight doesn't mean the source is
  wrong. The manual file itself stays on the machine, served only via
  `/api/documents/{id}/file`.

### Saved chat sessions (multi-turn Q&A)

The device page now has a **Chat sessions** panel:

- Sessions are stored per device in SQLite (`chat_sessions`,
  `chat_messages`) with every answer's citations — they survive restarts
  like everything else.
- Follow-ups keep conversational meaning: if a question is short or
  pronoun-ish ("what about the fill plug?"), the **retrieval query** is
  augmented with the previous user message so search still targets the
  right component. The model receives the recent conversation as real
  chat messages.
- **Fresh evidence every turn**: retrieval, reranking, and the
  component/property verification layers run against the current
  excerpts only. The prompt explicitly tells the model the history is
  for wording only and is never citable — so a value from turn 1 can
  never silently pass as evidence in turn 5.
- The history budget is the `chat_history_max_messages` setting
  (default 8, raisable); the model context window is the
  `llm_context_size` setting (default unchanged at 4096, raisable,
  floor 512). Neither is silently reduced.

### Structured diagnostic log

A **Diagnostic log** panel records real-world troubleshooting as
structured local data (never invented by a model): sessions with a
title and symptoms, ordered steps with what was done, the measured
reading, and a result (pass / fail / ruled out), plus an outcome when
resolved. It lives in SQLite alongside everything else and is
independent of the AI — useful as a technician's notebook and for
picking a conversation back up later.

### Structured specifications (verified vs. candidate)

Indexing now also extracts pattern-shaped values into the `spec_values`
table: **torque** (N·m / ft-lb / kgf·m), **fluid types** (SAE/API GL
gear oils, ATF, …), **capacities** (L / qt / ml), **fuse ratings** (both
prose and fuse-table rows, keyed by fuse id), and **service intervals**
(every N km/mi/months). Rules:

- Every row keeps **document, page, section, and the exact excerpt**
  it came from. Nothing is ever invented or averaged.
- Rows the extractors found by pattern alone are **candidates**; a row
  is **verified** only when the same component + property evidence bar
  used for exact-spec answers is met (component named on a label line
  in the chunk, property name next to the number). The UI shows the
  two states separately and tells you candidates are not factory
  values.
- With several manuals for one device, results are ordered by the
  `spec_document_preference` setting (default: service/factory/repair/
  workshop before owner/user) — every row still shows which document
  and page it came from.
- Re-extraction is additive (`POST /api/devices/{id}/specs/extract`);
  nothing is deleted or silently rebuilt.

### Persistent job queue, logging, metrics, diagnostics

- Indexing/reindexing run through a **SQLite-backed job queue**
  (`jobs` table): visible progress, up to `max_attempts` retries with
  the error retained, cooperative cancellation (a cancelled reindex
  restores the manual's reindexable state), and **crash recovery** —
  a job found 'running' after a restart is re-queued, never lost or
  silently dropped. Uploads and reindexes flow through it; the
  endpoints behave exactly as before.
- **Structured local logging**: `data/logs/app.log` (rotating, 5 MB × 3)
  records job outcomes and indexing failures alongside the existing
  managed-AI logs.
- **Usage metrics** (questions answered, keyword-hit usage, embedding
  fallbacks, retrieval latency) and a **System diagnostics** snapshot
  (Python/venv, data-folder writability, file-picker availability,
  llama-server/Ollama discovery, GPU presence — informational only,
  never assumed) live on the Settings page. The same snapshot is
  available on the CLI: `python doctor.py` (or `--json`).

### Interface

- **Dark mode** following the OS preference, with an override toggle in
  the toolbar (or the `t` key) and a **Follow system / Light / Dark**
  choice in Settings.
- **Compact technician layout** toggle (denser spacing, wider content)
  for workshop screens.
- **Keyboard navigation**: `/` focuses the page's question/search box,
  `t` toggles the theme, `,` / `.` page-navigate in the viewer, `?`
  shows the shortcut list.
- **Answer export**: every answer with citations gets an
  "Export answer + citations (.md)" button producing a Markdown file
  that mirrors exactly what the on-screen answer cited — nothing is
  added or upgraded by exporting.
- **Installable PWA** (`manifest.webmanifest`, service worker, icons):
  static assets and previously-visited pages are cached; a page served
  from cache offline shows a **clear "Offline — cached page" banner**,
  API/AI calls are never cached, and the worker never pretends a live
  AI query worked offline. Cached answers, when shown, are labeled.

### Optional OCR for scanned PDFs (nothing new is mandatory)

A scanned (image-only) PDF now triggers an OCR attempt **only if** you
have installed the optional pieces (`pip install -r requirements-ocr.txt`
plus the `tesseract` binary — Ubuntu: `sudo apt install tesseract-ocr`).
With them, scanned manuals index like any other PDF (same chunking,
citations, and page numbers). Without them, the existing clear error
message now names exactly what's missing and how to install it.

### Extension seams (prepared, not pretended)

`app/services/extensions/` holds clean modular boundaries for future
work: `obd.py` defines the adapter interface a live OBD scanner would
implement (today `get_adapter()` returns an honest NullAdapter — the
app still says "manual code entry only" rather than faking hardware
support), and `ocr.py` is the OCR hook described above. Additional AI
backends remain a settings-only change: `llm_client.py` reads whatever
OpenAI-compatible endpoints are configured, and `ai_manager.py`
already treats "embedding" as a swappable component behind one seam.

## 9. Project structure

```
kayock-support-desk/
├── run.py                    # entry point — starts the server, opens a browser, prints LAN info
├── doctor.py                 # CLI environment check (same data as Settings → System diagnostics)
├── requirements.txt
├── requirements-dev.txt      # requirements.txt + pytest/httpx, for running tests
├── requirements-ocr.txt      # OPTIONAL extras for scanned-PDF OCR (not installed by default)
├── Start-Windows.bat
├── Start-Linux.sh
├── Start-Mac.command
├── docs/
│   └── LINUX_SETUP.md        # Linux (Ubuntu-first) setup guide
├── scripts/
│   └── smoke_test.py         # end-to-end smoke test against a real server process
├── app/
│   ├── main.py                # FastAPI routes (pages + JSON API)
│   ├── config.py               # paths, defaults, ports, managed-AI timeouts
│   ├── db.py                   # tiny SQLite wrapper + schema + additive migrations + metrics
│   ├── services/
│   │   ├── device_service.py   # device/document CRUD (incl. vehicle fields)
│   │   ├── indexing.py         # parse → section/table-aware chunk → embed → store
│   │   ├── query_service.py    # hybrid retrieve (vector + keyword, RRF-fused) → verify → answer
│   │   ├── text_index.py       # chunks table + SQLite FTS5/BM25 keyword search (LIKE fallback)
│   │   ├── chat_service.py     # persisted per-device chat sessions + follow-up handling
│   │   ├── diagnostic_service.py # structured diagnostic log (symptoms/steps/readings/ruled-out)
│   │   ├── spec_service.py     # structured spec extraction (torque/fluid/capacity/fuse/schedule)
│   │   ├── job_queue.py        # persistent SQLite job queue (progress/retries/cancel/recovery)
│   │   ├── diagnostics.py      # local environment diagnostics snapshot
│   │   ├── logging_service.py  # rotating structured app log (data/logs/app.log)
│   │   ├── vehicle.py          # OBD normalization, vehicle context, verification layers, reranking
│   │   ├── automotive_sources.py # optional online lookups: VIN decode, recalls, DTC (isolated HTTP)
│   │   ├── dtc_dictionary.py   # local offline generic OBD-II code definitions
│   │   ├── llm_client.py       # OpenAI-compatible client (now also multi-turn messages)
│   │   ├── ai_manager.py       # owns the managed llama-server + Ollama processes
│   │   ├── file_picker.py      # host-side native file dialog for setup's Browse buttons
│   │   ├── vector_store.py     # Chroma wrapper (+ chunk read-back for the one-time backfill)
│   │   ├── settings_service.py # per-install settings (DB-backed)
│   │   ├── extensions/         # modular seams: obd.py (adapter interface), ocr.py (optional OCR)
│   │   └── network_info.py     # LAN IP + QR code generation
│   ├── templates/               # Jinja2 HTML pages (incl. viewer.html for the PDF viewer)
│   └── static/                  # CSS (light/dark/compact) + vanilla JS + vendored pdf.js + PWA assets
├── tests/                        # pytest suite (see below)
└── data/                         # created on first run — your library lives here
    ├── kayock.db                 # devices, documents, chunks, jobs, chats, specs, diagnostics, metrics
    ├── documents/
    ├── chroma/
    └── logs/                     # chat_server.log / embedding_server.log / app.log
```

---

## 10. Architecture summary

### Document Q&A (unchanged from earlier versions)

- **Web framework:** FastAPI + Uvicorn, serving both the JSON API and the
  server-rendered HTML pages (Jinja2 templates, vanilla JS — no frontend
  build step, so it's easy to open and modify).
- **Database:** plain SQLite (`data/kayock.db`) via Python's built-in
  `sqlite3` — no ORM. Stores devices, document metadata/status, and
  settings.
- **Document parsing:** [`pypdf[crypto]`](https://pypi.org/project/pypdf/) for
  PDFs (the `[crypto]` extra pulls in `cryptography`, needed for
  AES-encrypted PDFs -- see "Encrypted PDFs" below); plain text reads for
  `.txt`/`.md`.
- **Chunking:** [`langchain-text-splitters`](https://pypi.org/project/langchain-text-splitters/)'s
  `RecursiveCharacterTextSplitter`.
- **Embeddings & chat:** the official [`openai`](https://pypi.org/project/openai/)
  Python client pointed at any OpenAI-compatible endpoint — this is what
  lets it talk to Ollama, LM Studio, vLLM, etc. without hard-coding a
  provider.
- **Vector storage & retrieval:** [`chromadb`](https://www.trychroma.com/),
  running embedded (persisted to `data/chroma/`, no separate server
  process). One collection, filtered by `device_id` per query, then
  locally reranked (see "Technical-query retrieval" below).
- **QR codes:** [`qrcode`](https://pypi.org/project/qrcode/) + Pillow.

Indexing runs as a FastAPI background task, so uploads return immediately
and the device page polls for status until each manual finishes.

### Encrypted PDFs

Many factory/technical PDFs (including vehicle service manuals) are
AES-encrypted purely to restrict editing/printing, with **no user password
required to open and read them**. Before this pass, that tripped a raw
dependency error (`cryptography>=3.1 is required for AES algorithm`)
because plain `pypdf` doesn't bundle the crypto backend it needs.

The fix: `requirements.txt` now installs `pypdf[crypto]` (pulling in
`cryptography`), and `indexing.py` checks `reader.is_encrypted` and calls
`reader.decrypt("")` --  an empty-string decrypt succeeds for the
"encrypted but no real password" case and fails (returns `NOT_DECRYPTED`)
for a PDF that genuinely needs a password. On genuine failure, Kayock
shows a plain-English message ("This PDF is password-protected... remove
the password and re-upload it") — never the raw dependency/library error.
Ordinary unencrypted PDFs are completely unaffected.

### Technical-query retrieval (reranking)

Chroma's vector search alone will happily return any passage containing
the word "motor" for a query about "the blower motor" — including wiper
motors, washer motors, or a 4WD hydraulic actuator motor, since they're
all semantically close to "motor". Rather than replacing Chroma or adding
a second search system, `query_service.py` now:

1. asks Chroma for a **wider candidate set** (`TOP_K * 3` instead of just
   `TOP_K`),
2. hands that candidate list to `vehicle.rerank_hits()`, which boosts a
   chunk's score for each **exact** match it contains: an OBD code
   (`P0302`) gets the strongest boost, an exact 3-word phrase from the
   question next, then an exact 2-word phrase — so a chunk containing the
   literal phrase "blower motor" jumps ahead of a chunk that only shares
   the generic word "motor",
3. trims back down to `TOP_K` before building the prompt.

This is plain Python string matching over an already-small candidate list
(a few dozen chunks) — no new dependency, no new service, and it helps
*any* technical query (appliance model numbers, part names), not just
vehicles. See `tests/test_retrieval_reranking.py` for the exact "blower
motor vs. generic motor" behavior, and `tests/test_vehicle_query.py` /
the smoke test below for it working over real HTTP requests.

### Managed local AI (new)

The key design decision: **`ai_manager.py` never talks to the RAG
pipeline directly.** Starting AI just means writing the same
`llm_base_url` / `llm_model` / `embedding_base_url` / `embedding_model`
settings that `llm_client.py`, `indexing.py`, and `query_service.py`
already read — those three files have **zero changes** in this pass. The
"managed" and "external" modes are indistinguishable to the rest of the
app; only *who last wrote those four settings* differs.

**Chat/answering model — direct `llama-server`:**
- Kayock finds `llama-server` on PATH (or at a configured path), picks a
  free local port (bind-to-port-0 trick, no fixed/guessable port), and
  launches `llama-server -m <your.gguf> --port <port> --host 127.0.0.1`.
- Polls `/health` until ready (or the process exits early, whichever
  comes first) before reporting Ready.
- On success, writes `llm_base_url = http://127.0.0.1:<port>/v1` and a
  fixed `llm_model = "local-model"` — llama-server's OpenAI-compatible
  endpoint serves whatever single model it loaded regardless of the
  `model` field sent, so no real model-ID lookup is needed.
- A friendly display name (e.g. "Qwen3.5 4B") is derived from the GGUF
  filename by trimming a trailing quantization-looking token
  (`Q4_K_M`, `F16`, etc.) — best-effort, not a model registry.

**Embedding/manual-search model — managed Ollama, not a second
`llama-server`:**

The task asked me to investigate both options and pick the simplest,
most dependable one:

- *Option A (direct GGUF embeddings via a second llama-server instance)*
  would mean managing two independent llama-server processes, each with
  its own pooling/normalization flags that vary by embedding GGUF and
  quantization — more moving parts, more ways to silently get slightly
  wrong (but not obviously wrong) vectors.
- *Option B (managed Ollama)* reuses the **exact same embedding code path
  Kayock already had and had already validated** (`nomic-embed-text` via
  Ollama's OpenAI-compatible `/v1/embeddings`) — the only new work is
  making Ollama itself invisible: detect if it's already serving, start
  `ollama serve` if not, `ollama pull nomic-embed-text` if the model
  isn't there, then wait for `/api/version` to answer.

I chose **Option B**. It's less code, has no new failure surface for the
part of the app (retrieval quality) that's hardest to notice when subtly
wrong, and keeps 100% of the embedding_signature/reindex logic exactly as
it already worked. If a future pass wants Option A, `ai_manager.py`
already treats "embedding" as one swappable component — a second
`llama-server`-based implementation could be dropped in behind the same
`_do_start_embedding()` seam without touching anything else.

**Process safety:**
- Never launches a second managed instance if one it started is still
  alive and healthy (`Start AI` clicked twice is a no-op the second time).
- If Ollama was already running before Kayock touched it, Kayock marks
  that component `managed: false` and **never stops it** — `Stop AI` only
  terminates processes Kayock itself spawned this session.
- stdout/stderr from both processes goes to `data/logs/*.log` (for
  diagnosis), never into the on-screen status, which only ever shows a
  short plain-English hint plus which log file has the details.
- A crashed/never-healthy process moves its component to `error` with a
  human-readable detail; it never crashes the Kayock server itself.
- **Closing Kayock stops its own managed AI too.** When Kayock shuts down
  (Ctrl+C in its console, or the process is otherwise stopped), it
  synchronously stops any `llama-server`/Ollama it started this session
  before exiting — no orphan processes, no port left occupied. This uses
  a blocking shutdown call (not a background thread), so it reliably
  finishes before the Python process exits. An Ollama instance that was
  already running externally before Kayock connected to it is left
  completely alone, exactly as during a manual `Stop AI` click.

**Stop AI verifies the process is actually gone before saying so.** A
real Windows test surfaced a bug: clicking Stop AI sometimes left
`llama-server.exe` still running, requiring Task Manager → End Task to
actually free it up. That's fixed now — `Stop AI`:
1. sends a graceful termination signal to the exact PID Kayock launched;
2. if it hasn't exited after a few seconds, escalates to a **targeted**
   forceful kill — on Windows, `taskkill /PID <pid> /T /F` (scoped to
   that one process's tree), never a broad `taskkill /IM llama-server.exe`,
   which would also take down an unrelated FOXAI/Ollama/llama-server
   session someone else has running; on macOS/Linux, the equivalent is a
   process-group `SIGTERM` → `SIGKILL` (llama-server is launched in its
   own session specifically so this is possible if it ever spawns helper
   processes);
3. **confirms real exit** — checks the process object is actually dead,
   polls until nothing answers on the port it was using, and polls until
   its health endpoint stops responding;
4. only reports **Stopped** once all three checks pass. If any of them
   fail, the status becomes an honest **"Kayock could not fully stop the
   answering model"** instead of a false "Stopped" — the technical detail
   goes to `data/logs/*.log`, never a raw error dumped into the UI.

A compact **AI status widget** now lives in the top navigation on every
page (not just the dedicated AI page) — 🟢 Running / ⚫ Stopped / 🟡
needs attention, with a one-click Start/Stop right there, so unloading
the model never requires navigating anywhere first.

**Optional idle auto-unload.** Settings → "Automatically unload
answering model after inactivity" (Never / 10 / 30 / 60 minutes, default
Never). A lightweight background check every 30 seconds — not a
scheduler — unloads *only* the answering model (`llama-server`) once
that many minutes have passed with no question asked and no OBD
diagnosis run; manual search (embedding) is never auto-unloaded. Asking
a question, running OBD diagnosis, or a fresh `Start AI` all reset the
idle clock. The next question (or a manual `Start AI`) loads the model
again normally, same as any other start.

**Browse buttons open a real file dialog.** Since Kayock runs on the same
machine you're using to configure it, the **Browse…** buttons on the
setup page ask the *server* to open a native OS file-selection dialog
(via Python's built-in `tkinter`) and hand back the path you picked — not
a browser `<input type="file">`, which can never see the real filesystem
path for security reasons. Nothing is uploaded; only the chosen path
travels back to the browser. If no dialog can be opened (e.g. a headless
box with no display), Kayock says so and the text field next to it still
works — paste the path directly.

### Component- and property-aware spec verification

`app/services/vehicle.py` adds two layered verification passes that
`query_service.py` calls for vehicle spec-shaped questions (see §4):

**Layer 1 — component.** `is_spec_question()` (keyword match against a
list like "capacity", "torque", "resistance", …), `extract_component_phrases()`
(checks a modest built-in alias list — "rear differential", "transfer
case", "blower motor", "oxygen sensor", etc. — falling back to a generic
bigram of the question's significant words if nothing matches), and
`tag_component_verification()`, which splits an already-retrieved,
already-reranked candidate set into "verified" (the component phrase
appears on a short, label-like line within the chunk's opening ~300-500
characters, not just the very first line -- real manuals often open a
page with a broader section title and name the actual component a bit
further down) and "other."

**Layer 2 — property.** Correct component still isn't enough: a chunk
headed "Rear Differential" can contain a cover-bolt torque, a
mounting-bolt torque, *and* a drain-plug torque. `extract_requested_properties()`
pulls the specific property being asked about from a modest alias map
(`PROPERTY_ALIASES` in `vehicle.py` — drain plug, fill plug, mounting
bolt, cover bolt, guide pin, resistance, voltage, capacity, fluid type,
etc., with a generic "torque" fallback only used when no specific
fastener type was named). `tag_property_verification()` then checks,
*within* the component-verified set, whether a number appears close to
the property's name — same line, or the line immediately after (the
common manual layout of a label line followed by a value line) — via
`_property_evidence_in_text()`. Hits that clear both layers become
"VERIFIED EXACT-SPEC EXCERPTS"; component-verified hits that don't
satisfy any requested property become "RELATED EXCERPTS" (still shown
for general context, never for an exact value).

`query_service.answer_question()` builds a prompt with all groups clearly
labeled, tells the model exactly which requested properties have zero
verified evidence anywhere (so it can say so precisely instead of
guessing), and — critically — only ever includes exact-spec-verified
excerpts in the returned citation `sources`, regardless of what the model
says. This means the safety property doesn't depend entirely on the
model following instructions: even if a weaker/smaller local model
ignored the prompt rule, the wrong-property passage still never appears
in the citations a user would check. Nothing about the core retrieval
(Chroma, the existing exact-phrase reranker) changed — this is a
filtering/labeling pass layered on top of it, and multiple requested
properties in one question are verified completely independently of each
other.

---

## 11. Exact launch commands

If you'd rather run it manually instead of using the launcher scripts:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Then open the URL it prints (default `http://localhost:8765`).

Environment variables (optional, set before launching):

| Variable          | Default            | Purpose                          |
|-------------------|---------------------|-----------------------------------|
| `KAYOCK_DATA_DIR` | `./data`            | Where devices/manuals/index live |
| `KAYOCK_HOST`     | `0.0.0.0`           | Interface to bind                |
| `KAYOCK_PORT`     | `8765`              | Port to serve on                 |
| `KAYOCK_OPEN_BROWSER` | `1`             | Set to `0` to skip auto-opening a browser tab |

---

## 12. Running the tests

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

`requirements-dev.txt` installs everything in `requirements.txt` plus
`pytest` and `httpx` (the test client needs `httpx`; the running app
doesn't). This is the only extra step compared to a normal install — the
command above works on a completely clean checkout.

The test suite covers device CRUD, the full upload → index → ask
pipeline, auto-detect, settings, the reindex-on-embedding-change safety
net (see below), QR/network endpoints, backup export, the managed AI
process lifecycle (saved paths, missing-model errors, no duplicate
processes, health → Ready, crash handling, Stop AI only touching
Kayock-managed processes, chat-vs-embedding reindex interplay, and
external mode), the host-side file picker (real path returned, cancel is
harmless, unavailable degrades cleanly, and a real un-mocked call in this
headless environment to prove the fallback genuinely works),
application-shutdown AI cleanup (stops Kayock-managed processes, leaves
external/unmanaged ones alone), encrypted-PDF indexing (a real AES-256
PDF built at test time, both the no-password and password-required
cases), vehicle profile persistence, OBD-II code normalization, the exact
"blower motor" retrieval-reranking behavior, vehicle-aware prompt
construction (Beginner vs. Experienced, diagnostic headings, applicability
caution, safety instructions), OBD diagnosis staying scoped to the asking
vehicle's own manuals, VIN validation/normalization, VIN decode
(success/failure/disabled/cached/force-refresh), recall lookup
(success/no-results/timeout/disabled/cached/force-refresh), generic DTC
lookup (known/unknown codes, never touches the network), the standard
OBD-II definition staying visibly separate from the manual-backed answer,
a VIN decode never silently overwriting an existing profile field, manual
Q&A staying fully functional with online sources disabled, confirming
uploaded document content is never included in an external lookup
request, the component-verification spec-accuracy fix (rejecting a
transaxle's fluid capacity for a rear-differential question, keeping
multiple requested components' citations separate, excluding a wrong
motor's/fastener's specs, and reporting "not verified" instead of
guessing when no matching spec exists), the follow-up property-aware
verification fix (rejecting a differential's cover-bolt/mounting-bolt
torque for a drain-plug question, distinguishing drain vs. fill plug,
caliper guide-pin vs. carrier-mount torque, blower-motor resistance vs.
voltage, fluid type present but capacity absent, and multiple requested
properties verified fully independently of each other), the follow-up
component-heading detection fix (recognizing a component named on a
later short label line rather than only the chunk's first line, matching
real Hyundai-manual page layouts, while confirming the original
cross-component rejections still hold with the wider search window), and
the Stop AI reliability fix (real-exit verification via process/port/health
checks, forced-kill escalation when graceful termination hangs, never
touching an unmanaged or unrelated process, repeated start/stop/start
cycling, and the idle auto-unload timer's persistence/reset/"Never"
behavior), the hybrid-retrieval upgrades (exact trouble-code and fuse-number
keyword search, table rows kept intact, section attribution on chunks and
citations, embedding-outage degradation to keyword-only answers, the
chunks-table backfill from Chroma for pre-upgrade documents, the job queue's
enqueue/claim/retry/cancel/crash-recovery behavior, the diagnostics snapshot,
and launcher validity), structured spec extraction (torque/fluid/capacity/
fuse/schedule rows with excerpts, verified-vs-candidate separation, document
preference across multiple manuals, additive re-extraction), saved chat
sessions (persistence, follow-up retrieval resolution, history budget,
fresh-evidence-every-turn), diagnostic session tracking (steps, readings,
ruled-out causes, lifecycle and validation), the local PDF viewer endpoints
(bundled pdf.js wiring, file serving, page-text lookup), and the extension
seams (OBD adapter interface with honest NullAdapter default, OCR hook
unavailable/available/failure paths). It uses a fake embedding/chat model (see `tests/fakes.py`) and
mocks subprocess/HTTP calls for the AI manager and all external NHTSA
calls at their single choke points, so the whole suite runs fully offline
— no
Ollama, no llama-server binary, no GPU, no real GGUF file, and no
internet connection required to develop or verify the app.

---

## 13. Keeping answers consistent when you change the AI model

If you switch the **embedding** model or server on the Settings page
(the chat model can change freely — only the embedding model affects
this), any manuals indexed under the old embedding settings no longer
match: their vectors were produced by a different model. Kayock Support
Desk automatically marks those manuals **`needs_reindex`** and excludes
them from search — it never silently answers using embeddings from a
mismatched model.

On the device page, a manual flagged this way shows a "needs reindex"
status and a **Reindex Manuals** button appears. Clicking it rebuilds the
index for those manuals straight from the originally saved file — no
re-upload required. This applies the same way whether the change came
from Settings' Advanced fields or from starting the managed embedding
backend for the first time.

### Vehicle applicability (multiple manuals, multiple configurations)

A vehicle device can hold several manuals at once — Owner's Manual,
Factory Service Manual, Electrical Troubleshooting Manual, Maintenance
Schedule, TSBs — and a single factory manual often covers several
years/engines/trims in one PDF. Kayock doesn't build a vehicle database or
try to hard-filter which passages "belong" to your exact configuration
(that's the kind of heavyweight system this pass deliberately avoids).
Instead, it's a **lightweight, prompt-level check**: your vehicle profile
(year/make/model/engine/drivetrain, if you filled it in) is included as
context, and the model is instructed to (a) only apply excerpts consistent
with that profile, (b) explicitly flag it when an excerpt looks like it's
for a different powertrain/configuration (a hybrid/EV procedure showing up
for a gasoline vehicle, for example) rather than silently using it, and
(c) say so plainly when retrieved excerpts cover multiple variants and it
can't tell which applies. Every answer's sources still show exactly which
document and page it came from, so you can always check for yourself.

---

## 14. Known limitations

- **Text-based PDFs by default.** Scanned/image-only PDFs (no embedded
  text layer) are rejected with a clear error **unless** you install the
  optional OCR pieces (`requirements-ocr.txt` + the `tesseract` binary) —
  then they index like any other PDF. See §8.5 ("Optional OCR").
- **No diagrams/tables/images in answers.** Retrieval is text-chunk based;
  a wiring diagram or parts table won't be "read" beyond whatever text
  pypdf can extract around it.
- **No authentication.** Anyone who can reach the LAN URL can use and edit
  the library. Fine for a home network; don't expose this to the public
  internet.
- **Component verification is label-line/opening-section based, not full
  NLP.** A component is recognized from a short label-like line (≤100
  characters) within roughly the opening 300-500 characters of a chunk —
  this was widened from "line 1 only" after real Hyundai manual testing
  showed pages commonly open with a broader section title ("Driveshaft
  and axle") and name the actual component a bit further down ("Rear
  differential"). A component named only in the middle of a long
  explanatory sentence, or introduced well past that opening window, or
  referred to only by an abbreviation not on Kayock's small alias/synonym
  list, may still not get "verified" and will fall back to the
  unverified/"could not confirm" path even though the manual does have
  the answer. This trades some recall for the much more important
  property of never silently attributing the wrong number to a
  component — see `tests/test_heading_detection.py` for the regression
  tests confirming both that this widening fixed real false negatives
  and that it didn't reopen the original cross-component false positives.
- **Property verification is substring/proximity-based, not phrase-boundary
  aware.** Discovered during smoke testing: a fastener literally described
  as "differential **cover mounting** bolt" contains the substring
  "mounting bolt", so it can satisfy a `mounting_bolt_torque` property
  check alongside a genuinely separate "differential mounting bolt" --
  both get offered as evidence for a plain "mounting bolt torque"
  question, distinguishable only by their citation text, not
  structurally separated the way drain-vs-fill-vs-guide-pin-vs-resistance
  reliably are (those have no such substring overlap and are covered by
  the regression tests). This is a narrower version of the same
  known trade-off as component verification: lightweight phrase matching
  over real NLP. It does not reproduce the original Hyundai-class bug
  (a value from a genuinely different component/property silently
  presented as authoritative) -- the citations shown still let a user
  see exactly which fastener each value came from.
- **Idle auto-unload is a 30-second poll, not instant.** A model can stay
  loaded up to ~30 seconds past the configured idle window before the
  next check notices; this is intentional (see §10 — "a lightweight
  background timer, not a scheduler") and not adjustable via the UI.
- **No OBD scanner integration yet.** OBD-II codes are manually typed in
  — no Bluetooth/USB adapter support ships in this pass. The adapter
  interface for one now exists (`app/services/extensions/obd.py`) with an
  honest NullAdapter default; nothing in the UI pretends a scanner is
  connected when it isn't.
- **No VIN-specific recall lookup.** No officially documented NHTSA
  endpoint for it was found; recalls are matched by year/make/model and
  labeled as potentially applicable rather than guaranteed for your exact
  VIN.
- **Generic OBD-II definitions are a local dictionary (~120 common
  codes), not an online lookup.** No suitable free/stable/keyless public
  API exists today (see §5) — this is a deliberate fallback, not an
  oversight, and it's structured to be swappable for a real API later.
  An uncommon code not in the dictionary still gets a category-level note
  (Powertrain/Body/Chassis/Network) rather than nothing.
- **This sandbox's outbound network is allowlisted and does not include
  NHTSA's domains** (`vpic.nhtsa.dot.gov`, `api.nhtsa.gov`) — confirmed via
  a `403 host_not_allowed` response when attempting a live call from this
  environment. The endpoints and response schemas were verified against
  real live NHTSA data during research (see the completion report), and
  the failure-handling path was incidentally exercised for real against
  that same 403. A genuine end-to-end online-lookup smoke test needs to
  run on a machine with normal internet access (e.g. the eventual Windows
  target) rather than this development sandbox.
- **Vehicle applicability is a prompt-level instruction, not a hard
  filter.** Kayock tells the model your vehicle profile and asks it to
  flag mismatches (see §12) rather than maintaining a vehicle-configuration
  database to pre-filter retrieval. This depends on the model actually
  following that instruction reasonably well; always check the page
  citations on anything safety- or spec-critical.
- **The "manual-backed vs. general guidance" separation is a prompt
  instruction, not an enforced structural guarantee.** A small/weak local
  model may occasionally blend the two less cleanly than a stronger one
  would; the underlying retrieval and citation behavior (what's actually
  shown as a source) is unaffected either way.
- **Retrieval reranking is lightweight string matching**, not a learned
  reranker — it boosts exact phrase/code matches over an already-small
  Chroma candidate set. It won't help with synonyms Chroma's embeddings
  already miss (e.g. it won't connect "won't blow air" to "blower motor"
  on its own -- that's still on the embedding model).
- **Single background worker.** Uploading many large manuals at once will
  index them one at a time through the persistent job queue; nothing is
  lost, retries and cancellation are built in, and the queue survives
  restarts — it just takes a while for the last ones in the queue.
- **mDNS hostname (`.local`) isn't guaranteed** to resolve on every
  network/router — the plain LAN IP + QR code always works as a fallback.
- **No model discovery/scanning.** By design — you tell Kayock the path to
  a model you already have; it doesn't search your disk for GGUF files.
- **You provide `llama-server` and Ollama yourself.** This pass doesn't
  bundle either binary in the ZIP (they're large, platform-specific, and
  out of scope for a usability pass) — `ai_manager.py` is structured so a
  future pass could ship/detect a bundled `llama-server` more seamlessly,
  but for now the setup screen's "llama-server executable" field is where
  you point at one you already have (e.g. FoxAI's bundled copy).
- **Only the Ollama-backed embedding path (Option B) is implemented.**
  Direct GGUF embeddings via a second `llama-server` (Option A) was
  evaluated and intentionally not built this pass — see §10 for the
  reasoning. `embedding_backend` in settings is reserved for this.
- **Linux verification scope.** Linux (Ubuntu-first) is now a supported
  target with its own launcher, setup guide (docs/LINUX_SETUP.md), and
  diagnostics, and the full automated suite plus an end-to-end HTTP smoke
  test (scripts/smoke_test.py — real server process, real upload/index/
  search/chat/restart/shutdown) were run on Linux. What could NOT be
  live-tested in the development sandbox: a real `llama-server` GGUF run,
  a real Ollama embedding run (neither binary/GPU exists there — both are
  exercised via fakes and the honest failure paths), native GUI file
  dialogs (no display; the text-field fallback was tested for real), and
  browser auto-open on a desktop session. The Windows/macOS launchers are
  unchanged and remain as previously verified.
- **This development sandbox has no real `llama-server`/Ollama binaries or
  GGUF files available**, so the live HTTP smoke tests in this pass
  exercise the real endpoints and confirm graceful "AI needs attention"
  states when those binaries are genuinely absent, and use small
  HTTP-real stand-in scripts to verify the full start/ready/stop/restart
  and shutdown-cleanup lifecycle against real OS processes and ports. A
  full run with an actual model needs a real Windows machine with
  `llama-server` and Ollama present.
- **The real `2015-2018_HYUNDAI_Tucson_TL_SM.pdf` was not available in
  this development sandbox**, so the smoke test used a synthetic
  multi-section PDF built to mirror its structure (brake torque specs,
  an HVAC blower motor section, wiper/washer/4WD "motor" sections
  specifically to test retrieval discrimination, and a P0302 DTC section)
  plus a small real HTTP-speaking fake OpenAI-compatible server (not a
  mocked unit test -- an actual local process Kayock's real HTTP client
  talks to) standing in for Ollama/llama-server. All retrieval,
  reranking, citation, Beginner/Experienced, and OBD-diagnose behavior
  was exercised over genuine HTTP requests against the running app (see
  the smoke test results in the completion report). Running against the
  real Tucson PDF with a real local model is the natural next
  verification step on a machine that has both.
- **The Browse dialog needs `tkinter` and a display.** Windows' standard
  python.org installer includes tkinter by default, so this should work
  out of the box there. If it's missing (some minimal Python installs) or
  there's no display (a headless server), Browse falls back cleanly to
  the text field with an explanation — it never crashes the app. This
  sandbox itself has no tkinter at all, which is exactly what the
  fallback test in `tests/test_file_picker_and_shutdown.py` exercises for
  real.
- **Shutdown cleanup relies on the ASGI lifespan shutdown event firing** —
  true for Ctrl+C in the console the launcher opened, or any normal stop
  request. Forcibly killing the whole process tree at once (e.g. closing
  a terminal window in a way that sends a hard kill signal to every
  process in it simultaneously, or Task-Manager-killing `python.exe`)
  bypasses that event on any platform, same as it would for any other
  process-owning application — there's no way around that from inside
  the app being killed.
