# Changelog

Dated, day-by-day record of what actually landed in the repo. Each entry is
written the same day the work happens and pushed same-day going forward —
this file is the fast way to see what changed and when without reading full
commit diffs. Phase-by-phase status (not date-based) lives in
`IMPLEMENTED.md`; this file is chronological.

## 2026-08-17

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
