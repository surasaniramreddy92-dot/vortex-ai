# VORTEX — Implementation Matrix

Honest snapshot of what actually runs today. A phase is only marked
**Implemented** if its acceptance scenario runs end-to-end; if part of it
works it's **Partial**; **Started** means only groundwork exists. This file
only lists phases with real work — phases with no code yet aren't listed
here at all; they'll be added, one at a time, as each is actually built.

Last updated: 2026-08-02.

| Phase | Name | Status | Notes |
|---|---|---|---|
| 0 | Engineering Foundation & Repository Discipline | **Started** | Git repo, `.gitignore`, first commit exist. No typed config, no lint/type-check/pytest scaffolding, no CI, no ADRs/CHANGELOG yet. |
| 1 | Voice I/O Foundation | **Partial** | Mic capture, TTS playback, and chunked/interruptible speech work. STT is cloud-only (Google Web Speech via `speech_recognition`), TTS is cloud-only (`edge-tts`) — no offline path (faster-whisper/Piper) yet, so voice breaks without internet even though wake detection doesn't. No device-selection UI, no formal provider-interface abstraction (`speak`/`capture_command` are concrete methods, not swappable adapters). |
| 2 | Desktop & OS Automation | **Partial** | Open/close named apps, bulk-close with a protected-process allowlist, confirmation gating before shutdown/restart/close-all. No general capability registry, no file operations, no search, no structured audit log (just plaintext log lines). |
| 3 | Browser Automation & Web Interaction | **Partial** | `src/vortex/browser.py`: Playwright-driven navigate/search/click/read-page, voice-triggered ("go to...", "search for...", "click...", "read the page"). Search uses DuckDuckGo's plain-HTML endpoint, not Google — Google serves an automated browser a bot-detection page almost immediately, and working around that would mean bypassing an anti-abuse mechanism, which is out of bounds. No form-filling, uploads/downloads, or multi-page authenticated workflows yet. Browser launches visibly (not headless) and lazily on first use. |
| 4 | LLM Brain, Tool Calling & Hybrid Intent Routing | **Partial** | Regex-based deterministic router falls through to a streaming Ollama call with conversation history. No structured/JSON tool-call schemas, no tool registry, no prompt versioning or eval suite. |
| 5 | Memory, Knowledge & Production RAG | **Partial** | `src/vortex/memory.py`: conversation history now persists across restarts in a local SQLite database instead of an in-RAM list. This is *not* the full Phase 5 scope — no PostgreSQL, no Qdrant, no embeddings, no retrieval, no document/knowledge corpus. It closes one concrete gap (memory surviving a restart), not the whole phase. |
| 6 | Wake Word, Session Mode & Barge-In | **Implemented (v1)** | Furthest-along phase. Custom-trained "Hey Vortex" ONNX wake model (own training pipeline, not Porcupine), automatic gain control on the wake audio path, mid-speech barge-in (stricter threshold + instant TTS/LLM-stream cancellation), and multi-turn active session with inactivity timeout so you don't repeat the wake word for follow-ups or confirmations. Not yet measured against an explicit CPU/RAM budget; false-accept/reject rates are calibrated against synthetic TTS voices only, not a large real-microphone test set. **Known open issue:** false wake activations still occur in real-world conditions (reduced, not eliminated, by the AGC noise-floor fix) — diagnostic logging (score + noise floor per trigger) was added to make the next tuning pass data-driven instead of guesswork. |
| 7 | Document Intelligence | **Partial** | `src/vortex/documents.py`: reads PDF (PyMuPDF)/DOCX (python-docx)/XLSX (openpyxl)/text files by voice ("summarize...", "read...", "what does X say about Y"), resolving filenames against Desktop/Documents/Downloads. Whole-document-in-context, not chunked/embedded/indexed — fine for one file at a time, not a document corpus. No OCR fallback, no page/section provenance in answers yet. |
| 15 | Security, Identity & Policy Enforcement | **Partial** | Protected-process allowlist and explicit yes/no confirmation before destructive OS actions exist. No OAuth/OIDC, no secrets vault (only `.env` + `dotenv`), no dependency/container scanning, no sandboxing (there's no generated-code execution yet to sandbox). |

## Not started (no code yet)

Phases 8 (Vision), 9 (FastAPI/Event-Driven Core), 10 (Durable Workflows), 11
(Career Intelligence), 12 (Job Discovery/Application Automation), 13
(Multi-Agent Orchestration), 14 (Developer Agent), and 16 (Observability/
Deployment) have no code in this repo. Each will get its own row here, its
own README update, and its own section in the learning guide when it
actually lands.

## A capability the blueprint didn't spell out in detail, but is real today

**Self-trained wake-word model** (`tools/wakeword/`): a from-scratch pipeline
that synthesizes training audio (edge-tts across ~20 voices), extracts
embeddings via openWakeWord's shared preprocessor, trains a small MLP
classifier, runs a hard-negative-mining pass over long synthetic audio
streams, and exports the result as a standalone ONNX model matching
openWakeWord's exact I/O contract. This is a narrow, supervised,
single-classifier training loop — not general model training — but it's a
concrete first instance of "VORTEX can train its own models."

## Honest gaps versus the blueprint's own Phase 6 requirements

- *"Wake detection must not require network access"* — true for wake
  detection itself (fully local ONNX). Not true for the rest of the voice
  loop: STT and TTS are both cloud calls, so a network outage silences
  VORTEX even though it can still hear its name.
- *CPU/RAM budget on 8GB-class hardware* — never measured. Should be profiled
  before calling Phase 6 done.
