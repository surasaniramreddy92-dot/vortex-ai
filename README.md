# VORTEX AI

A voice-driven desktop assistant on a path toward a full agentic personal
operating system. Today it's a single Windows tray application; the
[master roadmap](#roadmap-phases-0-16) below describes the 17-phase plan for
everything it's meant to become. See [IMPLEMENTED.md](IMPLEMENTED.md) for an
honest, phase-by-phase status matrix — this README summarizes it.

**Current state in one sentence:** VORTEX wakes on a custom-trained "Hey
Vortex" model, holds a multi-turn voice conversation with barge-in
interruption, executes a handful of deterministic OS commands, and falls back
to a local Ollama LLM for everything else.

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
that's the biggest near-term debt, and the
[Claude Code implementation contract](#implementation-contract) below for why
it should *stay* one file a little longer rather than being split
prematurely.

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

- [docs/LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md) — a substantive,
  phase-by-phase explanation of the concepts behind the whole roadmap (not
  just what's built yet), meant to be read as a study guide.
- [IMPLEMENTED.md](IMPLEMENTED.md) — the acceptance-scenario-driven status
  matrix.

## Roadmap (Phases 0–16)

| Phase | Name | Status |
|---|---|---|
| 0 | Engineering Foundation & Repository Discipline | Planned |
| 1 | Voice I/O Foundation | Partial |
| 2 | Desktop & OS Automation | Partial |
| 3 | Browser Automation & Web Interaction | Planned |
| 4 | LLM Brain, Tool Calling & Hybrid Intent Routing | Partial |
| 5 | Memory, Knowledge & Production RAG | Planned |
| 6 | Wake Word, Session Mode & Barge-In | **Implemented (v1)** |
| 7 | Document Intelligence | Planned |
| 8 | Vision & Screen Understanding | Planned |
| 9 | FastAPI Service Layer & Event-Driven Core | Planned |
| 10 | Durable Workflow Orchestration | Planned |
| 11 | Resume & Career Intelligence | Planned |
| 12 | Job Discovery & Safe Application Automation | Planned |
| 13 | Multi-Agent Orchestration | Planned |
| 14 | Developer Agent & Software Generation | Planned |
| 15 | Security, Identity & Policy Enforcement | Partial |
| 16 | Production Platform: Observability, Deployment | Planned |

Full detail per phase (objective, recommended tech, concepts, exit criteria)
is in [docs/LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md).

## Implementation contract

From the master blueprint, and worth restating here so it isn't lost:

- Treat the roadmap as a specification, not permission to build every feature
  into one giant `main.py`. Implement phase-by-phase behind stable interfaces.
- Deterministic tools before LLM reasoning; LLM reasoning before browser/UI
  automation as a last resort.
- Consequential actions (destructive OS commands, submissions, payments,
  irreversible operations) require confirmation — never bypass CAPTCHA, MFA,
  or access controls.
- Never mark a phase complete until its listed acceptance scenario passes
  end-to-end, not just "an API exists."
- Never commit credentials, personal data, resume contents, or session
  secrets.

## Known limitations (read before relying on this)

- STT and TTS require internet even though wake detection doesn't.
- The wake model is calibrated against synthetic TTS voices, not a large set
  of real recordings of you — expect it to need retuning if your voice,
  mic, or room acoustics differ a lot from the validation set.
- No persistent memory: conversation history resets every time the process
  restarts.
- No tests, no CI, no lint/type-check gate yet (Phase 0 debt).
