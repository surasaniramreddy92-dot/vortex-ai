# VORTEX AI

A voice-driven desktop assistant, built incrementally against a 17-phase
roadmap (Phase 0–16) toward a full agentic personal operating system. This
README only documents the phases with real work in the repo so far — it's
updated in the same commit/PR as each phase lands, rather than describing
work that doesn't exist yet. See [IMPLEMENTED.md](IMPLEMENTED.md) for the
detailed status/notes on each of those phases.

**Current state in one sentence:** VORTEX wakes on a custom-trained "Hey
Vortex" model, holds a multi-turn voice conversation with barge-in
interruption, remembers that conversation across restarts, reads and answers
questions about your documents, browses the web, executes a handful of
deterministic OS commands, and falls back to a local Ollama LLM for
everything else.

## Phases with work in this repo so far

| Phase | Name | Status |
|---|---|---|
| 0 | Engineering Foundation & Repository Discipline | Started |
| 1 | Voice I/O Foundation | Partial |
| 2 | Desktop & OS Automation | Partial |
| 3 | Browser Automation & Web Interaction | Partial |
| 4 | LLM Brain, Tool Calling & Hybrid Intent Routing | Partial |
| 5 | Memory, Knowledge & Production RAG | Partial |
| 6 | Wake Word, Session Mode & Barge-In | **Implemented (v1)** |
| 7 | Document Intelligence | Partial |
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
  "search for openai"                      - browses, reads back top results
  "go to github.com"                       - navigates there (visible browser)
  "play some badminton highlights on youtube" - searches YouTube, plays the first result
  "click sign in"                          - clicks matching visible text
  "read the page"                          - reads back the open page's text
  "summarize my resume"                    - finds + summarizes a document (whole-document)
  "what does budget.xlsx say about Q3"     - RAG-retrieved answer from a document's contents
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
pip install -r requirements.txt
python -m src.vortex.main
```

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

Say **"Hey Vortex"**, wait for "Yes Boss?", then speak a command. Follow-up
commands and yes/no confirmations don't need the wake word repeated — the
session stays open for `VORTEX_SESSION_TIMEOUT` seconds (default 18s) of
silence before returning to standby.

While VORTEX is talking, saying "Hey Vortex" again cuts it off mid-sentence
(barge-in) and takes your new instruction immediately.

## Environment variables (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `VORTEX_VOICE` | `en-US-AvaMultilingualNeural` | edge-tts voice |
| `USER_NAME` | `Boss` | how VORTEX addresses you |
| `VORTEX_WAKE_WORD` | `tools/wakeword/models/hey_vortex.onnx` | path to the wake model |
| `VORTEX_WAKE_THRESHOLD` | `0.8` | score needed to wake from standby |
| `VORTEX_BARGE_IN_THRESHOLD` | `0.9` | stricter score needed to interrupt VORTEX's own speech (the mic also hears the speakers) |
| `VORTEX_AGC_TARGET_RMS` / `VORTEX_AGC_MAX_GAIN` / `VORTEX_AGC_NOISE_MARGIN` | `3500` / `4.0` / `1.6` | automatic gain control on the wake audio path: boosts speech-level audio toward the target RMS, but only for frames that stand out `NOISE_MARGIN`x above a tracked ambient noise floor, so steady background noise doesn't get amplified into a false wake trigger |
| `VORTEX_SESSION_TIMEOUT` | `18` | seconds of silence before an active session returns to standby |
| `VORTEX_MEMORY_DB` | `data/vortex_memory.db` | SQLite database conversation history persists to |
| `VORTEX_POSTGRES_DSN` | `dbname=vortex user=vortex password=... host=localhost` | Postgres connection string for document/chunk metadata (RAG) |
| `VORTEX_QDRANT_URL` | `http://localhost:6333` | Qdrant endpoint for vector search (RAG) |
| `VORTEX_EMBED_MODEL` | `nomic-embed-text` | local Ollama embedding model used to vectorize document chunks and questions |

## Architecture today

```
Microphone
  -> sounddevice InputStream (1280-sample frames, always-on)
       -> AGC -> openWakeWord (custom "Hey Vortex" ONNX model)
            -> score >= threshold? -> wake/barge-in event
  -> worker thread: "Yes Boss?" -> SpeechRecognition (Google Web Speech)
       -> regex router: deterministic command? -> execute directly
                          |                        (apps/processes, browser via
                          |                         Playwright, documents via
                          |                         PyMuPDF/python-docx/openpyxl,
                          |                         RAG-backed Q&A via Postgres+Qdrant)
                          otherwise -> Ollama (llama3.2:1b, streamed)
  -> response text -> sentence-chunked -> edge-tts -> pygame playback
       (each chunk synthesized ahead of playback; stop_speaking event
        can cut it off between chunks or mid-chunk)
  -> conversation turns persisted to SQLite (survives restarts)

Document Q&A specifically:
  file -> extract_text() -> chunk (800 chars, 100 overlap)
       -> embed each chunk (nomic-embed-text via Ollama)
       -> Postgres (chunk text + metadata, source of truth)
       -> Qdrant (vectors, indexed by document_id)
  question -> embed -> Qdrant similarity search (top 5, filtered to that document)
           -> only the relevant chunks -> Ollama (streamed) -> spoken answer
```

The orchestration/voice/OS-automation logic still lives in
[src/vortex/main.py](src/vortex/main.py) as one class — see
[IMPLEMENTED.md](IMPLEMENTED.md) Phase 0 for why that's the biggest
near-term debt, and [docs/REFACTOR_PLAN.md](docs/REFACTOR_PLAN.md) for the
planned (not yet started) modularization. The newer capabilities are at
least already separate, composed modules rather than more methods on the
same class:

- [src/vortex/memory.py](src/vortex/memory.py) — SQLite-backed conversation history
- [src/vortex/documents.py](src/vortex/documents.py) — PDF/DOCX/XLSX/text reading
- [src/vortex/browser.py](src/vortex/browser.py) — Playwright-driven navigation/search/click
- [src/vortex/rag.py](src/vortex/rag.py) — chunking/embedding/Postgres+Qdrant retrieval for document Q&A

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
- Conversation memory persists across restarts (SQLite), but it's still
  plain chronological history, not retrieval — the Postgres+Qdrant RAG stack
  is used for document Q&A only, not (yet) for searching past conversations.
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
- No tests, no CI, no lint/type-check gate yet (Phase 0 debt).
