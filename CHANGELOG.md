# Changelog

Dated, day-by-day record of what actually landed in the repo. Each entry is
written the same day the work happens and pushed same-day going forward —
this file is the fast way to see what changed and when without reading full
commit diffs. Phase-by-phase status (not date-based) lives in
`IMPLEMENTED.md`; this file is chronological.

## 2026-09-02

### Added — custom voice training infrastructure (direct user request: "I want to train a new voice model")

After prosody tuning and a voice comparison (both below) still left the
voice sounding like a commercial TTS engine, the user wanted a real
custom-trained voice on their own recorded voice - not a clone of Emma or
any other commercial voice (a real legal problem, not just technical).

Checked hardware first: no NVIDIA GPU on this machine (Intel integrated
graphics only). Training a real generative voice model on CPU is
documented to be dramatically slower than GPU - the user chose to proceed
anyway, accepting a "working first pass, not polished" result, the same
honest framing `tools/wakeword/build_hey_vortex.py` already uses.

- New `tools/voice_training/` - mirrors `tools/wakeword/`'s own precedent
  exactly: a new `voice-training` pyproject extra (`torch`, `lightning`,
  `librosa`, `pysilero-vad`, `jsonargparse[signatures]`), isolated from
  `src/vortex/` entirely. Needs zero VORTEX source changes to integrate
  later - the runtime already resolves any named offline Piper voice via
  `VORTEX_OFFLINE_TTS_VOICE`/`VORTEX_OFFLINE_TTS_MODEL_DIR`.
- `script.py` (155 hand-composed, phonetically varied sentences),
  `record.py` (a resumable recording helper the user runs themselves -
  there's no way to participate in live audio recording from this side).
- **Two real bugs in the published `piper-tts==1.7.0` package found and
  fixed, both confirmed by actually running the pipeline against a
  throwaway fake dataset, not assumed:**
  1. `SileroVoiceActivityDetector.process_array` doesn't exist in any
     currently-published `pysilero-vad` release (checked 3.4.0 and 3.0.0
     directly) - `process_samples` is the same-shaped equivalent, patched
     in by `train.py`.
  2. The Cython source (`core.pyx`) for the required monotonic-alignment
     extension is missing from the PyPI wheel entirely (checked 1.7.0 and
     1.6.1 - present in neither) - no C compiler would have fixed this,
     there was nothing to compile. Fetched the real source directly from
     the project's GitHub repo via a raw HTTP request (deliberately not
     an LLM-summarizing fetch - algorithmic source must be exact, not
     paraphrased), ported it line-for-line to Numba
     (`_monotonic_align_numba.py`, already an indirect dependency via
     `librosa`), and verified correctness against hand-computable cases
     (5 new tests) before trusting it with real training.
- **Live-verified end to end**: a real training step (`fast_dev_run`)
  completed successfully against the fake dataset - dependencies resolve,
  phonemization works, the model builds, a real forward/backward pass
  runs. This proves the pipeline works; it cannot and does not produce a
  usable voice (2-3 synthetic clips is nowhere near enough data) - deleted
  after the test, not mistaken for real progress.
- **Explicitly not done yet**: the user hasn't recorded the real script,
  so no real voice model exists. Automated MOS-based checkpoint scoring
  was deliberately left disabled (`torchaudio` has no release matching
  the pinned `torch==2.14.0` yet, and forcing a mismatched pair risked
  destabilizing a pipeline that took real debugging to get working) -
  checkpoint selection will rely on the already-working `val_mel` metric
  plus the user's own ears, the intended final check regardless.
- `.gitignore` updated so the real recorded dataset, checkpoints, and
  exported models (personal data / large binaries) never get committed.

### Added — TTS prosody controls (earlier the same day, direct user feedback: "the voice itself sounds robotic")

Checked first whether the offline Piper fallback (more robotic-sounding
than edge-tts) was silently doing the real work - it wasn't; the log
showed it's only ever pre-warmed, never actually triggered for real
speech. The real primary voice (`en-US-AvaMultilingualNeural` via
edge-tts) had never been given anything but bare text - `voice/tts.py`'s
`_synth()` called `edge_tts.Communicate(text, self.voice)` with no rate or
pitch at all, i.e. Microsoft's raw default delivery.

- `VortexConfig.tts_rate`/`tts_pitch` (`VORTEX_TTS_RATE`/`VORTEX_TTS_PITCH`)
  now thread through to edge-tts's real SSML prosody parameters. Default
  rate `-10%` - a commonly-cited, moderate adjustment for a calmer, less
  rushed cadence. Pitch stays at `+0Hz` on purpose (an arbitrary global
  pitch shift on an already-natural neural voice tends to sound uncannier,
  not warmer).
- **Honest scope**: unlike `wake_threshold`, this default was NOT
  independently verified through repeated real-listening trials -
  judging whether synthesized speech sounds more human needs the user's
  own ears, not something checkable from this environment. What IS
  verified: the wiring is real, not cosmetic - the same sentence
  synthesized at `+0%` vs `-10%` produced measurably different audio
  (5.06s vs 5.71s, +12.8%), proving the parameter reaches Microsoft's API.
- Real trade-off, disclosed: a slower rate means every utterance takes
  marginally longer, very slightly widening the near-field self-noise
  window barge-in already struggles with - kept modest specifically
  because of that.
- 4 new unit tests (`test_tts.py`, `test_config.py`), full regression
  suite stayed green.

### Changed — default voice, same day, direct user choice after real comparison

Generated 5 real samples of the same sentence (current voice + 4
candidates) instead of guessing which would sound more natural - all
Microsoft's own "Conversation/Copilot"-tagged voices specifically, not the
more commonly name-recognized Aria/Guy, which checking real voice metadata
showed are tagged for News/Novel narration, not assistant conversation.
User picked `en-US-EmmaMultilingualNeural` (tagged "Cheerful, Clear,
**Conversational**" - the only candidate with that exact tag) over the
previous default, `en-US-AvaMultilingualNeural`.

Updated both `config.py`'s code default and this machine's own `.env`,
which had the old voice pinned explicitly - caught by live-verifying the
actual running value (`v.tts.voice`) after the code change, not assumed
from the source edit alone.

## 2026-09-01

### Added — Standby/Activation/Personality/Owner-Context foundation

Direct user request for the foundation of a future Jarvis/Friday-level
personality layer, explicitly scoped as *foundation only* - not the full
multi-model/multi-agent system. Full architectural analysis, two real
conflicts surfaced and resolved with the user before any code was written
(see `IMPLEMENTED.md`'s new N/A row for the complete reasoning):

- **"Stand down"** (`core/intent_router.py`'s new `StandDown` intent): ends
  the active session immediately and silently, back to standby, WITHOUT
  killing the process - kept deliberately separate from the existing
  `"shutdown vortex"` (full process exit, unchanged, still tested and
  working exactly as before). New `Session.end_session_now` Event
  (`voice/session.py`), additive in exactly the same way `in_active_session`
  was added in the Step 7 refactor - doesn't touch barge-in's
  speaking/stop_speaking timing at all.
- **Configurable activation text**: `'Yes Boss?'`/`"Yes Boss, I'm
  listening."` were hardcoded literals in `voice/session.py` - now
  `VortexConfig.activation_response`/`barge_in_response`
  (`VORTEX_ACTIVATION_RESPONSE`/`VORTEX_BARGE_IN_RESPONSE`), defaults
  unchanged.
- **`core/personality.py`**: `PersonalityMode` enum (professional/friendly/
  witty/protective/demo), switchable via "switch to X mode". One real
  integration point - `ask_llm_stream()`'s system prompt now runs through
  `build_system_prompt()` instead of a flat constant. **Live-verified**
  against real Ollama: the same question in PROFESSIONAL vs. WITTY mode
  produced two different real answers - the wiring genuinely reaches the
  model (the tonal difference itself was real but subtle with
  `llama3.2:1b`, honestly noted rather than oversold).
- **`core/owner_context.py`**: thin single-owner identity object -
  `session_state`/`personality_mode` are live-delegating properties, not
  copied fields (avoids the exact stale-snapshot problem
  `core/state_manager.py` already documents for `VortexState`).
- **`core/social_context.py`**: a deliberately simple, rule-based
  `classify()` (technical criticism / friendly teasing / owner-directed
  disrespect / genuine abuse / ambiguous / normal) feeding an additional
  directive into `build_system_prompt()`. Explicitly NOT sentiment analysis
  or a trained model - fails closed to AMBIGUOUS/NORMAL whenever unsure.
  Structural guarantee tested across all 30 mode×label combinations: no
  combination this code can produce ever yields an aggressive, insulting,
  or threatening directive.
- **`core/state_manager.py`**: added one new `EXECUTING` state - not the
  spec's literal `PROCESSING`+`EXECUTING` pair, since intent classification
  is a sub-millisecond regex match in this codebase, not an observably
  separate phase from dispatch. Documented as a deliberate scope
  adjustment, not an oversight.
- **`presentation_mode`**: derived read-only property
  (`personality_mode == DEMO`), not a second independently-settable flag.
- 42 new unit tests (`test_personality.py`, `test_owner_context.py`,
  `test_social_context.py`, `test_lifecycle.py`, plus extensions to
  `test_state_manager.py`/`test_intent_routing.py`/`test_registry.py`/
  `test_config.py`). Full regression suite (280 pre-existing + new) stayed
  green throughout.

### Fixed (found during direct user re-testing, same day)

- **"VORTEX, demonstrate yourself" didn't do anything** - the literal
  phrase the feature itself is named after fell straight through to the
  plain LLM fallback, since only the more mechanical "switch to demo mode"
  wording was actually routed. Added a dedicated `_DEMO_TRIGGER` pattern
  (`intent_router.py`) for `"demonstrate yourself"` / `"show me what you
  can do"` / `"give me a demo(nstration)"`, mapped to the same
  `SetPersonalityMode(mode='demo')` intent. **Live-verified end to end**:
  after saying it, a real question about internal tools/architecture got
  back a real Ollama answer that declined to discuss internals, unprompted
  - the DEMO directive actually shaping real output, not just present in
  the prompt on paper. New tests in `test_intent_routing.py`/
  `test_lifecycle.py`.

### Added — real self-demonstration content (same day, direct user request)

Routing to Demo mode only changed the *tone* of future answers - it never
actually said what VORTEX can do, how it was built, the conversation
history with the user, future plans, or current drawbacks, despite the
user explicitly asking for all of that when demonstrating itself.

- **First attempt (rejected on live evidence):** a grounding prompt with
  all the real facts, asking the LLM to narrate them conversationally.
  Live-tested against `llama3.2:1b`: it recited a few of ~29 raw capability
  strings near the start of the prompt, silently dropped the build-history/
  relationship/plans/drawbacks sections entirely, and closed with "Would
  you like to proceed with any of these actions?" - misreading a self-
  introduction as an action menu. A raised token budget alone didn't fix
  the earlier truncation either (`"...and I can tell"` cut off mid-
  sentence) - same class of unreliability already documented for this
  model elsewhere (tool-calling hallucinations).
- **Redesigned as fully deterministic** (`core/self_knowledge.py`, no LLM
  call): a curated, hand-written capabilities overview (cross-checked
  against `intent_router.ALL_INTENT_TYPES` via a test so it can't silently
  drift), a short build-history paragraph, real conversation-history stats
  from a new `MemoryStore.stats()` (`turn_count`/`first_turn_at` - real
  SQL, not invented numbers), the same honest future-plans/drawbacks
  content - spoken as one complete paragraph via `Vortex.demonstrate_self()`.
- **Live-verified, real output**: *"...We've exchanged 490 messages
  together since 2026-08-02 11:36:25..."* followed by real future plans and
  drawbacks, complete and never truncated.
- 12 new unit tests (`test_self_knowledge.py`, `test_memory.py`), full
  regression suite stayed green throughout.

### Fixed (same day, direct user report: "barge-in fails during the demonstration")

Real log evidence, not a guess: the wake detector logged zero diagnostic
scores - not even a low failed attempt - for the entire ~107 seconds of the
(then-unbroken) self-introduction utterance. The instant VORTEX fell
silent afterward, a wake attempt succeeded within milliseconds (score
0.752, immediately triggered). This is this project's already-documented
near-field self-noise limitation (VORTEX's own voice masking the mic - see
`tts_volume`'s docstring), but no feature had ever produced continuous
speech this long in one unbroken block before, so nothing had stress-tested
it this severely.

- `core/self_knowledge.py`'s `build_demo_segments()` now returns the five
  topics as a list instead of one joined string (`build_demo_speech()`
  kept, now just `' '.join()` of the same list, for callers that want the
  full text).
- `Vortex.demonstrate_self()` speaks each segment as its own utterance with
  a real, configurable pause between them (`VortexConfig.demo_segment_pause`
  / `VORTEX_DEMO_SEGMENT_PAUSE`, default 0.6s - a judgment call, not
  independently re-derived through repeated trials the way `wake_threshold`
  was) and checks `stop_speaking` between segments, so a barge-in
  registered after one topic stops the rest of the introduction instead of
  continuing regardless.
- Honest scope: doesn't fix the underlying acoustic problem (true acoustic
  echo cancellation still isn't implemented) - gives the wake model several
  genuine quiet windows across the introduction instead of zero.
  Live-verified the pauses are real (~0.59-0.61s measured gaps between
  segment calls); the *acoustic* improvement itself needs the user's real
  microphone to confirm, not something checkable from this environment.
- 5 new/updated unit tests, full regression suite (344 passed, 1 hardware
  deselected) stayed green.

### Fixed (still failed on real re-test, same day - "still doesn't process")

The fix above only paused *between* the five broad topics. `CAPABILITIES_SUMMARY`
alone is six sentences (~25-30s of continuous speech, zero internal breaks)
- exactly the block a user naturally tries to interrupt first. Real log
evidence confirmed it: six "Speaking:" lines fired back-to-back with no
real gap, all from inside that one topic-level segment.

- `core/self_knowledge.py`'s `build_demo_segments()` now splits every
  constant down to individual sentences (`_sentences()`, a small regex)
  instead of five topic blocks - 13 sentences total, each getting its own
  pause, not just 5 topics.
- Live-verified the real segment count (13) and ~0.59-0.61s gaps after
  every sentence, not just between topics.
- Same honest caveat as before: doesn't fix the underlying acoustic
  problem, just gives many more genuine quiet windows than either previous
  version.
- 2 more updated unit tests, full regression suite (345 passed, 1 hardware
  deselected) stayed green.

### Fixed (two more real bugs, same day, direct user re-testing)

- **Follow-up question hijacked into the full self-introduction**: "can you
  give a demonstration on that" (referring to VORTEX's own PREVIOUS
  answer) matched a too-loose `_DEMO_TRIGGER` bare pattern ("give a
  demo(nstration)" with no object) and restarted the entire 13-sentence
  self-introduction, completely ignoring what "that" referred to. Fixed by
  requiring an explicit "of yourself"/"of what you can do" object on that
  branch - a bare "demonstrate"/"give a demonstration" now correctly falls
  through to the LLM as an ordinary follow-up instead of being assumed to
  mean "introduce yourself again."
- **"What makes you different" hallucinated a fake capability**: asked
  with no dedicated handling, the plain LLM fallback fabricated *"I
  possess a unique ability to understand and respond to subtle emotional
  cues"* - not a real VORTEX capability. New `WhatMakesYouDifferent` intent
  routes this to `core/self_knowledge.py`'s new `DIFFERENTIATION_SUMMARY`
  instead - spoken directly, no LLM call, every claim independently
  checkable against the actual codebase (local model, tested code with
  public history, docs that say "Partial" instead of overclaiming) rather
  than generic assistant-marketing language.
- Live-verified end to end: the real, honest answer is now given, and the
  follow-up phrase confirmed to no longer touch `personality_mode`.
- 6 new/updated unit tests, full regression suite (350 passed, 1 hardware
  deselected) stayed green.

## 2026-08-19 (continued into 2026-08-20)

### Added — security, memory retrieval, tool-calling (in that priority order, per direct user request to finish all three as fast as honestly possible)

- **Dependency vulnerability scanning** (`26ae19d`, fixed `046b6dc`): `pip-audit`
  added as a CI step and to the `dev` extra. Found and fixed real CVEs:
  Pillow 12.2.0 → 12.3.0 (15 CVEs, both the `windows` and `documents`
  extras). The new step itself then failed on a genuinely fresh CI runner
  (not locally, where the dev venv's setuptools was already newer) - 7 CVEs
  in CI's own bundled setuptools 65.5.0 - fixed by explicitly upgrading
  `pip`/`setuptools` in the install step, same category of "only a real
  fresh runner catches this" bug as the four portability fixes on
  2026-08-18.
- **Conversation memory retrieval** (`3ddc87e`) - closes the exact gap
  `IMPLEMENTED.md`'s Phase 5 row had flagged since 2026-08-16
  ("no retrieval over it, just chronological recall"). `rag.py`'s
  `RagStore` gained a second Qdrant collection (`vortex_conversation_turns`)
  indexed via a background daemon thread as each turn is added
  (`app.py`'s `_index_turn_async` - deliberately off the `ask_llm_stream`
  critical path, since that same thread feeds TTS and a synchronous embed
  call would add real latency to a live barge-in-sensitive conversation).
  "Do you remember X" / "what did I tell you about X" now retrieves the
  most relevant past turns (dense-only search, no BM25/rerank - short
  conversational text doesn't need document retrieval's hybrid pipeline)
  and answers via the LLM. **Live end-to-end verified**, not just mocked:
  told VORTEX "my favorite programming language is Rust because of its
  safety guarantees," waited for the background index, then asked "do you
  remember my favorite programming language" and got back a real, correct
  Ollama answer citing Rust's safety guarantees. 8 new unit tests
  (`test_memory_retrieval.py`) plus 4 more in `test_registry.py` covering
  `recall_memory`'s three degrade-gracefully paths (`rag` unavailable,
  search failure, no relevant turns found).
- **Structured tool-calling infrastructure** (Phase 4 gap, uncommitted as of
  this entry - see below) - `src/vortex/llm/tools.py` (4 safe non-destructive
  tool schemas: `open_app`, `web_search`, `get_time`, `get_date`),
  `LLMProvider.chat_with_tools`/`OllamaProvider.chat_with_tools`, and
  `Vortex._try_tool_call` (only reached when the deterministic regex router
  found nothing). **Live-tested against every model on this machine before
  deciding how to ship it:** `llama3:latest` and `phi:latest` both reject
  Ollama's `tools` parameter outright (HTTP 400, "does not support tools");
  `llama3.2:1b` (the only one that accepts it) hallucinated a tool call for
  a plain unrelated question ("what is the capital of France" → called
  `get_date`) and, on topically-right phrases, echoed the parameter's own
  JSON schema back instead of the extracted value. Real course-correction,
  not a shipped feature: rather than enable this, it lands **off by
  default** (`VortexConfig.llm_tool_calling_enabled`,
  `VORTEX_LLM_TOOL_CALLING_ENABLED=false`), with `tool_call_to_intent()`
  defensively failing closed (returns `None`, dispatches nothing) on any
  malformed argument shape - confirmed live against the exact
  schema-echoed-back failure mode observed above. 19 new unit tests
  (`test_tools.py`, `test_registry.py`) cover the mapping/fail-closed logic
  and `execute()`'s flag-off/flag-on/malformed-call/no-call/request-failure
  paths.

### Also this stretch (2026-08-19)

- **Gmail check/reply** (`0ac1804`) - first slice of Communication
  (auto-reply, direct user request, scoped to email only, voice-triggered
  only). See `IMPLEMENTED.md`'s Communication (Email) row for the full
  writeup; not yet live-verified against a real Gmail account since that
  needs a one-time OAuth consent only the account owner can do.
- **Documentation drift fixed**: `README.md`/`IMPLEMENTED.md`/
  `docs/ARCHITECTURE.md`/`docs/LEARNING_GUIDE.md` had fallen behind the
  2026-08-18 refactor completion (still describing `main.py` as a
  god-object, "no tests/CI" language, a stale Phase 0 status, a missing
  Phase 8 row). Fixed, plus an explicit disambiguation callout added to
  both README and IMPLEMENTED distinguishing `docs/REFACTOR_PLAN.md`'s
  internal Steps 0-10 (code-organization refactor, fully done) from the
  master feature roadmap's Phase 0-16 table (still mostly Partial, for
  real, specific reasons). Phases 2, 3, and 7 bumped from Partial to
  **Implemented (v1)** based on a genuine re-assessment against live
  evidence already gathered this session, not a relabeling exercise.
- **Real Ollama server outage fixed** (the user's actual running instance,
  not a code bug): a zombie old Ollama install (v0.18.2 at `E:\Ollama`)
  never got its server ready and blocked the correct, newer auto-updated
  install (v0.32.14) from starting. Root cause of it recurring on every
  reboot: the Windows Startup shortcut pointed at the old install. Fixed by
  killing the stuck process, starting the correct install, and repointing
  the shortcut.

## 2026-08-17 (continued into 2026-08-18)

### Fixed (severe regression - direct user report: "it sometimes speaks without me calling for it")
- **VORTEX could hold entire fabricated "conversations" with itself, with no
  user input anywhere in them.** Root cause, confirmed directly from live
  logs: earlier the same day, `capture_command()` was changed to try the
  offline STT model as a "second opinion" whenever cloud STT returned
  `UnknownValueError` (see the "capture reliability" fix above) - correct
  and verified for the case it was built for (a real command Google
  mis-heard right after a fresh "Hey Vortex"). The gap: `_active_session()`
  keeps listening for follow-ups for up to `session_timeout` (18s) *without*
  requiring the wake word again, and that same offline "second opinion" was
  also being tried on every one of those follow-up captures - meaning any
  ambient sound during passive listening (room noise, a TV, adjacent
  conversation) that Google couldn't parse now also got fabricated into a
  plausible-sounding "command" by the offline model (a well-documented
  Whisper-family behavior). Executing *any* transcription, even a
  hallucinated one, resets the follow-up window - so this cascaded: one
  fabricated command produced a spoken response, which reset the listening
  window, which captured more ambient noise, which fabricated another
  "command," repeating until 18s of genuine silence finally occurred.
  Observed live, verbatim from the log: a long, uninterrupted run of
  entirely unprompted responses - "Did you know that there is a type of
  jellyfish that is immortal?", "It was a pleasure assisting you.", "I
  can't engage in conversations that promote or glorify violence against
  women." - none of them replies to anything the user actually said.
  **Fix:** `capture_command()` gained `allow_offline_on_unclear` (default
  `True`); `voice/session.py`'s `active_session()` now passes `True` only
  for the *first* capture in a session (right after the wake/barge-in
  acknowledgment - a deliberate, intentional signal from the user) and
  `False` for every continuation capture after that. A real network failure
  (`sr.RequestError`) is unaffected and still always tries offline,
  regardless of session state - only the `UnknownValueError` "unclear
  audio" path is gated. Verified: a direct test constructs a real `Session`
  object (not mocked) and asserts the flag sequence across a multi-command
  session is `[True, False, False, ...]`; live-tested that the first-capture
  offline fallback still works correctly (unaffected, "list files on
  desktop" still recovered via offline STT right after a fresh wake).
  167/167 tests pass, including 4 new ones covering both the gating logic
  and that `RequestError` stays ungated.

### Added (two direct user feature requests)
- **"Read my screen."** New `src/vortex/screen.py`: screenshots the current
  screen (`PIL.ImageGrab`) and OCRs it (reusing `documents.py`'s existing
  Tesseract probe/dependency rather than a second copy of the same "is OCR
  actually usable" check), then speaks the extracted text back. New
  registry entry (`read_screen`, matching "read my screen"/"read the
  screen"/"what's on my screen"), checked before `read_document`'s broad
  "read (?:me )?(.+)" catch-all so it doesn't get misinterpreted as "find a
  document named my screen." Verified directly: screen capture works for
  real (a genuine 1920x1080 screenshot). **Honest gap, same as document
  OCR:** actually reading text needs the Tesseract binary installed, which
  isn't set up on this dev machine - confirmed the code degrades correctly
  to a clear spoken explanation rather than silently failing or claiming it
  read something it didn't.
- **File-listing popup.** New `src/vortex/popup.py`: when `list_files`/
  `search_files` run, a small window opens showing each result as
  "filename (TYPE) - Location", at the same moment the spoken summary
  starts (both fire together in `h_list_files`/`h_search_files`, right
  before `self.speak(...)`), matching a direct request for a synchronized
  visual + spoken listing instead of a spoken-only one. Runs tkinter on its
  own dedicated thread with its own independent `Tk()` root - never shared
  across threads, which Tkinter doesn't support safely - so `show_file_popup`
  returns immediately and never blocks the voice worker thread waiting for
  the window to be closed. A new call closes any previous popup first, so
  repeated listings don't pile up windows. Verified directly: a real,
  visible window opened with the correct title while the caller returned
  in well under a second. Best-effort like every other optional feature in
  this project - a failure to open the window (no display, tkinter
  unavailable) logs and stops, it never breaks the voice response that's
  already speaking the same information.
- Both features gained real, direct unit test coverage: `tests/unit/
  test_screen.py` (12 cases covering the degrade-gracefully contract),
  `tests/unit/test_popup.py` (formatting + non-blocking + failure-handling),
  and new `test_registry.py` cases proving `read_screen` dispatches
  correctly and doesn't disturb the pre-existing `read_document` routing.
  164/164 tests pass.

### Fixed (direct user feedback after live testing)
- **File listing no longer reads back full paths, and now says which folder
  each file is in instead of a bare, unattributed name.** Direct user
  feedback: reading a raw path (`C:\Users\...\Desktop\report.pdf`) out loud
  is exactly the kind of thing that shouldn't be spoken - long, awkward,
  not actionable by ear. Turned out `list_files()` was already filename-
  only, never full paths - the earlier complaint was from before the
  `list_files` "on"/"in" matcher fix (commit `385b1c4`'s predecessor)
  landed, when "list files on desktop" fell through to the LLM fallback
  entirely and it hallucinated fake generic paths. What was genuinely
  missing: when combining multiple folders (`list_files(dir_name=None)`,
  e.g. plain "list files"), the result was a flat list of names with no
  indication of *which* folder each came from - not useful if you actually
  want to go find one. `files.list_files()` now returns `{'name', '
  location'}` entries instead of bare strings; `main.py`'s `h_list_files`/
  `h_search_files` speak "name, in Location" (e.g. "report.pdf, in
  Desktop") when multiple folders are involved, and just the plain name
  when a specific folder was already named in the query (repeating it for
  every file would be redundant). Verified live: "list files" (no folder
  named) now speaks each result with its folder attached.
- **Offline STT hallucination filter.** Separately found during the same
  testing session (not the user's report, but real evidence from live use):
  faster-whisper - a well-documented Whisper-family failure mode - can
  produce fluent-sounding but entirely fabricated text on quiet/unclear
  audio. Observed live twice: "thanks for watching, and i'll see you in the
  next video" and "hey, hey, hey, mister..." from audio that wasn't real
  intelligible speech. `_recognize_offline` now drops any segment with
  `no_speech_prob >= 0.6` (Whisper's own estimate the segment isn't real
  speech) before joining the transcript - verified directly against saved
  debug captures from tonight's real use: a genuine hallucination-prone
  segment (`no_speech_prob=0.72`, text "You") gets filtered, while a
  confident real segment (`no_speech_prob=0.07`, "Breathe in a little
  bit.") is kept. 0.6 is a conservative starting cutoff, not tuned against
  a labeled dataset.

### Fixed (capture reliability - root-caused with real evidence, not a guess)
- **Offline STT (faster-whisper) now also engages on `sr.UnknownValueError`,
  not just `sr.RequestError`.** Originally (2026-08-16) the offline fallback
  only triggered on a real network failure, on the theory that falling back
  to a smaller local model when Google *had* been reached but found the
  audio unclear would be a downgrade, not a fallback. Real evidence
  overturned that tonight: added a temporary diagnostic that saves the exact
  audio sent to Google whenever it returns `UnknownValueError`
  (`logs/debug_captures/`, `SpeechToText._save_debug_capture`). A live
  failed capture from real use was inspected directly - a clean, real-
  speech RMS envelope (627-8900 across the clip, not noise or silence),
  confirmed clipping-free - fed to Google directly (reproduced the exact
  same `UnknownValueError`) and to `faster-whisper` directly (transcribed
  it correctly: "So, I love you so much, I love you so much.", language
  probability 1.0). Google's cloud STT is, at least for this project's
  AGC-boosted audio profile, demonstrably *less* reliable than the
  "fallback" - so `UnknownValueError` now also tries offline, same as a
  network failure. `_save_debug_capture` is now called only when *both*
  cloud and offline fail to produce anything, since that's the genuinely
  unexplained case worth capturing for next time.
- **Offline STT is eagerly warmed at startup again; offline TTS stays
  lazy.** These stopped being symmetric the moment STT's trigger widened
  from "rare network outage" to "common unclear-audio miss" - leaving STT
  lazy would mean the first capture failure in every session pays a
  3.8-8s cold-load penalty on top of already having failed once, which is
  worse for responsiveness, not better. TTS's fallback trigger is
  unchanged (network failure only, still rare), so it stays lazy.
- **`WAKE_THRESHOLD`/`BARGE_IN_THRESHOLD` lowered from 0.65 to 0.60** and
  **offline models briefly made fully lazy then partially re-warmed**, both
  investigated with direct evidence tonight - see the "post-wave-2 live
  testing" section below for the full trail (isolated wake-model scoring at
  0.6499 on a real utterance, 478MB vs 249MB memory measurements). The
  wake-registration gap in synthetic testing mentioned there was NOT fully
  explained by threshold or memory alone; it turned out to be entangled
  with the capture-reliability problem fixed above in the same testing
  session - real human voice attempts scored and captured correctly
  throughout, confirmed live multiple times after these fixes landed
  (wake scores 0.640-0.809; a real "list files on desktop" command
  correctly captured, routed, and answered end-to-end).

### Fixed (post-wave-2 live testing)
- **`WAKE_THRESHOLD`/`BARGE_IN_THRESHOLD` lowered from 0.65 to 0.60.** Direct
  evidence, not a guess: ran the exact wake model + AGC pipeline in
  isolation (bypassing the live app entirely) against a real captured "Hey
  Vortex" utterance and got a max score of 0.6499 - a genuine attempt
  landing a hair's-width under the old 0.65 bar. The live process scored
  the same kind of attempt even lower still. Same accepted trade-off as
  every previous threshold reduction: standby false-positives become more
  likely, accepted because a wake word that doesn't wake is worse.
- **Offline STT/TTS fallback models (faster-whisper/piper-tts) no longer
  eagerly warm up at startup.** They were adding ~200MB of permanently-
  resident memory (measured: 478MB with eager warm-up vs 249MB without) for
  a fallback that exists for a network outage which hasn't actually
  happened once in extensive testing. Live testing found the live process
  scoring a real wake attempt much lower than the same audio scored in
  isolation - real evidence of resource contention, and the offline
  models' background thread pools (ctranslate2, onnxruntime, alongside
  openWakeWord's own onnxruntime session, all on the same 4 physical
  cores) are a plausible contributor now that they're permanently loaded.
  Both engines still lazy-load correctly on first real use if a genuine
  outage happens - this only removes the *eager* load at startup, trading
  a few extra seconds on the first real fallback for a lighter, more
  responsive process the rest of the time. Wake/barge-in responsiveness is
  this project's constant, everyday priority; the fallback is not.
- **Honest, unresolved finding from the same testing session:** even after
  both fixes above, live synthetic acoustic re-testing (via the disposable
  test scripts used throughout this project) still frequently failed to
  register any wake score at all, despite direct verification that (a) raw
  audio reaching the mic during these attempts was strong (max RMS 7069,
  measured via a standalone probe bypassing VORTEX entirely) and (b) the
  wake model scores this kind of audio correctly when run in isolation.
  Ruled out: threshold value, offline-model memory/resource contention (the
  fix above). Not yet root-caused: something specific to how the live,
  long-running process (many hours, many restarts in one session)
  interacts with repeated synthetic test playback specifically - real
  human voice attempts during the same session did register correctly
  (e.g. a live "Hey Vortex" scored 0.664 against the then-current 0.65
  threshold). Flagged honestly rather than claimed fixed; needs a live
  human-voice check to confirm the two fixes above actually helped.

### Added (wave 2 — three more phases, done in parallel and merged carefully)
Three more independent tracks of previously-deferred work, again via
isolated-worktree agents running in parallel (each touching a different
concern), each reviewed line-by-line and independently re-tested against
its own worktree before merging. Unlike the previous parallel wave, all
three agents touched `src/vortex/config.py` (and two touched
`src/vortex/main.py`/`pyproject.toml`) with independent additions - merged
by hand, field by field, rather than a blind file copy, specifically to
avoid one agent's work silently overwriting another's. Verified after
merging: `VortexConfig.from_env()` exposes every new field from all three
agents together, `pytest tests/unit/` is 146/146, and a full live restart
with Ollama + Qdrant both actually running (both were down after an
extended idle gap - restarted manually before testing) confirmed wake,
barge-in, and general Q&A all still work correctly together.

- **Phase 1 (Voice I/O): offline STT/TTS fallback.** Cloud stays primary
  (Google Web Speech / edge-tts, unchanged behavior when online); a local
  model only engages on a *network-reachability* failure specifically -
  `sr.RequestError` for STT, `aiohttp.ClientConnectionError`/
  `asyncio.TimeoutError` for TTS - never on "the service was reached but
  didn't like this audio/text" (`sr.UnknownValueError`, edge-tts's own
  `EdgeTTSException` family), since falling back to a worse local model for
  that would be a downgrade, not a fallback. Offline STT: `faster-whisper`
  `base.en` (CPU, int8) - chosen over `tiny.en` since measured warm latency
  wasn't meaningfully different (0.9-1.4s vs 0.5-1.5s) but accuracy is
  better. Offline TTS: `piper-tts` `en_US-lessac-medium`. Both lazy-load as
  an in-process singleton and are explicitly warmed (downloaded + loaded)
  from the existing `_warm_up_models()` hook, off the critical path, so a
  real outage isn't also the first download attempt. New optional extra
  `voice-offline` (not in `voice`/`all`) - kept separate for two verified
  reasons: real added install weight (`ctranslate2`+`av`+`piper-tts`
  wheels, before either engine's model weights are even downloaded) and
  `piper-tts` being GPL-3.0-or-later, a different license than the rest of
  this project's dependencies. One kill switch,
  `VortexConfig.offline_fallback_enabled`. 55 tests (17 new); real,
  non-mocked verification through the actual classes (a real edge-tts clip
  transcribed correctly offline; a real sentence synthesized to a playable
  WAV offline). Honestly not verified: an actual severed-network scenario
  end-to-end (this sandbox has no clean way to do that) - the trigger
  condition itself is verified via mocked exceptions, not a real outage.
- **Phase 2 (Desktop & OS Automation): file operations, search, structured
  audit log, capability registry.** New `src/vortex/files.py`: voice-
  triggered list/find/move/copy/rename/delete, scoped ONLY to
  `documents.SEARCH_DIRS` (Desktop/Documents/Downloads - reusing the
  existing path policy, not a second one that could drift), every resolved
  path verified to actually live under one of those directories before
  being touched (`PathNotAllowedError` otherwise, blocking `..`/absolute
  paths/symlinks). Delete goes through `send2trash` (Recycle Bin), never a
  permanent `os.remove`; move/copy/rename structurally refuse to overwrite
  an existing destination file. Delete/move/rename are gated behind the
  same `awaiting_confirmation` yes/no flow already used for shutdown/
  restart/close-all (now a dict, `{'action', 'path', ...}`, not a bare
  string - every assignment site updated consistently, verified no leftover
  string-format assumption remained anywhere). New `src/vortex/audit.py`:
  a separate JSON-lines audit trail (timestamp/action/target/outcome) for
  consequential actions, additional to (not replacing) the existing
  plaintext log. `execute()`'s if/elif dispatch chain was mechanically
  restructured into a list-based capability registry (`_build_registry`) -
  dispatch infrastructure only, not authorization/policy; every pre-
  existing command verified to still route to the exact same handler.
  104 tests (66 new). **Found live, after merging (not by the agent) and
  fixed:** the `list_files` matcher only accepted "list files in X", not
  "list files on X" - a natural phrasing a real live test actually used -
  which silently fell through to the generic LLM fallback instead of
  failing clearly, and the model hallucinated a plausible-looking but
  entirely fake file listing (generic `username`-placeholder paths, not
  real files). Broadened the matcher to accept both "in" and "on"; added a
  regression test (`test_list_files_matches_on_as_well_as_in`) proving both
  phrasings now reach the real handler, not the LLM fallback, and list a
  real file, not a hallucinated one.
- **Phase 5 (Memory & RAG): hybrid dense+sparse search, reranking.**
  `retrieve()` now fuses Qdrant's dense cosine search with BM25 keyword
  search (`rank_bm25`, pure-Python, only depends on numpy already pinned
  for the wake-word stack) via Reciprocal Rank Fusion - rank-only
  combination, chosen specifically because cosine similarity and BM25
  scores live on incomparable scales and a weighted blend would need ad-hoc
  normalization RRF avoids entirely - then reranks the fused pool with a
  keyword-overlap/exact-substring heuristic. **Deliberately not a cross-
  encoder reranker:** `sentence-transformers`' actual footprint was checked
  before deciding (`pip install --dry-run` resolved a 122MB torch wheel
  plus transformers/tokenizers/safetensors, with a further separate model
  download at first real use) - a real mismatch for an otherwise fully
  local, lightweight desktop app, so a cheap heuristic was used instead.
  Both stages independently off-switchable (`VORTEX_RAG_HYBRID_SEARCH`,
  `VORTEX_RAG_RERANK`). Conversation-memory migration onto this stack was
  considered and deliberately scoped out as too large/risky to do safely
  in the same pass - `memory.py` is untouched. **Incidental bug found and
  fixed while testing:** `ensure_ingested()` re-ingesting a changed
  document deleted the old Postgres chunk rows but never told Qdrant to
  drop the matching points, so a document's previous version's chunk
  vectors stayed indexed forever, silently polluting future dense search
  for that document - now scoped `qdrant.delete()` before re-upserting.
  `retrieve()`'s signature/return shape unchanged, so `main.py`'s one call
  site needed zero edits. 62 tests (22 new); live end-to-end verified
  against real Postgres+Qdrant+Ollama (Qdrant started temporarily via its
  scheduled task, stopped after) with an adversarial near-duplicate-keyword
  test document.

### Changed
- **Resumed the modular refactor (`docs/REFACTOR_PLAN.md`), on hold since
  Step 3: Steps 4, 5, and 6, each tested and pushed same-day.**
  - **Step 4 — LLM provider extraction.** `llm/provider.py` (abstract
    `LLMProvider.chat_stream`) + `llm/ollama_provider.py` (concrete
    `OllamaProvider`) now own the `ollama.chat` call and the queue/thread
    token-polling pattern that used to live inline in `main.py` as
    `_poll_stream`. `ask_llm_stream` and `_stream_llm_answer` (the
    document/RAG answer path, not named in the original plan but sharing
    the identical pattern) both now call `self.llm.chat_stream(messages)`;
    all error-handling/fallback-text/memory-bookkeeping logic is untouched.
    Verified live against real Ollama (real streamed reply, memory turn
    persisted), not just the mocked test suite.
  - **Step 5 — OS automation behind a platform adapter.** `platform/base.py`
    (abstract `PlatformAdapter`) + `platform/windows/*.py` (the concrete
    shutdown/restart/lock commands and the native-app/web-app/
    protected-process tables) separate *what* VORTEX does from *how*
    Windows does it; `tools/system/apps.py` and `tools/system/process.py`
    hold the ported-verbatim capability logic. Pulled two real gaps forward
    from their originally-planned later step instead of leaving them open:
    `protected_processes` grew from 12 to 19 entries (added the
    system-critical processes `CURRENT_STATE.md` §6 had flagged as
    missing - `svchost.exe`, `wininit.exe`, `smss.exe`, `spoolsv.exe`,
    `registry`, `fontdrvhost.exe`, `msmpeng.exe`), and
    `handle_confirmation`'s `'yes' in cmd` substring bug (same §6) was
    replaced with a real `_is_affirmative()` word-boundary check - a
    misheard "no, not yes I don't want that" now correctly declines instead
    of confirming a shutdown/restart/close-all/delete. `main.py`'s `psutil`
    and `subprocess` imports came out entirely, now unused there. Verified
    live: `_is_affirmative()` against the exact flagged bug phrase plus 9
    others, and a real `Vortex()` opening/closing a real Notepad process
    end-to-end. Shutdown/restart/close-all/lock deliberately not
    live-triggered (disruptive to a running session) - verified by code
    reading instead, since each is a verbatim port.
  - **Step 6 — capability registry + intent router.** `core/intent_router.py`'s
    `route(cmd)` is a genuinely pure function - 26 frozen Intent dataclasses,
    zero side effects, not even a `Vortex` instance needed - replacing the
    297-line, 24-entry fused matcher+handler chain that used to live in
    `main.py`'s `_build_registry`. `core/capability_registry.py`'s
    `CapabilityRegistry(host).dispatch(intent)` is the deliberately-impure
    dispatch half, every handler a direct port of the old closures; it
    asserts full dispatch coverage at construction time. `core/policy_engine.py`
    got `is_affirmative()` moved into it, completing the move Step 5's note
    promised. A fully general confirmation-policy engine (Phase 15 of the
    master roadmap) was deliberately not built - out of this step's actual
    scope. Three structural tests that inspected the old registry's internals
    directly were rewritten against the new shape; every behavioral test
    needed zero changes, since `execute()`'s external behavior is
    byte-identical. Verified live: a real `Vortex()` opening/closing a real
    Notepad process through `execute()` end-to-end, and the exact flagged
    confirmation-bug scenario correctly declining.
  - All three steps: full `pytest` suite green after each (214 tests by the
    end, 47 new), zero regressions; `REFACTOR_PLAN.md` updated in the same
    commit as each step with a "done" note documenting what actually landed
    and any judgment calls made along the way, same convention Step 3
    established.
  - Steps 7-10 (orchestrator/state manager, thin bootstrap, test hardening +
    CI, feature-parity checklist) remain - their mapping tables still
    describe the codebase as it looked when this plan was written.

## 2026-08-16

### Added (seventh pass — refactor + document intelligence, done in parallel)
Two independent tracks of deferred work, done via two isolated-worktree
agents running in parallel (touching disjoint files, so no merge conflicts),
each reviewed line-by-line and independently re-tested against its own
worktree before merging - and the fully merged result re-tested live one
more time before this commit, per the same "verify before pushing" rule as
every pass above.

- **`docs/REFACTOR_PLAN.md` Step 3 — voice subsystem extraction.** Pulled
  wake detection, TTS, STT, and session/event handling out of the `Vortex`
  god-object in `main.py` into `src/vortex/voice/` (`wake.py`, `tts.py`,
  `stt.py`, `audio.py`, `barge_in.py`, `session.py`), each a small class
  taking its dependencies as constructor arguments. Mechanical extraction
  only - every fix from earlier today (the cancellable-asyncio-task
  synthesis, the polled LLM-stream consumption, the `stop_speaking` check at
  the top of the session loop, the sounddevice capture path with its
  60-800-capped threshold, the distinct barge-in acknowledgment) is preserved
  verbatim, not "cleaned up" while moving. `_poll_stream` deliberately stayed
  in `main.py` (LLM-domain, not TTS - see REFACTOR_PLAN.md's Step 3 note);
  no `SpeechToText`/`TextToSpeech` interface abstraction was added, since one
  concrete implementation exists today and an abstraction layer added right
  after a hard-won debugging session risked losing a fix in translation for
  no near-term benefit. Verified: 14 tests (`tools/test_barge_in.py`'s
  scenarios newly ported into `tests/unit/test_barge_in.py`, mocked, no
  hardware) plus a full live acoustic test after merging - wake (score
  0.861), capture (`Heard: what is java`), and barge-in all fired correctly,
  with "Barge-in triggered" to "Speaking: Yes Boss, I'm listening." in 57ms -
  matching every timing measurement from before the extraction.
- **Phase 7 (Document Intelligence): OCR fallback + page/section
  provenance.** A PDF page whose native PyMuPDF text is under ~20 characters
  (the signal it's a scanned image with no text layer) is now rasterized and
  run through `pytesseract`/Tesseract, if the Tesseract binary is actually
  present - it is NOT installed on this dev machine, so this is verified only
  as far as "detects candidate pages and degrades gracefully" (logs once,
  keeps native text, never crashes), not as "recovers real text from a scan."
  Separately, `documents.extract_pages()` (new) returns page-tagged text for
  PDFs, heading-grouped sections for DOCX, per-sheet sections for XLSX;
  `rag.py` now chunks per page/section instead of one flattened string and
  stores `page`/`section` with every chunk (Postgres via an additive
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, so existing chunks are
  unaffected; Qdrant payload). `build_rag_prompt()` labels retrieved excerpts
  (`[Page 3]`) and asks the model to cite them. Verified end-to-end live
  (Postgres + Ollama already running, Qdrant started temporarily for the
  test and stopped after): ingested a synthetic 3-page PDF, asked a targeted
  question, got back correctly-tagged chunks and a real model answer citing
  the actual source page. Both features are off-switchable via
  `VORTEX_OCR_ENABLED`/`VORTEX_DOCUMENT_PAGE_NUMBERS`. `ensure_ingested()`'s
  signature is unchanged and `retrieve()`'s new dict-shaped return is handled
  defensively by `build_rag_prompt()` (dicts or plain strings both work), so
  `main.py`'s one call site needed zero edits.

### Changed (sixth pass)
- **Barge-in now speaks a distinct acknowledgment** - "Yes Boss, I'm
  listening." instead of the same generic "Yes Boss?" used for a fresh wake.
  Direct user feedback: hearing "Yes Boss?" right after VORTEX's own
  sentence gets cut off mid-word reads ambiguously (did it hear the
  interruption, or is this a coincidence?); the barge-in-specific phrase
  explicitly confirms the cutoff registered. Verified live: "Barge-in
  triggered" to "Speaking: Yes Boss, I'm listening." in 57ms, consistent
  with every other timing measurement since the fourth-pass interrupt fix.

### Fixed (fifth pass - tested live before pushing this time)
- Command capture's energy threshold had no upper bound - live evidence: one
  real capture calibrated to `energy_threshold=1947` purely from ambient
  noise/echo right after "Yes Boss?" finished, well above where real speech
  (and every successful capture logged all day) actually sits, meaning
  ordinary speech afterward could fail to ever cross it. Capped at 800.
- **The response-length cap wasn't actually keeping responses short.** 60
  tokens, then 40, both still let real answers run 14-17s - a raw token cut
  lands mid-sentence rather than at a natural stop, and doesn't reliably
  produce a shorter sentence to begin with. Root cause: the system prompt's
  vague "short spoken sentences" (plural, no limit) wasn't a strong enough
  instruction - repeated identical queries against the model directly showed
  9 to 24+ words for the same question, un-capped. Rewrote the system prompt
  to a concrete constraint ("ONE sentence, no more than 20 words"), and
  re-tuned `llm_max_tokens` down to 32 as a firm backstop (verified: 5
  repeated tries of the same question all completed with a proper sentence
  ending, 7-19 words, none cut off - vs. every previous cap still getting
  cut short at least once).
- **This time, every change here was restarted and re-tested live via the
  acoustic loopback script before committing, not after** - direct user
  feedback ("do a proper testing before pushing the code... only then push")
  after the fourth-pass fixes turned out to have their own live regression
  (a `WaitTimeoutError` from an over-calibrated threshold). Five consecutive
  live runs after this pass all showed "Barge-in triggered" to "Barge-in:
  yielding the floor" in under 75ms (25/42/51/72/37ms) - the core interrupt
  fix from the fourth pass holding up consistently, not a one-off. Near-field
  self-noise masking (VORTEX's own voice preventing a barge-in from ever
  being detected at all, not a delay) remains a real, separate, unresolved
  acoustic limitation - shortening responses reduces its window but doesn't
  eliminate it; true acoustic echo cancellation would be required for that.

### Fixed (fourth pass - the actual dominant cause)
- **Found the real, dominant reason barge-in could log as "triggered" and
  then take 15-25+ seconds to silence VORTEX: `_active_session()`'s loop
  never checked `stop_speaking` between turns.** The third-pass fixes above
  (LLM stream, TTS synthesis) were real bugs and did make the *current*
  response stop quickly - but `_active_session()` would then immediately
  start a brand-new, full-length `capture_command()` window for "the next
  follow-up," completely unaware a `'barge_in'` event was sitting in
  `self.events` waiting to be handled. That event - and the "Yes Boss?"
  acknowledgment - only got processed once that unrelated new listen window
  finally timed out on its own, which is exactly the 15-25s delay observed
  all session. Fixed with one check at the top of the loop: if
  `stop_speaking` is set (meaning the response we just spoke was cut off by
  a barge-in, not silence), return immediately instead of listening for a
  new command, so `_worker()`'s outer loop can process the pending event
  right away. Verified live: "Barge-in triggered" to "Barge-in: yielding
  the floor" dropped from 15-25+ seconds to **42 milliseconds**.
- Rewrote `capture_command()`'s audio acquisition to use the same
  `sounddevice` path the wake detector uses, instead of a separate PyAudio-
  based `sr.Microphone()`. Real captures via the old path were mostly RMS
  40-1000 (rarely transcribing) for the same mic/user/moment the wake stream
  reliably scored strong signal on - a standalone probe confirmed the new
  path itself captures cleanly (max RMS 8081 against a real spoken phrase).
  Uses simple energy-based VAD with a debounced onset (4 consecutive frames
  above threshold, not 1 - a first cut without this triggered on single
  noise blips and gave up before real speech even started) and a short
  pre-roll buffer so the committed clip doesn't clip the first syllable.
- `_speak_chunks()`'s `interrupted` bookkeeping missed the case where a
  chunk was cancelled mid-synthesis (discarded by the producer before ever
  reaching the playback queue) - `stop_speaking.is_set()` is now checked
  once more directly before returning, so "Speech interrupted" logs
  correctly regardless of which stage the interruption landed in.

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
