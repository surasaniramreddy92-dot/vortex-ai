# Changelog

Dated, day-by-day record of what actually landed in the repo. Each entry is
written the same day the work happens and pushed same-day going forward —
this file is the fast way to see what changed and when without reading full
commit diffs. Phase-by-phase status (not date-based) lives in
`IMPLEMENTED.md`; this file is chronological.

## 2026-08-16

### Fixed (third pass, after a reboot)
- **Barge-in could be logged as "triggered" and then take 15-25+ seconds to
  actually silence VORTEX** - two real, distinct interrupt-propagation gaps,
  found by adding precise timing diagnostics rather than guessing: (1)
  `ask_llm_stream`/`_stream_llm_answer` consumed Ollama's token stream with a
  plain `for part in stream:`, which blocks on the network read for the next
  token and only re-checks `stop_speaking` once one arrives - deadly on a
  cold-loaded model (this session's model failed to warm at boot because
  Ollama itself wasn't up yet). (2) `_synth()` awaited the edge-tts network
  call directly (`run_until_complete`), same blind spot for however long one
  chunk's synthesis took - live evidence: a barge-in that landed while a
  chunk was still being synthesized (not yet playing) took ~9s to register,
  matching one edge-tts round trip almost exactly. Both fixed by applying the
  same producer-thread-plus-polled-queue pattern `_speak_chunks` already used
  for TTS playback: a background thread does the blocking network I/O, and
  the generator polls a queue every 0.1s, checking `stop_speaking` on every
  poll regardless of whether new data has arrived. New shared helper
  `_poll_stream` used by both LLM call sites; `_synth` now runs synthesis as
  a cancellable `asyncio` task instead of a plain awaited coroutine.
- Added a hard cap on response length - `VORTEX_LLM_MAX_TOKENS` (default 60,
  via Ollama's `num_predict`) - independent of the system prompt's "short
  spoken sentences" instruction, which the model doesn't reliably follow
  (observed: single-sentence answers running 230+ characters, ~14s to speak).
  A shorter response directly shrinks the window where VORTEX's own voice can
  mask a real "Hey Vortex" barge-in attempt (the near-field self-noise
  problem diagnosed earlier today) - unlike volume or threshold tuning, this
  doesn't trade off against anything else being audible or sensitive enough.
  Verified respected at the Ollama level directly; 60 tokens (~45-50 words)
  turned out not to be a very aggressive cap in practice for factual
  questions - a real, tunable lever, not a complete fix on its own.
- Added temporary timing diagnostics: `_play()` now logs how long each chunk
  actually played and whether it was cut short by `stop_speaking`. This is
  what surfaced both bugs above - a barge-in was logged as "triggered" with
  no matching `_play` log line at all for the chunk in flight, proving the
  interrupt was landing before `_play()` was ever reached, not inside it.
- Found in the same session, not yet investigated: VORTEX's own scheduled-
  task autostart raced Ollama's own startup after a reboot - "Model warm-up
  failed... Failed to connect to Ollama" appeared at boot, meaning the first
  real request of the day paid a full cold-load cost. Unlike the Postgres/
  Qdrant connections (already timeout-guarded), warm-up failure isn't
  currently retried once Ollama does come up.

### Fixed (second pass)
- **0.75 still wasn't low enough** - real attempts kept landing at 0.70-0.74
  and missing. A full-session histogram (397 scored frames) showed no clean
  valley between "background" and "genuine speech": real attempts spread
  roughly across 0.5-0.95 rather than clustering tightly above one obvious
  cutoff, meaning this wake model's confidence for this user's actual voice
  runs lower overall than it did for the synthetic clips it was validated
  against. Lowered `WAKE_THRESHOLD` further to `0.65` - a pragmatic,
  evidence-based compromise, not a clean fix. The real fix is very likely
  retraining/revalidating the wake model against this user's real voice
  (`tools/wakeword/build_hey_vortex.py` currently only uses synthetic TTS
  clips), which hasn't been done. Also lowered `BARGE_IN_THRESHOLD` to match
  (`0.65`) - it was about to become *stricter* than `WAKE_THRESHOLD`, which
  `main.py`'s own comment on that constant documents as a previously-fixed
  bug (an earlier stricter-barge-in attempt broke real interruptions, since
  barge-in is inherently harder to score high on). Verified restart caught a
  wake immediately (score 0.732 - would have missed both 0.75 and 0.8).

### Fixed
- **`WAKE_THRESHOLD` (0.8) was too strict for real human voice, causing "Hey
  Vortex" to frequently not register at all** - 0.8 was calibrated only
  against synthetic TTS clips (`tools/wakeword/validate_hey_vortex.py`),
  never against a real mic. Live evidence from today's session: 74 separate
  real "Hey Vortex" attempts scored between 0.65 and 0.84 and never
  triggered - genuine attempts landing just under 0.8, not background noise
  (the session's median score was 0.46, nowhere near this band). Lowered to
  0.75, matching `BARGE_IN_THRESHOLD`, which had already proven reliable in
  live use at that level the same day. Verified immediately after: two
  wake triggers at 0.984 and 0.977, both followed by strong real captures
  (raw RMS 9585 and 13542) and working exchanges. Trade-off: standby false
  positives were already a known open issue before this and a lower
  threshold makes that more likely, not less - considered worth it, since
  a wake word that doesn't wake is a worse failure mode.
- Added temporary diagnostic logging to `capture_command()` (duration + raw
  RMS of the captured audio, logged even when `recognize_google()` fails) -
  used to confirm the "doesn't understand what I said next" failures are a
  real, low-signal capture problem (RMS 144-283, near silence) and not a
  code-level bug: on the same live test, RMS 9585 and 13542 captures both
  transcribed successfully. Root cause of the low-signal captures
  themselves (timing right after "Yes Boss?", mic distance, or something
  else) is not yet identified - this logging is what the next investigation
  will use.

### Fixed (revert)
- **`VORTEX_TTS_VOLUME`'s default of `0.6` (added earlier today as a barge-in
  mitigation, see below) made VORTEX effectively silent in real use** -
  confirmed live: after restarting with the lower default, "Hey Vortex" got
  zero audible response ("Yes Boss?" was being logged as spoken but never
  heard). A standalone pygame test proved `set_volume(0.6)` itself doesn't
  throw and does play audio for the correct duration, so this wasn't a code
  exception - the reduced level just wasn't enough headroom above this
  machine's actual output/room conditions to be perceptible, despite sounding
  reasonable in isolation. Reverted the default back to `1.0` (unchanged,
  full volume). The env var and the underlying self-noise mitigation
  mechanism stay in place for anyone who wants to opt in and tune it
  (`VORTEX_TTS_VOLUME=0.6` or similar via `.env`), but it is no longer
  silently on by default - a barge-in improvement nobody can hear is worse
  than no improvement at all.
- **Confirmed, via the same live session, a real and separate problem**:
  wake detection and "Yes Boss?" both work reliably at full volume, but the
  follow-up command very often fails to transcribe (`Capture error:
  UnknownValueError` immediately after almost every "Yes Boss?") - this is
  what actually reads as "not responding" day to day, distinct from both the
  volume regression above and the barge-in self-noise issue. Not yet fixed;
  under active investigation.

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
- **Diagnosed why "wait wait wait" and even a repeated "Hey Vortex" didn't
  interrupt VORTEX during a real long response**: live log evidence showed
  zero `[diag]` score lines - not even near-misses - during a 25-second
  window while the user was actively trying to interrupt. On a laptop the
  mic and speakers sit close together, so VORTEX's own TTS output dominates
  whatever the mic hears while it's talking; `_agc` cannot fix this because
  it scales the whole mixed signal uniformly and has no way to separate two
  overlapping voices in one channel (that requires true acoustic echo
  cancellation, not implemented). Mitigated by turning VORTEX's own output
  down while speaking - `VORTEX_TTS_VOLUME` (default `0.6`) is now wired
  through `VortexConfig` into `pygame.mixer.music.set_volume()` in `_play()`.
  This is a partial mitigation (better self-noise ratio for "Hey Vortex"
  specifically), not a fix for generic words like "wait" - those are not,
  and still are not, checked at all; only the trained wake phrase triggers
  an interrupt. Full AEC would be required to make arbitrary speech
  interrupt reliably, and is out of scope for now.
- Confirmed (while investigating the above) that the AGC's noise floor
  starts every process launch at a hardcoded `250.0` and only decays back
  toward the true ambient floor via a slow exponential average (2%/update)
  - a deliberate asymmetry (see `_agc`'s docstring) since starting too low
  would let the floor get permanently stuck, boosting noise into false
  triggers. Side effect: for roughly 1-2 minutes after every restart, wake
  scores run measurably lower than steady-state (observed live: `noise_floor`
  201 -> 102 -> 78 -> 21 -> 0 over ~2.5 minutes, with scores climbing
  alongside it). Not a regression and not related to the self-noise issue
  above - VORTEX runs as a persistent background process in normal use, so
  this transient is only visible right after a fresh restart, which is
  exactly when today's re-testing kept hitting it.

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
- **Refactor Step 2** (`docs/REFACTOR_PLAN.md`): `main.py`'s module-level
  constants now come from one `VortexConfig` instance instead of their own
  scattered `os.getenv(...)` calls (19 values, including a previously-missed
  `VORTEX_EMBED_MODEL` lookup inside `_warm_up_models`). Still module-level
  constants, not `self.config.*` access throughout the class - that's a
  separate, larger change (Step 3+, extracting subsystems into their own
  files). Verified programmatically that all 19 values match both
  `config.py`'s independent computation and the exact hardcoded values
  `main.py` had before this change - zero drift - plus a live restart
  (greets, connects to Qdrant, warms up both Ollama models identically).

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
