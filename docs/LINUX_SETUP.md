# Linux setup guide (Ubuntu first)

Kayock Support Desk runs entirely on your machine. This guide covers a
native Linux install — Ubuntu 22.04 / 24.04 first, with notes for other
distros and headless servers. Nothing here is installed automatically by
Kayock; the launcher and `doctor.py` only *check* these pieces and tell
you what's missing.

## 1. System dependencies

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
```

| Package | Why | Required? |
|---|---|---|
| `python3` (≥ 3.10) | the app itself | **Yes** |
| `python3-venv` | creates the project's `.venv` (this is the one Ubuntu box people miss — without it `python3 -m venv` fails) | **Yes** |
| `python3-pip` | installing the Python requirements into the venv | **Yes** |
| `python3-tk` | native "Browse…" file dialogs on the setup page | No — without it the Browse buttons fall back to a plain text field |
| `tesseract-ocr` (+ `pip install -r requirements-ocr.txt`) | indexing scanned/image-only PDFs | No — optional, see §5 |
| `xdg-open` (present on desktop Ubuntu) | auto-opening the browser tab | No — if missing, just open the printed URL manually |

## 2. Start it

```bash
./Start-Linux.sh
```

First run: creates `.venv`, installs the Python requirements (a minute or
two, once), then starts the server and prints the URL
(`http://localhost:8765` by default). Later runs start straight away.

The launcher deliberately does **not** install system packages, start or
restart any system services, or touch anything outside its own folder.

### Environment variables the launcher respects

| Variable | Default | Purpose |
|---|---|---|
| `KAYOCK_PYTHON` | `python3` | use a specific Python (e.g. `KAYOCK_PYTHON=python3.12 ./Start-Linux.sh`) |
| `KAYOCK_DATA_DIR` | `./data` | where your library lives |
| `KAYOCK_HOST` | `0.0.0.0` | interface to bind |
| `KAYOCK_PORT` | `8765` | port to serve on |
| `KAYOCK_OPEN_BROWSER` | `1` | set `0` to skip auto-opening a browser tab |

## 3. The local AI pieces (you provide them)

Kayock manages the model processes for you, but you supply them once:

### llama-server (answering model, GGUF)

Install llama.cpp's `llama-server` (via your distro's preferred method —
a release build from the llama.cpp GitHub releases, or a package). Kayock
looks for `llama-server` on `PATH`; if you keep it elsewhere, point at it
with the "llama-server executable" field on the AI setup screen.

- **CPU-only builds work identically** — no GPU flags are needed and none
  are assumed.
- On a GPU machine, llama-server uses whatever acceleration its build
  provides. If you want to pass flags explicitly (e.g.
  `--n-gpu-layers 99 --flash-attn`), set them in
  **Settings → Advanced AI Settings → Managed model launch options →
  Extra llama-server arguments**. Parsed shell-style; nothing GPU-specific
  is ever hard-coded.

### Ollama (manual-search embeddings)

Install [Ollama](https://ollama.com) (`curl -fsSL https://ollama.com/install.sh | sh`
per their docs). Kayock starts `ollama serve` if nothing is listening on
its port, pulls `nomic-embed-text` if missing, and stops only what it
started itself — an Ollama you were already running is left alone.

If you'd rather point Kayock at an existing model server (LM Studio,
vLLM, a llama-server on another box), use **Settings → External /
Advanced Server** instead — no managed processes at all.

## 4. Headless servers (no display)

Works fine; two behavior changes are automatic and printed as notes by
the launcher:

- Browse… buttons fall back to text fields (no display → no native dialog).
- No browser tab opens; open the printed URL (or use the QR code on
  another device).

Check everything at any time:

```bash
python doctor.py            # human-readable; exit code 0 = ready
python doctor.py --json     # machine-readable
```

## 5. Scanned PDFs (optional OCR)

Image-only PDFs have no text layer. Install the optional pieces and they
index like any other manual:

```bash
sudo apt install tesseract-ocr
.venv/bin/pip install -r requirements-ocr.txt
```

Re-upload (or Reindex) the scanned manual afterwards. Without these
pieces, scanned PDFs are rejected with a message naming exactly what's
missing.

## 6. Running it manually / as a service

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

If you want it as a **user service**, write your own systemd unit calling
those commands — Kayock intentionally ships none and never touches system
services itself. Set `KAYOCK_OPEN_BROWSER=0` in a service context.

```ini
# Example skeleton (adjust paths/user) — ~/.config/systemd/user/kayock.service
[Unit]
Description=Kayock Support Desk
After=network.target

[Service]
WorkingDirectory=%h/kayock-support-desk
Environment=KAYOCK_OPEN_BROWSER=0
ExecStart=%h/kayock-support-desk/.venv/bin/python run.py
Restart=on-failure

[Install]
WantedBy=default.target
```

## 7. Updating / moving an install

- **Backup**: Settings → Download backup (.zip), or copy the `data/`
  folder while stopped.
- **Moving machines**: copy the whole folder (or restore `data/`), then
  `./Start-Linux.sh` again. The database, manuals, vector index, text
  index, chat history, and settings all live under `data/`.
- Upgrades are **additive migrations**: starting a newer version never
  deletes or silently rebuilds existing data. Documents indexed by an
  older version are backfilled into the keyword index automatically on
  first start (no re-upload, no re-embedding).

## 8. Troubleshooting

- `python3 -m venv` fails with "ensurepip is not available" → install
  `python3-venv python3-pip` (§1). The launcher catches this before it
  happens.
- "llama-server executable not found" → put it on PATH or set the path on
  the AI setup screen (Advanced disclosure).
- Browse… says the picker is unavailable → install `python3-tk`, or just
  type the path.
- Model won't start → the AI page shows a short hint plus which log file
  has details (`data/logs/chat_server.log`, `data/logs/app.log`).
- Something else → **Settings → System diagnostics → Run diagnostics**
  (or `python doctor.py`) and check `data/logs/`.
