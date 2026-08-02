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
  "click sign in"                          - clicks matching visible text
  "read the page"                          - reads back the open page's text
  "summarize my resume"                    - finds + summarizes a document
  "what does budget.xlsx say about Q3"     - answers from a document's contents
  "explain how Java works"                 - falls back to the local LLM
```

## Quickstart

Prerequisites:
- Windows, Python 3.11
- [Ollama](https://ollama.com) running locally with `llama3.2:1b` pulled
- A working microphone and speakers
- Internet access (STT and TTS are both cloud calls today — see
  [IMPLEMENTED.md](IMPLEMENTED.md) for why that's a gap, not a design choice)

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m src.vortex.main
```

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
                          |                         PyMuPDF/python-docx/openpyxl)
                          otherwise -> Ollama (llama3.2:1b, streamed)
  -> response text -> sentence-chunked -> edge-tts -> pygame playback
       (each chunk synthesized ahead of playback; stop_speaking event
        can cut it off between chunks or mid-chunk)
  -> conversation turns persisted to SQLite (survives restarts)
```

The orchestration/voice/OS-automation logic still lives in
[src/vortex/main.py](src/vortex/main.py) as one class — see
[IMPLEMENTED.md](IMPLEMENTED.md) Phase 0 for why that's the biggest
near-term debt, and [docs/REFACTOR_PLAN.md](docs/REFACTOR_PLAN.md) for the
planned (not yet started) modularization. The three newest capabilities are
at least already separate, composed modules rather than more methods on the
same class:

- [src/vortex/memory.py](src/vortex/memory.py) — SQLite-backed conversation history
- [src/vortex/documents.py](src/vortex/documents.py) — PDF/DOCX/XLSX/text reading
- [src/vortex/browser.py](src/vortex/browser.py) — Playwright-driven navigation/search/click

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
- Conversation memory persists across restarts (SQLite), but it's plain
  history, not retrieval — no embeddings, no document/knowledge corpus (that
  fuller scope is still Phase 5's RAG work).
- Document reading is whole-file-in-context, not chunked/indexed — fine for
  one document at a time, not a corpus.
- Browser search uses DuckDuckGo, not Google — Google serves an automated
  browser a bot-detection page almost immediately, and working around that
  would mean bypassing an anti-abuse mechanism.
- Browser automation is navigate/search/click/read-page only — no form
  filling, uploads, downloads, or authenticated multi-page workflows yet.
- No tests, no CI, no lint/type-check gate yet (Phase 0 debt).
