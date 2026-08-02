# VORTEX AI

A voice-driven desktop assistant, built incrementally against a 17-phase
roadmap (Phase 0–16) toward a full agentic personal operating system. This
README only documents the phases with real work in the repo so far — it's
updated in the same commit/PR as each phase lands, rather than describing
work that doesn't exist yet. See [IMPLEMENTED.md](IMPLEMENTED.md) for the
detailed status/notes on each of those phases.

**Current state in one sentence:** VORTEX wakes on a custom-trained "Hey
Vortex" model, holds a multi-turn voice conversation with barge-in
interruption, executes a handful of deterministic OS commands, and falls back
to a local Ollama LLM for everything else.

## Phases with work in this repo so far

| Phase | Name | Status |
|---|---|---|
| 0 | Engineering Foundation & Repository Discipline | Started |
| 1 | Voice I/O Foundation | Partial |
| 2 | Desktop & OS Automation | Partial |
| 4 | LLM Brain, Tool Calling & Hybrid Intent Routing | Partial |
| 6 | Wake Word, Session Mode & Barge-In | **Implemented (v1)** |
| 15 | Security, Identity & Policy Enforcement | Partial |

Everything else in the master roadmap (browser automation, memory/RAG,
document intelligence, vision, service layer, durable workflows, career
intelligence, multi-agent orchestration, developer agent, observability) has
no code yet. It'll show up here, in IMPLEMENTED.md, and in
[docs/LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md) as each phase actually lands,
phase by phase, in its own commit batch.

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
| `VORTEX_WAKE_THRESHOLD` | `0.7` | score needed to wake from standby |
| `VORTEX_BARGE_IN_THRESHOLD` | `0.85` | stricter score needed to interrupt VORTEX's own speech (the mic also hears the speakers) |
| `VORTEX_AGC_TARGET_RMS` / `VORTEX_AGC_MAX_GAIN` | `3500` / `8.0` | automatic gain control on the wake audio path, so normal speaking volume registers |
| `VORTEX_SESSION_TIMEOUT` | `18` | seconds of silence before an active session returns to standby |

## Architecture today

```
Microphone
  -> sounddevice InputStream (1280-sample frames, always-on)
       -> AGC -> openWakeWord (custom "Hey Vortex" ONNX model)
            -> score >= threshold? -> wake/barge-in event
  -> worker thread: "Yes Boss?" -> SpeechRecognition (Google Web Speech)
       -> regex router: deterministic command? -> execute directly
                          otherwise -> Ollama (llama3.2:1b, streamed)
  -> response text -> sentence-chunked -> edge-tts -> pygame playback
       (each chunk synthesized ahead of playback; stop_speaking event
        can cut it off between chunks or mid-chunk)
```

Everything lives in one file, [src/vortex/main.py](src/vortex/main.py), by
design at this stage — see [IMPLEMENTED.md](IMPLEMENTED.md) Phase 0 for why
that's the biggest near-term debt.

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
  of real recordings of you — expect it to need retuning if your voice,
  mic, or room acoustics differ a lot from the validation set.
- No persistent memory: conversation history resets every time the process
  restarts.
- No tests, no CI, no lint/type-check gate yet (Phase 0 debt).
