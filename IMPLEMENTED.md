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
| 2 | Desktop & OS Automation | **Partial** | Open/close named apps, bulk-close with a protected-process allowlist, confirmation gating before shutdown/restart/close-all, lock-system (no confirmation needed - trivially reversible, loses no work). No general capability registry, no file operations, no search, no structured audit log (just plaintext log lines). **Fixed bug:** "shutdown vortex" set internal flags to stop the worker/audio threads but never stopped the `pystray` tray icon, so `icon.run()` (blocking the main thread) never returned and the process lingered as a zombie - stopped listening/responding, but never actually exited. `stop()` now stops the icon too, unifying the voice-triggered shutdown and the tray's Exit item into one real shutdown path. |
| 3 | Browser Automation & Web Interaction | **Partial** | `src/vortex/browser.py`: Playwright-driven navigate/search/click/read-page/play-YouTube-video, voice-triggered ("go to...", "search for...", "click...", "read the page", "play X on youtube"). Search uses DuckDuckGo's plain-HTML endpoint, not Google — Google serves an automated browser a bot-detection page almost immediately, and working around that would mean bypassing an anti-abuse mechanism, which is out of bounds. All web-opening actions (including the old app-launcher's fallback) now route through this one controlled browser session instead of ever popping open the system's default browser. No form-filling, uploads/downloads, or multi-page authenticated workflows yet. Browser launches visibly (not headless) and lazily on first use. **Fixed bug:** "open youtube and play X" used to be swallowed whole by the generic app-launcher's `open (.+)` pattern, which — finding no app or exact web_app match — fell back to opening a literal Google search of the entire phrase in the system's separate default browser (not the automated one), producing search-results pages instead of playing anything. Now routed to an explicit YouTube search-and-click-first-result action before that fallback ever fires. |
| 4 | LLM Brain, Tool Calling & Hybrid Intent Routing | **Partial** | Regex-based deterministic router falls through to a streaming Ollama call with conversation history. No structured/JSON tool-call schemas, no tool registry, no prompt versioning or eval suite. |
| 5 | Memory, Knowledge & Production RAG | **Partial** | Two separate pieces, not yet unified: (1) `src/vortex/memory.py` — conversation history persists across restarts in a local SQLite database instead of an in-RAM list; still no retrieval over it, just chronological recall. (2) `src/vortex/rag.py` — the real Postgres+Qdrant stack from the master blueprint, used specifically for document question-answering: documents are chunked, embedded locally (`nomic-embed-text` via Ollama), and indexed in Qdrant, with Postgres as the transactional source of truth for chunk text/metadata. A targeted question retrieves only the relevant chunks instead of truncating a long document. Runs against local, self-hosted Postgres and Qdrant (native Windows installs, not Docker — see README setup) — no hybrid dense+sparse search, no reranking, and conversation history hasn't been migrated onto this stack (still SQLite). Degrades gracefully to the old truncated-whole-document approach if Postgres/Qdrant aren't running. |
| 6 | Wake Word, Session Mode & Barge-In | **Implemented (v1)** | Furthest-along phase. Custom-trained "Hey Vortex" ONNX wake model (own training pipeline, not Porcupine), automatic gain control on the wake audio path, mid-speech barge-in, and multi-turn active session with inactivity timeout so you don't repeat the wake word for follow-ups or confirmations. Not yet measured against an explicit CPU/RAM budget; false-accept/reject rates are calibrated against synthetic TTS voices only, not a large real-microphone test set. **Barge-in confirmed working (2026-08-16):** verified end-to-end with a real acoustic test - synthesized audio played through the actual speakers, picked up by the actual mic, not a mock or unit test. Sequence: wake → "Yes Boss?" → question captured and answered → "Hey Vortex" said again mid-response → interrupted mid-sentence (score 0.774 against a 0.75 threshold) → confirmed silent within ~1.5s. The earlier reports of barge-in not working were most likely the wake-stream-death bug above, not the threshold itself - once the stream stopped dying, barge-in worked on the first live test. **Known open issue:** false wake activations still occur in real-world conditions during standby (reduced, not eliminated, by the AGC noise-floor fix). Diagnostic logging (score + noise floor per trigger, in `_on_audio`) is kept permanently, not temporarily, specifically to make the next occurrence tunable from real data. **Fixed bug:** barge-in cut off speech with total silence and no acknowledgment - only a fresh standby wake spoke "Yes Boss?"; a barge-in went straight to listening with no audible signal the interruption had registered. Both paths now speak "Yes Boss?" for a consistent, unambiguous confirmation. **Fixed bug (2026-08-16):** the wake word could silently stop registering entirely after the app had run a while, with no error logged anywhere - caused by reusing one `InputStream` across hundreds of stop/start cycles (one pair per command), which eventually stopped delivering audio to the callback. Fixed by recreating the stream on every mic handoff instead of reusing it, plus a watchdog that rebuilds the stream if no audio callback fires for 5+ seconds while idle. See `CHANGELOG.md`. **Diagnosed (2026-08-16):** a separate, real barge-in failure mode on laptops - the mic and speakers sit close together, so VORTEX's own TTS output dominates the mic signal while it's talking, and `_agc` cannot separate two overlapping voices in one channel (that needs true acoustic echo cancellation, not implemented). Confirmed via live logs: zero wake-model scores above 0.3 during a 25-second window while the user was actively trying to interrupt. Mitigated, not fixed, by turning VORTEX's own output down while speaking (`VORTEX_TTS_VOLUME`, default `0.6`). This does not make generic words like "wait" trigger an interrupt - only the trained "Hey Vortex" phrase is ever checked; that remains unchanged. |
| 7 | Document Intelligence | **Partial** | `src/vortex/documents.py`: reads PDF (PyMuPDF)/DOCX (python-docx)/XLSX (openpyxl)/text files by voice ("summarize...", "read...", "what does X say about Y"), resolving filenames against Desktop/Documents/Downloads. Targeted questions ("what does X say about Y") now use the Phase 5 RAG stack (chunked + embedded + retrieved) instead of truncation. Plain "summarize" still truncates the whole document (summarization wants the whole document, not similarity-retrieved snippets — a genuinely different problem, not solved by retrieval). No OCR fallback, no page/section provenance in answers yet. |
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
- *Barge-in with real human voice, not just the trained wake phrase* — only
  "Hey Vortex" is ever checked as an interrupt trigger; generic words like
  "wait" or "stop" do nothing, by design (a VAD-based "any speech interrupts"
  approach would make VORTEX constantly interrupt itself, since its own TTS
  output is speech too). Near-field self-noise (mic hears VORTEX louder than
  the user while it's talking) is mitigated via `VORTEX_TTS_VOLUME` but not
  solved - true acoustic echo cancellation would be required to solve it
  fully, and isn't implemented.
