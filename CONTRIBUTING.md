# Contributing to Kayock Support Desk

Thank you for considering a contribution! Kayock is a local-first manual
and diagnostic assistant — every change should keep it private, honest
about what is verified versus guessed, and easy to run on one machine.

## Development setup

Requirements: Python 3.10+, Git. On Ubuntu, `python3-venv` is needed
(`sudo apt install python3-venv python3-pip`).

```bash
git clone <your fork or the repository>
cd kayock-support-desk
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt -r requirements-ocr.txt
```

## Running the tests

```bash
python -m pytest -q                # full offline suite (deterministic fakes, no models needed)
python scripts/smoke_test.py       # end-to-end against the real server + scripts/fake_model_server.py
python doctor.py                   # environment diagnostics
```

The pytest suite is designed to run fully offline: LLM/embedding calls are
deterministic fakes (`tests/fakes.py`). Please keep it that way — no test
should require downloaded models, GPUs, or network access.

For interactive work you can run the app against the fake model server:

```bash
python scripts/fake_model_server.py 8791 &
python run.py                      # then Settings -> External/Advanced, point at http://127.0.0.1:8791/v1
```

## How we work

- **Additive migrations only.** Schema changes go through the additive
  migration pattern in `app/db.py` (`_migrate`). Never require existing
  installs to delete data or re-upload manuals.
- **Honesty rules.** Never invent values, sources, or features. Exact
  specifications must pass the component/property verification layers;
  unverified data is labeled as candidates. The PWA never presents cached
  content as a live AI answer.
- **Preserve existing behavior.** Keep endpoints, launchers, and settings
  backward compatible. Don't silently reduce model context or add
  authentication gates.
- **Keep new dependencies optional** unless they are genuinely unavoidable;
  runtime installs belong in `requirements.txt`, dev/test-only ones in
  `requirements-dev.txt`, and optional features in their own extras file
  (e.g. `requirements-ocr.txt`). Bundled assets need a third-party notice
  entry (see `THIRD_PARTY_NOTICES.md`).

## Submitting changes

1. Create a branch for your change.
2. Add or update tests for the behavior you're changing (bug fixes need a
   regression test that fails before your fix).
3. Run the full pytest suite and, for user-facing changes, the smoke test.
4. Update documentation if behavior, settings, or setup steps changed
   (README, docs/, docstrings).
5. Open a pull request using the provided template with a clear description
   of what changed and why.

## Proposing features

Open a feature-request issue first for anything larger than a bug fix, so
design can be discussed before code. Small, well-contained fixes can go
straight to a pull request.

## Reporting bugs

Please use the bug-report issue template and include your OS, Python
version, and the relevant section of `data/logs/app.log` (redact anything
personal). Security concerns: see [SECURITY.md](SECURITY.md) — please don't
open public issues for those.
