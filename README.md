# VORTEX AI

A voice-driven desktop assistant, built incrementally against a 17-phase
roadmap (Phase 0–16) toward a full agentic personal operating system. This
README only documents the phases with real work in the repo so far — it's
updated in the same commit/PR as each phase lands, rather than describing
work that doesn't exist yet. See [IMPLEMENTED.md](IMPLEMENTED.md) for the
detailed status/notes on each of those phases, and
[CHANGELOG.md](CHANGELOG.md) for a dated, day-by-day record of what shipped
when.

**Current state in one sentence:** VORTEX wakes on a custom-trained "Hey
Vortex" model, holds a multi-turn voice conversation with barge-in
interruption, remembers that conversation across restarts, reads and answers
questions about your documents, browses the web, executes a handful of
deterministic OS commands, and falls back to a local Ollama LLM for
everything else.

> **Two separate numbered "phase/step" tracks exist in this repo — don't
> confuse them.** The table below tracks the master **feature roadmap**
> (Phase 0-16: Voice I/O, OS Automation, Browser, RAG, etc.) — most rows are
> genuinely "Partial," each with specific, real gaps documented in
> [IMPLEMENTED.md](IMPLEMENTED.md). Separately,
> [docs/REFACTOR_PLAN.md](docs/REFACTOR_PLAN.md) tracks an **internal
> code-organization refactor** (Steps 0-10: the god-object `main.py` →
> `app.py` + `core/`/`platform/`/`llm/`/`tools/`) — that one *is* fully
> complete, but it changed *how the code is structured*, not *what features
> exist*. "The refactor is done" and "a feature phase is Partial" are both
> true at the same time; they're not the same claim.

## Phases with work in this repo so far

| Phase | Name | Status |
|---|---|---|
| 0 | Engineering Foundation & Repository Discipline | Partial |
| 1 | Voice I/O Foundation | Partial |
| 2 | Desktop & OS Automation | **Implemented (v1)** |
| 3 | Browser Automation & Web Interaction | **Implemented (v1)** |
| 4 | LLM Brain, Tool Calling & Hybrid Intent Routing | Partial |
| 5 | Memory, Knowledge & Production RAG | **Implemented (v1)** |
| 6 | Wake Word, Session Mode & Barge-In | **Implemented (v1)** |
| 7 | Document Intelligence | **Implemented (v1)** |
| 8 | Vision | Started |
| N/A | Communication (Email) — not one of the original 17 phases, see [IMPLEMENTED.md](IMPLEMENTED.md) | Started |
| 15 | Security, Identity & Policy Enforcement | Partial |

Everything else in the master roadmap (vision, service layer, durable
workflows, career intelligence, multi-agent orchestration, developer agent,
observability) has no code yet. It'll show up here, in IMPLEMENTED.md, and in
[docs/LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md) as each phase actually lands,
phase by phase, in its own commit batch.

## Example voice commands

```
"Hey Vortex" -> "Yes Boss?"
  "open chrome"                            - launch a native app
  "what time is it" / "what's the date"    - deterministic, no LLM
  "close all applications"                 - asks for confirmation first
  "shutdown system" / "restart system"     - asks for confirmation first
  "lock the system"                        - immediate, no confirmation (reversible)
  "search for openai"                      - browses, reads back top results
  "go to github.com"                       - navigates there (visible browser)
  "play some badminton highlights on youtube" - searches YouTube, plays the first result
  "click sign in"                          - clicks matching visible text
  "read the page"                          - reads back the open page's text
  "summarize my resume"                    - finds + summarizes a document (whole-document)
  "what does budget.xlsx say about Q3"     - RAG-retrieved answer from a document's contents
  "check my email"                         - summarizes unread mail (needs Gmail setup, see below)
  "reply to john and say I'll be there at 5" - LLM drafts a reply, asks before sending
  "do you remember my favorite language"   - retrieval over past conversation turns
  "explain how Java works"                 - falls back to the local LLM
```

## Quickstart

Prerequisites:
- Windows, Python 3.11
- [Ollama](https://ollama.com) running locally with `llama3.2:1b` and
  `nomic-embed-text` pulled (`ollama pull llama3.2:1b`, `ollama pull nomic-embed-text`)
- A working microphone and speakers
- Internet access (STT and TTS are both cloud calls today — see
  [IMPLEMENTED.md](IMPLEMENTED.md) for why that's a gap, not a design choice)
- **Optional but recommended:** PostgreSQL + Qdrant running locally, for the
  RAG-backed document Q&A path (see below). VORTEX starts and runs fine
  without them — document Q&A just falls back to the older truncated-
  whole-document approach if they're not reachable.

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools
pip install -e ".[all,voice-offline,dev]"
python -m playwright install chromium
python -m src.vortex.main
```

(`requirements.txt` still exists but is superseded by `pyproject.toml`'s
dependency groups — see its own header comment. `all` covers every runtime
extra; drop `voice-offline`/`dev` if you don't need the offline STT/TTS
fallback or the test/lint tooling.)

### Setting up the RAG stack (PostgreSQL + Qdrant)

Both run as native Windows services/binaries here — no Docker required
(Docker Desktop needs WSL2, which needs admin rights and usually a reboot;
this avoids that entirely).

```powershell
# PostgreSQL 17
winget install --id PostgreSQL.PostgreSQL.17 -e

# Create a dedicated database + role (don't use the postgres superuser directly)
$env:PGPASSWORD = "postgres"  # or whatever password the installer set
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h localhost -c "CREATE USER vortex WITH PASSWORD 'vortex_local_dev';"
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE vortex OWNER vortex;"

# Qdrant - download the native Windows binary and run it (no install needed)
# (see https://github.com/qdrant/qdrant/releases for the latest version)
mkdir tools\qdrant
Invoke-WebRequest -Uri "https://github.com/qdrant/qdrant/releases/latest/download/qdrant-x86_64-pc-windows-msvc.zip" -OutFile tools\qdrant\qdrant.zip
Expand-Archive tools\qdrant\qdrant.zip tools\qdrant -Force
cd tools\qdrant; .\qdrant.exe   # leave this running in its own window
```

Then set `VORTEX_POSTGRES_DSN` and `VORTEX_QDRANT_URL` in `.env` (see
`.env.example` for the shape) to match. VORTEX's RAG schema and Qdrant
collection are created automatically on first run — no manual migration step.

### Setting up Gmail ("check my email" / "reply to X and say Y")

Optional. Without this, email commands speak "Email isn't set up yet"
instead of failing silently or crashing. Needs a one-time OAuth consent in
a real browser - not something `pip install` alone can do.

1. **Google Cloud Console** (one-time, ~5 minutes):
   - Go to [console.cloud.google.com](https://console.cloud.google.com),
     create a project (or reuse one).
   - **APIs & Services → Library** → search "Gmail API" → Enable.
   - **APIs & Services → Credentials** → Create Credentials → OAuth client
     ID → Application type: **Desktop app**.
   - If prompted for an OAuth consent screen first, choose **External**,
     fill in the required fields (app name, your email) - it can stay in
     "Testing" mode, no Google review needed for personal use with your own
     account added as a test user.
   - Download the credentials JSON.
2. Save it as `gmail_credentials.json` at your `VORTEX_HOME` root (e.g.
   `E:\VORTEX\gmail_credentials.json`) - or point `VORTEX_GMAIL_CREDENTIALS`
   at wherever you saved it instead.
3. The first time you say "check my email" or "reply to...", a browser
   window opens asking you to sign in and approve access - this only
   happens once. VORTEX caches the resulting token at
   `<VORTEX_HOME>/data/gmail_token.json` (`VORTEX_GMAIL_TOKEN` to
   override) and refreshes it silently after that.

Scopes requested are read-only + send only (`gmail.readonly` +
`gmail.send`) - VORTEX never deletes, archives, or labels anything. Every
reply is drafted by the LLM and spoken back for your explicit yes/no before
it's ever sent - see "Known limitations" below for what this doesn't do yet.

Say **"Hey Vortex"**, wait for "Yes Boss?", then speak a command. Follow-up
commands and yes/no confirmations don't need the wake word repeated — the
session stays open for `VORTEX_SESSION_TIMEOUT` seconds (default 18s) of
silence before returning to standby.

While VORTEX is talking, saying "Hey Vortex" again cuts it off mid-sentence
(barge-in) and takes your new instruction immediately.

## Environment variables (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `VORTEX_HOME` | `~/.vortex` | where logs/data/memory live. Set this to point at an existing install's directory to preserve continuity rather than starting fresh |
| `VORTEX_VOICE` | `en-US-AvaMultilingualNeural` | edge-tts voice |
| `USER_NAME` | `Boss` | how VORTEX addresses you |
| `VORTEX_WAKE_WORD` | `tools/wakeword/models/hey_vortex.onnx`, resolved relative to the repo checkout (not `VORTEX_HOME` — it's a committed asset, not user data) | path to the wake model |
| `VORTEX_WAKE_THRESHOLD` | `0.60` | score needed to wake from standby |
| `VORTEX_BARGE_IN_THRESHOLD` | `0.60` | score needed to interrupt VORTEX's own speech — deliberately *not* stricter than the wake threshold (barge-in is inherently harder to score high on, since the mic also hears VORTEX's own speakers) |
| `VORTEX_AGC_TARGET_RMS` / `VORTEX_AGC_MAX_GAIN` / `VORTEX_AGC_NOISE_MARGIN` | `3500` / `4.0` / `1.6` | automatic gain control on the wake audio path: boosts speech-level audio toward the target RMS, but only for frames that stand out `NOISE_MARGIN`x above a tracked ambient noise floor, so steady background noise doesn't get amplified into a false wake trigger |
| `VORTEX_SESSION_TIMEOUT` | `18` | seconds of silence before an active session returns to standby |
| `VORTEX_MEMORY_DB` | `<VORTEX_HOME>/data/vortex_memory.db` | SQLite database conversation history persists to |
| `VORTEX_POSTGRES_DSN` | `dbname=vortex user=vortex password=... host=localhost` | Postgres connection string for document/chunk metadata (RAG) |
| `VORTEX_QDRANT_URL` | `http://localhost:6333` | Qdrant endpoint for vector search (RAG) |
| `VORTEX_EMBED_MODEL` | `nomic-embed-text` | local Ollama embedding model used to vectorize document chunks and questions |
| `VORTEX_OFFLINE_FALLBACK_ENABLED` | `true` | kill switch for the offline STT (`faster-whisper`)/TTS (`piper-tts`) fallback, engaged only on a real network-reachability failure |
| `VORTEX_OCR_ENABLED` | `true` | OCR fallback for scanned PDF pages and `read my screen` (needs the separate Tesseract binary on PATH) |
| `VORTEX_AUDIT_LOG` | `<VORTEX_HOME>/logs/audit.jsonl` | structured JSON-lines record of consequential actions (file delete/move, app close, shutdown/restart, confirmation prompts and their outcomes) |
| `VORTEX_GMAIL_CREDENTIALS` | `<VORTEX_HOME>/gmail_credentials.json` | OAuth client credentials you download from Google Cloud Console (see "Setting up Gmail") |
| `VORTEX_GMAIL_TOKEN` | `<VORTEX_HOME>/data/gmail_token.json` | cached OAuth token after the one-time browser consent, auto-refreshed after that |
| `VORTEX_MAIL_MAX_RESULTS` | `5` | how many unread emails "check my email" summarizes at once |

## Architecture today

```
Microphone
  -> voice/wake.py: sounddevice InputStream (1280-sample frames, always-on)
       -> voice/audio.py AGC -> openWakeWord (custom "Hey Vortex" ONNX model)
            -> score >= threshold? -> wake/barge-in event
  -> voice/session.py worker thread: "Yes Boss?"
       -> voice/stt.py: Google Web Speech, falling back to faster-whisper
          (offline) on a real network failure
       -> core/intent_router.py: pure text -> Intent (29 types, no side effects)
            -> Intent matched? -> core/capability_registry.py dispatches to:
                 tools/system/* (apps/processes) + platform/windows/* (the how)
                 browser.py (Playwright)
                 documents.py (PyMuPDF/python-docx/openpyxl, OCR fallback)
                 files.py (list/search/move/copy/rename/delete)
                 screen.py (screenshot + OCR)
                 mail.py (Gmail check/reply, LLM-drafted, confirm-before-send)
                 rag.py (RAG-backed document Q&A + conversation-memory recall via Postgres+Qdrant)
            -> Unhandled -> llm/ollama_provider.py (llama3.2:1b, streamed)
  -> voice/tts.py: response text -> sentence-chunked -> edge-tts (falling
       back to piper-tts offline on a network failure) -> pygame playback
       (each chunk synthesized ahead of playback; stop_speaking event
        can cut it off between chunks or mid-chunk)
  -> conversation turns persisted to SQLite (survives restarts)
  -> consequential actions also logged to audit.py's JSON-lines trail

core/orchestrator.py owns the process lifecycle around all of this (tray
icon, worker thread spawn, clean teardown); core/state_manager.py exposes
the current STANDBY/ACTIVE_SESSION/SPEAKING state as a read-only view over
the same events, for anything that wants to ask "what is VORTEX doing
right now" without re-deriving it.

Document Q&A specifically:
  file -> extract_text() -> chunk (800 chars, 100 overlap)
       -> embed each chunk (nomic-embed-text via Ollama)
       -> Postgres (chunk text + metadata, source of truth)
       -> Qdrant (vectors, indexed by document_id)
  question -> embed -> Qdrant similarity search (top 5, filtered to that document)
           -> only the relevant chunks -> Ollama (streamed) -> spoken answer
```

**The modular refactor described in [docs/REFACTOR_PLAN.md](docs/REFACTOR_PLAN.md)
is complete (all 11 steps, 0-10)** — `main.py` is now a 15-line bootstrap
(`from .app import Vortex; Vortex().start()`); every piece of the former
god-object lives in its own module:

- [src/vortex/app.py](src/vortex/app.py) — the `Vortex` class: composition root, document/RAG orchestration, file-op execution
- [src/vortex/config.py](src/vortex/config.py) — typed, testable config (`VortexConfig`), one env var per field
- [src/vortex/voice/](src/vortex/voice/) — wake detection (`wake.py`), STT (`stt.py`, cloud + offline fallback), TTS (`tts.py`, cloud + offline fallback), AGC (`audio.py`), barge-in signaling (`barge_in.py`), the active-session/worker event loop (`session.py`)
- [src/vortex/llm/](src/vortex/llm/) — `LLMProvider` interface (`provider.py`) + the concrete Ollama implementation (`ollama_provider.py`)
- [src/vortex/platform/](src/vortex/platform/) — `PlatformAdapter` interface (`base.py`) + the Windows implementation (`windows/power.py`, plus its app-table and protected-process data files) — the seam a future Linux/macOS adapter would plug into
- [src/vortex/tools/system/](src/vortex/tools/system/) — OS-automation capability logic (open/close apps, bulk-close), consuming the platform-specific tables above
- [src/vortex/core/](src/vortex/core/) — `intent_router.py` (pure text→Intent classification, 29 Intent types), `capability_registry.py` (dispatch), `policy_engine.py` (yes/no confirmation parsing), `orchestrator.py` (process lifecycle: tray icon, worker thread, teardown), `state_manager.py` (explicit `VortexState` enum)
- [src/vortex/memory.py](src/vortex/memory.py) — SQLite-backed conversation history
- [src/vortex/documents.py](src/vortex/documents.py) — PDF/DOCX/XLSX/text reading, with OCR fallback for scanned PDFs
- [src/vortex/browser.py](src/vortex/browser.py) — Playwright-driven navigation/search/click
- [src/vortex/rag.py](src/vortex/rag.py) — chunking/embedding/Postgres+Qdrant hybrid retrieval for document Q&A, plus dense-only retrieval over indexed conversation turns for memory recall ("do you remember...")
- [src/vortex/files.py](src/vortex/files.py) — voice-triggered list/search/move/copy/rename/delete, scoped to Desktop/Documents/Downloads
- [src/vortex/screen.py](src/vortex/screen.py) — screenshot + OCR ("read my screen")
- [src/vortex/popup.py](src/vortex/popup.py) — synchronized visual file-listing window
- [src/vortex/audit.py](src/vortex/audit.py) — structured JSON-lines audit trail for consequential actions
- [src/vortex/mail.py](src/vortex/mail.py) — Gmail check/reply, lazy OAuth like `browser.py`'s lazy Playwright launch, reply-sending gated behind the same confirmation flow as destructive file ops

`tests/unit/` (228 tests) and `tests/integration/` (real intent router →
real capability registry → real tools/system, faking only the genuinely
dangerous or environment-dependent boundaries) run in CI
([.github/workflows/ci.yml](.github/workflows/ci.yml)) on every push —
lint (`ruff`), type-check (`mypy`), then the full suite, excluding anything
marked `hardware` (needs a real mic/Ollama/GUI process — those get their
own manually-triggered workflow,
[.github/workflows/hardware.yml](.github/workflows/hardware.yml)).

## The wake-word training pipeline

`tools/wakeword/build_hey_vortex.py` trains the custom "Hey Vortex" model
from scratch: synthesizes ~95 positive and ~290 negative TTS clips across 19
voices/accents, extracts embeddings via openWakeWord's shared preprocessor,
trains a small MLP classifier, mines hard negatives from long synthetic audio
streams, and exports an ONNX model matching openWakeWord's exact input/output
contract. `tools/wakeword/validate_hey_vortex.py` checks it against held-out
voices before it's trusted. Re-run the build script (cached audio in
`tools/wakeword/cache/` avoids re-synthesizing) if the wake word needs
retraining — e.g., swapping the target phrase or adding real recorded samples
instead of only synthetic ones.

## Learning resources

- [docs/LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md) — substantive explanations
  of the concepts behind each phase covered so far, meant to be read as a
  study guide, not a one-liner summary.
- [IMPLEMENTED.md](IMPLEMENTED.md) — the acceptance-scenario-driven status
  matrix for those same phases.

## Known limitations (read before relying on this)

- STT and TTS require internet even though wake detection doesn't.
- The wake model is calibrated against synthetic TTS voices, not a large set
  of real recordings of you — false wake activations in real-world
  conditions are reduced (noise-floor-aware AGC, raised thresholds) but not
  eliminated. Every trigger now logs its score and noise floor, so further
  tuning is data-driven rather than guesswork.
- Conversation memory persists across restarts (SQLite) and now supports
  retrieval too ("do you remember X") via the same Postgres+Qdrant stack
  used for document Q&A — but dense (embedding) search only, not the hybrid
  BM25+rerank pipeline documents get; needs Postgres+Qdrant running (see
  Quickstart), degrades to a clear spoken explanation if they aren't.
- Document *question-answering* uses real chunking/embedding/retrieval
  (Postgres+Qdrant); document *summarization* still truncates the whole file
  — summarizing wants the whole document, not similarity-retrieved snippets,
  so retrieval doesn't apply there the same way.
- The RAG stack requires PostgreSQL and Qdrant running locally as separate
  processes (see Quickstart) — VORTEX degrades gracefully to the old
  truncated approach if they're not reachable, rather than failing to start.
- Browser search uses DuckDuckGo, not Google — Google serves an automated
  browser a bot-detection page almost immediately, and working around that
  would mean bypassing an anti-abuse mechanism.
- Browser automation is navigate/search/click/read-page only — no form
  filling, uploads, downloads, or authenticated multi-page workflows yet.
- Email is voice-triggered only — you ask VORTEX to check mail or draft a
  reply; it doesn't proactively notice new mail and interrupt you about it
  (that would need a background-polling worker integrated with the wake/
  session state machine, deliberately deferred as a separate, larger piece
  of work). "Reply to X" resolves X against unread messages only (sender or
  subject substring match), not your whole mailbox, and needs an exact-
  enough match — ambiguous matches ask you to be more specific rather than
  guessing. Every reply is LLM-drafted and spoken back for an explicit yes/
  no before sending; nothing is ever sent automatically. Not yet
  live-verified end-to-end (needs your own Gmail OAuth setup - see "Setting
  up Gmail" - to test against a real inbox); unit-tested against a mocked
  Gmail API instead.
- Wake-word/AGC/barge-in timing and the offline STT/TTS fallback's actual
  trigger condition both need real microphone/speaker hardware to verify —
  covered by historical live acoustic testing (see `CHANGELOG.md`) rather
  than CI, which runs on a headless runner with no audio device.
