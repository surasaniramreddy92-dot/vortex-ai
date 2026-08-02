# VORTEX — Implementation Matrix

Authoritative, honest snapshot of what actually runs today versus the 17-phase
roadmap (Phase 0–16) in the master blueprint. A phase is only marked
**Implemented** if its acceptance scenario runs end-to-end; if part of it works
it's **Partial**; if nothing exists yet it's **Planned**. Nothing here is
marked complete just because a related library is imported.

Last updated: 2026-08-02.

| Phase | Name | Status | Notes |
|---|---|---|---|
| 0 | Engineering Foundation & Repository Discipline | **Planned** | No typed config, no lint/type-check/pytest scaffolding, no CI, no ADRs/CHANGELOG yet. Git repo itself was only initialized in this session. |
| 1 | Voice I/O Foundation | **Partial** | Mic capture, TTS playback, and chunked/interruptible speech work. STT is cloud-only (Google Web Speech via `speech_recognition`), TTS is cloud-only (`edge-tts`) — no offline path (faster-whisper/Piper) yet, so voice breaks without internet even though wake detection doesn't. No device-selection UI, no formal provider-interface abstraction (`speak`/`capture_command` are concrete methods, not swappable adapters). |
| 2 | Desktop & OS Automation | **Partial** | Open/close named apps, bulk-close with a protected-process allowlist, confirmation gating before shutdown/restart/close-all. No general capability registry, no file operations, no search, no structured audit log (just plaintext log lines). |
| 3 | Browser Automation & Web Interaction | **Planned** | Only `webbrowser.open(url)` — no Playwright, no DOM interaction, no form filling. |
| 4 | LLM Brain, Tool Calling & Hybrid Intent Routing | **Partial** | Regex-based deterministic router falls through to a streaming Ollama call with conversation history. No structured/JSON tool-call schemas, no tool registry, no prompt versioning or eval suite. |
| 5 | Memory, Knowledge & Production RAG | **Planned** | Conversation history is an in-memory Python list, lost on restart. No PostgreSQL, no Qdrant, no embeddings, no retrieval. |
| 6 | Wake Word, Session Mode & Barge-In | **Implemented (v1)** | Furthest-along phase. Custom-trained "Hey Vortex" ONNX wake model (own training pipeline, not Porcupine), automatic gain control on the wake audio path, mid-speech barge-in (stricter threshold + instant TTS/LLM-stream cancellation), and multi-turn active session with inactivity timeout so you don't repeat the wake word for follow-ups or confirmations. Not yet measured against an explicit CPU/RAM budget; false-accept/reject rates are calibrated against synthetic TTS voices only, not a large real-microphone test set. |
| 7 | Document Intelligence | **Planned** | No PDF/DOCX/XLSX parsing. |
| 8 | Vision & Screen Understanding | **Planned** | No screen capture, no accessibility-tree reading, no vision model integration. |
| 9 | FastAPI Service Layer & Event-Driven Core | **Planned** | Pure desktop tray app; no HTTP/WebSocket API, no Redis, no Kafka. |
| 10 | Durable Workflow Orchestration | **Planned** | No Temporal; nothing survives a process restart except the wake model files on disk. |
| 11 | Resume & Career Intelligence | **Planned** | Not started. |
| 12 | Job Discovery & Safe Application Automation | **Planned** | Not started. |
| 13 | Multi-Agent Orchestration | **Planned** | Single monolithic `Vortex` class; no supervisor/agent split. |
| 14 | Developer Agent & Software Generation | **Planned** | Not started. |
| 15 | Security, Identity & Policy Enforcement | **Partial** | Protected-process allowlist and explicit yes/no confirmation before destructive OS actions exist. No OAuth/OIDC, no secrets vault (only `.env` + `dotenv`), no dependency/container scanning, no sandboxing (there's no generated-code execution yet to sandbox). |
| 16 | Production Platform: Observability, Performance, Deployment | **Planned** | Logging is a flat file (`logs/vortex.log`); no tracing, metrics, SLOs, containers, or CI/CD pipeline. |

## A capability the blueprint didn't spell out in detail, but is real today

**Self-trained wake-word model** (`tools/wakeword/`): a from-scratch pipeline
that synthesizes training audio (edge-tts across ~20 voices), extracts
embeddings via openWakeWord's shared preprocessor, trains a small MLP
classifier, runs a hard-negative-mining pass over long synthetic audio
streams, and exports the result as a standalone ONNX model matching
openWakeWord's exact I/O contract. This is a narrow, supervised,
single-classifier training loop — not general model training — but it's a
concrete first instance of "VORTEX can train its own models," worth treating
as the seed of that larger ambition rather than overselling it as more than
it is.

## Honest gaps versus the blueprint's own Phase 6 requirements

- *"Wake detection must not require network access"* — true for wake
  detection itself (fully local ONNX). Not true for the rest of the voice
  loop: STT and TTS are both cloud calls, so a network outage silences
  VORTEX even though it can still hear its name.
- *CPU/RAM budget on 8GB-class hardware* — never measured. Should be profiled
  before calling Phase 6 done.
