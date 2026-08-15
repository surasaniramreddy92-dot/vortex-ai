# Changelog

Dated, day-by-day record of what actually landed in the repo. Each entry is
written the same day the work happens and pushed same-day going forward —
this file is the fast way to see what changed and when without reading full
commit diffs. Phase-by-phase status (not date-based) lives in
`IMPLEMENTED.md`; this file is chronological.

## 2026-08-16

### Verified
- **Barge-in confirmed actually working, end-to-end, via a real acoustic
  test** - not a unit test, not a code read-through: synthesized audio
  played through the real speakers, picked up by the real mic. Sequence:
  wake → "Yes Boss?" → question captured and answered → "Hey Vortex" said
  again mid-response → interrupted mid-sentence within ~1.5s. The standing
  complaint that barge-in "doesn't work" was most likely the wake-stream-
  death bug (fixed earlier today), not the interruption logic itself - once
  the stream stopped silently dying, barge-in worked on the first live test.
  Test scripts (acoustic loopback via edge-tts + pygame, log-synchronized
  polling rather than fixed sleeps) are disposable/local, not committed.

### Fixed
- **`speak_stream()` - every LLM answer and document response - was never
  logged at all.** Only `speak()`'s deterministic replies ("Yes Boss?",
  date/time) logged what they said; anything AI-generated was invisible in
  the log. Found while writing the barge-in test above (a `wait_for
  ("Speaking:", ...)` check kept timing out - not because VORTEX wasn't
  responding, but because that log line structurally never gets written for
  streamed responses). Moved the log call into `_synth()`, which both
  `speak()` and `speak_stream()` funnel through per-chunk - now every
  spoken chunk is logged, including the actual text of every AI response.
- **Wake word silently stopped registering after the app had been running a
  while**, even at full volume, with no error anywhere in the logs. Root
  cause: `_own_mic()` called `stream.stop()`/`stream.start()` on the *same*
  `sounddevice.InputStream` instance every time the mic was handed to
  SpeechRecognition and back (once per command, so hundreds of cycles over a
  long session). After enough stop/start cycles, the stream kept running but
  silently stopped delivering audio to the callback - no exception, tray icon
  still responsive, just no more "Wake triggered" log lines, ever. Fixed by
  closing and fully recreating the `InputStream` on every handoff instead of
  reusing one instance (`_open_wake_stream`, `_recover_wake_stream`).
- Added a watchdog as a safety net for any *other* reason the stream might go
  quiet: if no audio callback fires for 5+ seconds while VORTEX should be
  listening (not mid-command-capture), `_worker()`'s idle loop rebuilds the
  stream automatically. Diagnosed via `WAKE_WATCHDOG_TIMEOUT`.
- `RagStore()`'s Postgres/Qdrant connections had no explicit timeout. If
  either service was down or slow to start, the *entire* VORTEX startup
  could hang indefinitely inside `Vortex.__init__` - before the wake stream,
  tray icon, or anything else came up - because construction happens
  synchronously before `start()`. This is very likely what made the app look
  completely dead today: Qdrant's own scheduled task had been killed earlier
  in the session (`LastTaskResult` showed a `STATUS_CONTROL_C_EXIT`) and
  never came back. Added explicit 5s timeouts to both connections
  (`psycopg2.connect(..., connect_timeout=5)`,
  `QdrantClient(..., timeout=5)`) so a down dependency fails fast into the
  existing graceful-degradation path instead of hanging everything.
- "Qdrant Vector DB Autostart" scheduled task now auto-restarts up to 3 times
  (1 min apart) if the process exits unexpectedly, instead of staying dead
  until the next login.
- **Command audio sent to speech-to-text was completely unboosted**, unlike
  the wake stream (which runs through `_agc`) - a real asymmetry where "Hey
  Vortex" could fire fine on a boosted signal while the actual follow-up
  command consistently failed to transcribe on a raw one. Added
  `_boost_audio_data()` applying the same gain treatment before
  `recognize_google()`. Also fixed `Capture error:` log lines that had been
  silently empty this entire project - `sr.UnknownValueError`'s `str()` is
  empty by design; the log now includes the exception type name too.

### Changed
- **Started the modular refactor** (`docs/REFACTOR_PLAN.md` Step 1), on hold
  since the audit that produced it: `pyproject.toml` with dependency groups
  split by concern (every version pinned to what's actually installed,
  verified via `pip show`, not guessed), and `src/vortex/config.py` - a typed
  config object mirroring every constant in `main.py`. Not wired into
  `main.py` yet (Step 2); zero behavior change, verified by restarting
  VORTEX afterward and confirming it still greets and detects wake
  identically. Caught a real bug before it shipped: a first draft of
  `config.py` used plain class-body `= os.getenv(...)` defaults, which
  Python freezes at class-definition time - confirmed with a standalone
  repro, not assumed - so the config would have silently ignored any env
  var set after the module was first imported. Fixed by switching every
  field to `field(default_factory=...)`, with a regression test
  (`test_two_instances_are_independent_snapshots`) written specifically to
  catch it if it comes back. `legacy/main_working_baseline.py` added as the
  verbatim (byte-diffed) pre-refactor safety copy per the standing rule.

### Added
- `CHANGELOG.md` (this file).
- `tests/unit/test_config.py` - the first real pytest tests in the repo (7,
  all passing).

## 2026-08-02

The bulk of the current build. Eight commits, same calendar day:

### Added
- **Custom-trained "Hey Vortex" wake model** (`tools/wakeword/`): synthesizes
  training audio via edge-tts across ~20 voices/accents, extracts embeddings
  through openWakeWord's shared preprocessor, trains a small MLP classifier,
  runs a hard-negative-mining pass, exports a standalone ONNX model matching
  openWakeWord's exact I/O contract.
- **Barge-in**: mid-speech interruption via chunked/streamed TTS synthesis
  and a shared `stop_speaking` cancellation signal checked at every yield
  point in playback and LLM token streaming.
- **Multi-turn active session** with inactivity timeout, so the wake word
  doesn't need repeating for follow-ups or yes/no confirmations.
- **Automatic gain control** on the wake audio path, later made noise-floor-
  aware (tracks a rolling ambient estimate, only boosts audio that stands
  out above it) after real-world false activations traced back to steady
  background noise getting amplified.
- **Persistent conversation memory** (`memory.py`, SQLite) - history now
  survives a restart instead of living only in a Python list.
- **Document intelligence** (`documents.py`) - reads PDF/DOCX/XLSX/text
  files by voice ("summarize...", "read...", "what does X say about Y"),
  resolving filenames against Desktop/Documents/Downloads.
- **Browser automation** (`browser.py`, Playwright) - navigate/search/click/
  read-page/play-YouTube-video, driven by one visible, controlled browser
  session.
- **Full RAG stack** (`rag.py`) - PostgreSQL + Qdrant running natively on
  Windows (no Docker - WSL2 wasn't available without admin rights/a reboot),
  local embeddings via Ollama's `nomic-embed-text`. Targeted document
  questions retrieve only the relevant chunks instead of truncating a long
  document at a fixed character limit.
- `docs/CURRENT_STATE.md`, `docs/REFACTOR_PLAN.md`, `docs/ARCHITECTURE.md` -
  full audit of the codebase against the master blueprint, an incremental
  modularization plan, and the target design. Tagged `pre-modular-refactor`
  as a rollback point. (The refactor itself is on hold - capabilities kept
  getting added directly instead; the plan is still there when picked up.)

### Fixed
- Google served the automated browser a bot-detection page ("unusual
  traffic") almost immediately when used for search - switched to
  DuckDuckGo's plain-HTML endpoint rather than attempting to bypass an
  anti-abuse mechanism.
- "open youtube and play X" was being swallowed by the generic app-launcher
  pattern and falling back to a literal Google search in the *system's
  default browser* (not the automated one) - added explicit YouTube search-
  and-click-first-result routing, and made the app-launcher's fallback route
  through the one controlled browser instead of spawning a second one.
- "shutdown vortex" set internal flags but never stopped the `pystray` tray
  icon, so the process lingered as a zombie - stopped listening, never
  actually exited. Unified the voice-triggered shutdown and the tray's Exit
  item into one real shutdown path.
- Barge-in cut off speech with total silence and no acknowledgment - only a
  fresh standby wake spoke "Yes Boss?". Both paths now speak it, so
  cutting off mid-sentence has an audible confirmation the interruption
  registered.
- `BARGE_IN_THRESHOLD` had briefly been raised *above* `WAKE_THRESHOLD` as an
  unevidenced "safety margin," which made genuine interruptions unreliable -
  every observed false trigger had happened in standby, never during
  barge-in, and barge-in is inherently harder to score high on (the mic
  hears VORTEX's own speech too). Reverted to below `WAKE_THRESHOLD`.

### Changed
- README/IMPLEMENTED.md/docs/LEARNING_GUIDE.md trimmed to cover only the
  phases with real code, rather than describing the full 17-phase roadmap
  up front - each phase's documentation now lands in the same commit as its
  code.
