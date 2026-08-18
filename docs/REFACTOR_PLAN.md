# VORTEX — Refactor Plan

Companion to `docs/CURRENT_STATE.md` (what exists) and `docs/ARCHITECTURE.md`
(target design). This is the *sequence* — what changes, in what order, and
the exit criteria for each step. Steps 0-6 are done (see each step's own
"done" note for what actually landed and any judgment calls made along the
way); the refactor was on hold between Step 3 and Step 4 while capability
work (offline STT/TTS fallback, file ops, hybrid RAG search, OCR document
intelligence, screen reading, popups) shipped directly on top of `main.py`
instead. Steps 7-10's mapping tables below still describe the codebase as it
looked when this plan was written. Each step from here still waits for
explicit sign-off before starting.

**Hard rule for every step below:** run whatever tests exist, confirm no
regression in the "preserve these" feature list, report exactly which files
changed and why, and stop if anything that currently works stops working.

---

## Step 0 — Safety net (done as part of this audit turn)

- Tagged the current commit `pre-modular-refactor` so there's an immediate,
  named rollback point regardless of what happens next.
- This document set (`CURRENT_STATE.md`, `REFACTOR_PLAN.md`,
  `ARCHITECTURE.md`) is the map; nothing gets moved blind.
- Per your "most important rule": before any code in `main.py` is deleted or
  overwritten, it will first be copied to `legacy/main_working_baseline.py`
  so there is a literal, in-repo, always-available reference copy — not just
  a git tag someone has to know to check out.

## Step 1 — Project metadata & config foundation (proposed next step, awaiting approval)

**Goal:** make the repo pip-installable and typed-config-driven *without
changing any runtime behavior yet*. This is the lowest-risk possible first
step — it adds files, it doesn't move logic.

**Files added:**
- `pyproject.toml` — project metadata, dependency groups (`core`, `voice`,
  `windows`, `dev`), replacing the implicit "just pip install -r
  requirements.txt" flow. Fixes the undeclared `onnx`/`imageio-ffmpeg`/
  `scikit-learn` dependency gap identified in `CURRENT_STATE.md` §3 by
  putting them in a `wakeword-training` extra.
- `src/vortex/__init__.py` — makes this an actual package, not a namespace
  package that happens to work.
- `src/vortex/config.py` — a typed config object (dataclass or Pydantic
  Settings) that reads the same environment variables `main.py` currently
  reads at import time, but as an explicit, constructable, testable object.
  **`main.py` is not changed to use it yet in this step** — that migration
  happens in Step 3. This step only introduces the object and proves it
  works (via a unit test) alongside the untouched original code.
- `legacy/main_working_baseline.py` — verbatim copy of current
  `src/vortex/main.py`, per your rollback requirement.
- `tests/unit/test_config.py` — first real pytest test in the repo.

**Files NOT touched:** `src/vortex/main.py` itself. It keeps running exactly
as it does today.

**Exit criteria:** `pip install -e .` succeeds in a clean venv;
`python -m src.vortex.main` still runs identically to today; `pytest
tests/unit/test_config.py` passes; report back before Step 2.

---

## Step 2 — Extract configuration + logging (into the app, not just alongside it)

**Goal:** `main.py` actually uses `config.py` instead of its own
module-level `os.getenv` calls. Replace `logging.basicConfig(...)` with a
small `observability/logging.py` setup (still a flat file for now — no
structured logging yet, that's a later phase — just moved out of `main.py`
and given a place to grow).

**Mapping:**
| Current (`main.py` lines, approx.) | Moves to |
|---|---|
| `VOICE`, `USER_NAME`, `WAKE_WORD`, `WAKE_THRESHOLD`, `BARGE_IN_THRESHOLD`, `WAKE_COOLDOWN`, `AGC_*`, `SESSION_TIMEOUT`, `MODEL`, `SYSTEM_PROMPT`, `ROOT`, `LOG_DIR` (lines 30-58) | `src/vortex/config.py` (typed `VortexConfig`, with `ROOT` replaced by `Path.home() / ".vortex"` or an explicit `VORTEX_HOME` env var, not a hardcoded drive letter) |
| `logging.basicConfig(...)` (line 60) | `src/vortex/observability/logging.py` |

**Exit criteria:** identical behavior verified by running the app and
checking the log output looks the same; existing `pytest` suite (growing
from Step 1) still green.

---

## Step 3 — Extract the voice subsystem (done, 2026-08-16)

Landed as planned, with two judgment calls documented in-file rather than
here: `_poll_stream` (LLM token-stream polling) stayed in `main.py` since
it's LLM-domain logic, not TTS, even though it shares the same cancellation
pattern as `voice/tts.py` - it'll move again as one piece when Step 4
extracts the LLM provider. The wake `InputStream`'s lifecycle (open/close/
recover) stayed in `voice/wake.py` rather than `voice/audio.py`, since
`wake_model.reset()` must happen at exactly the moment the stream rebuilds -
splitting them risked a coordination bug for no benefit. No
`SpeechToText`/`TextToSpeech` interface abstraction was added (the plan's
"New seam introduced" note) - right after a long, hard-won live-debugging
session fixing real cancellation bugs, adding an abstraction layer on top
was judged to add a place a fix could get lost in translation for no
near-term benefit; each class *is* the concrete implementation, ready to be
wrapped later. Verified: 14 new/ported tests (`tests/unit/test_barge_in.py`,
mocked, no hardware) plus a full live acoustic test after merging into
`main.py` - wake, capture, response, barge-in, and the distinct barge-in
acknowledgment all still worked, with the same sub-100ms interrupt timing
measured before the extraction.

## Step 3 (original plan, preserved below for reference) — Extract the voice subsystem

**Goal:** pull everything audio-related out of the `Vortex` god-object into
`src/vortex/voice/`, behind small interfaces, without changing the
algorithms themselves (the AGC math, the chunking regex, the barge-in
signal flow all stay exactly as validated).

**Mapping:**
| Current | Moves to |
|---|---|
| `_agc`, noise-floor tracking | `voice/audio.py` (an `AudioProcessor` or similar, holding `noise_floor` state) |
| `_on_audio`, wake model loading/predict/reset, `WAKE_THRESHOLD`/`BARGE_IN_THRESHOLD`/`WAKE_COOLDOWN` logic | `voice/wake.py` |
| `_chunk_stream`, `_synth`, `_play`, `_speak_chunks`, `speak`, `speak_stream`, `_unlink` | `voice/tts.py` |
| `_own_mic`, `capture_command` | `voice/stt.py` |
| `speaking`/`stop_speaking` Events and the cancellation checks scattered across TTS/LLM-streaming | `voice/barge_in.py` (a small shared cancellation-token object both TTS and the LLM provider check) |
| `_active_session`, `_worker`'s event loop, `SESSION_TIMEOUT` | `voice/session.py` |

**New seam introduced (not new behavior):** `voice/stt.py` and `voice/tts.py`
expose a small `SpeechToText`/`TextToSpeech` interface (even if there's only
one concrete implementation — Google Web Speech / edge-tts — today). This is
what makes an offline engine (faster-whisper/Piper) a future *addition*,
not a rewrite.

**Exit criteria:** full manual voice-loop test (wake → command → response,
barge-in mid-response, multi-turn session, confirmation flow) still behaves
identically; `tools/test_barge_in.py`'s scenarios ported into
`tests/unit/test_barge_in.py` and passing under pytest with mocked
audio/synthesis (no real mic/speaker needed in CI).

---

## Step 4 — Extract the LLM provider (done, 2026-08-18)

Landed as planned, with four judgment calls documented in-file rather than
here: `llm/prompts.py` was **not** created - `SYSTEM_PROMPT` already lives in
`config.py` as of Step 2, so a second home for the same string would just be
a redundant indirection with no seam it doesn't already have. The two
`_warm_up_models` pings (`ollama.chat(...,'hi')`, `ollama.embeddings(...)`)
were left calling `ollama` directly - a startup health check is a different
concern from the streaming reasoning path this step is about, and folding it
into `LLMProvider` would have widened the interface for no near-term
benefit. `_stream_llm_answer` (the document/RAG-answer streaming path) was
migrated alongside `ask_llm_stream` even though the original plan below only
named the latter - it runs the identical `ollama.chat` + polling pattern, and
leaving it on raw `ollama.chat` while `ask_llm_stream` moved would have been
an inconsistent half-migration. `OllamaProvider.chat_stream` is a plain
method that calls `ollama.chat(...)` eagerly and *returns* a generator,
rather than being a generator function itself - so a connection failure
raises immediately to the caller's first `try/except` exactly as it did
inline in `main.py`, instead of being deferred to the first `next()` on the
returned iterator (which would have misrouted the error into the *second*
`except` block and logged a different message for the same user-visible
outcome). Verified: full `pytest` suite (167 tests) green, zero regressions;
a live sanity check - real `Vortex()`, real `ask_llm_stream` call against
real Ollama, real streamed reply, confirmed `memory.add_turn` persisted both
turns - not just the mocked test suite.

## Step 4 (original plan, preserved below for reference) — Extract the LLM provider

**Goal:** isolate Ollama specifics behind a `Provider` interface so a cloud
adapter can be added later without touching call sites.

**Mapping:**
| Current | Moves to |
|---|---|
| `ask_llm_stream`, `MODEL`, `SYSTEM_PROMPT`, `self.history` | `llm/provider.py` (abstract `LLMProvider.chat_stream(...)`) + `llm/ollama_provider.py` (concrete) + `llm/prompts.py` (the system prompt, pulled out as data) |

**Exit criteria:** LLM fallback path (open-ended questions) still works
identically; history still caps at last 10 turns; offline-Ollama fallback
message still fires correctly when Ollama is down.

---

## Step 5 — Extract OS automation behind a platform adapter (done, 2026-08-18)

Landed as planned, plus both known gaps pulled forward rather than left for
later (per your go-ahead when this step started): `protected_processes` grew
from 12 to 19 entries (added `svchost.exe`, `wininit.exe`, `smss.exe`,
`spoolsv.exe`, `registry`, `fontdrvhost.exe`, `msmpeng.exe` - the
system-critical processes `CURRENT_STATE.md` §6 named as missing), still as
a denylist rather than the inverted allowlist-of-safe-to-close
`CURRENT_STATE.md` offered as an alternative - inverting it would make
`close_all_apps` refuse anything it doesn't already recognize by name, a
much bigger behavior change than this step's scope. `lock_system` got a
`PlatformAdapter.lock()` method too, even though the original mapping below
only named shutdown/restart - it's the same category of platform-specific
power-state call, and leaving it inline while its two siblings moved would
have been inconsistent. `handle_confirmation`'s `'yes' in cmd` substring
bug (`CURRENT_STATE.md` §6, originally slated for Step 6's
`core/policy_engine.py`) was fixed here instead via a small
`_is_affirmative()` helper - whole-word matching, any negative word wins
over an affirmative one - rather than waiting for Step 6's full
intent-routing machinery just to close a live safety gap; it'll move again,
unchanged, into `core/policy_engine.py` when Step 6 actually happens.
`main.py`'s `psutil` and `subprocess` imports came out entirely - every call
site that used them moved into `tools/system/*.py` /
`platform/windows/*.py`. Verified: full `pytest` suite (167 tests) green,
zero regressions; `_is_affirmative()` checked against the exact flagged bug
phrase plus 9 others; a real `Vortex()` opening and closing a real Notepad
process end-to-end through the new wiring. Shutdown/restart/close-all/lock
were **not** live-tested (disruptive to a running session) - verified by
code-path reading instead, since each is a verbatim port with no behavior
change beyond the two gaps above.

## Step 5 (original plan, preserved below for reference) — Extract OS automation behind a platform adapter

**Goal:** this is the step that actually addresses the "platform-independent
architecture" requirement — separate *what* VORTEX wants to do
(open an app, close a process, shut down, protect critical processes) from
*how* Windows specifically does it.

**Mapping:**
| Current | Moves to |
|---|---|
| `open_target`, `native_apps`, `web_apps` | `tools/system/apps.py` (capability logic) + `platform/windows/apps.py` (the `.exe` name table, moved as-is, not redesigned) |
| `close_named_app`, `close_all_apps`, `protected` | `tools/system/process.py` (capability logic, `psutil` calls) + `platform/windows/protected_processes.py` (the allowlist, expanded per the security-review gap in `CURRENT_STATE.md` §6, as its own reviewable file instead of an inline set literal) |
| `system_shutdown`, `system_restart` | `platform/base.py` (`PlatformAdapter.shutdown()`/`.restart()` abstract methods) + `platform/windows/power.py` (the concrete `shutdown /s`/`/r` commands) |
| `ROOT = r'E:\VORTEX'` | Fully removed. Replaced by `Path.home() / ".vortex"` (or `platformdirs`-style app-data directory) via `config.py`, decided in Step 1/2 already — this step just confirms nothing still reads the old hardcoded path. |

**Explicitly NOT created in this step:** `platform/linux/`,
`platform/macos/`, `platform/mobile/` — per your own rule 18 ("do not create
empty directories purely for appearance"), these don't exist until Linux/
macOS/mobile support is actually being built. `platform/base.py`'s abstract
interface is what makes adding them later straightforward — the extension
point exists; the unused implementations don't.

**Exit criteria:** every OS-automation voice command (open Chrome, close
Notepad, close all, shutdown/restart with confirmation) still works
identically on Windows; a unit test can now instantiate the process/app
logic with a **fake** `PlatformAdapter` and verify allowlist/confirmation
behavior without touching real processes.

---

## Step 6 — Introduce the capability registry + intent router (done, 2026-08-18)

Landed as planned. `core/intent_router.py`'s `route(cmd)` is a genuinely
pure function - 26 frozen Intent dataclasses (`name`/`destructive`/
`description` as `ClassVar`s, so they don't affect equality), one per
distinct capability the old 24-entry registry recognized plus `Unhandled`
for the LLM-fallback case; every regex/literal pattern and their checking
order is an unchanged, verbatim port. `core/capability_registry.py`'s
`CapabilityRegistry(host).dispatch(intent)` is the deliberately-impure
other half - every handler is a direct port of the old `h_*` closures,
taking `host` (the `Vortex` instance) explicitly instead of closing over
`self`; it asserts full dispatch coverage at construction time so an
Intent type without a wired handler fails immediately, not silently at
first use. `core/policy_engine.py` got `is_affirmative()` moved into it
unchanged, completing the move Step 5's done-note promised - a fully
general "does this Intent require confirmation" policy engine (Phase 15 of
the master roadmap) was **not** built, since `CURRENT_STATE.md` §6 already
judged the existing per-branch `awaiting_confirmation` pattern fine at this
scale and nothing in this step's actual scope changes that judgment.
`main.py`'s `_build_registry` (297 lines) is gone entirely; `re` and the
last remaining `psutil`/`subprocess` references went with it. Three
`test_registry.py` tests that inspected the old list-of-dicts `_registry`
directly were replaced with equivalents against the new structures - every
other test in that file, and every test in `test_file_confirmation.py`,
needed zero changes, since `execute()`'s external behavior is
byte-identical. Verified: full `pytest` suite (214 tests, 47 new) green;
live - a real `Vortex()` opening/closing a real Notepad process through
`execute()` end-to-end (not just the direct method calls Step 5 tested), a
real time query, and the exact flagged confirmation-bug scenario ("close
all" → "no, not yes I don't want that" → correctly cancelled).

## Step 6 (original plan, preserved below for reference) — Introduce the capability registry + intent router

**Goal:** split `execute()`'s fused "classify + run" regex chain into two
things: something that maps recognized text to a named capability (pure,
testable, no side effects), and something that actually invokes it.

**Mapping:**
| Current | Moves to |
|---|---|
| The regex chain in `execute()` | `core/intent_router.py` (pure function: text → `Intent` value, e.g. `OpenApp(target=...)`, `CloseApp(...)`, `Shutdown`, `Unhandled(text)`) |
| The actual dispatch (calling `open_target`, `system_shutdown`, etc.) | `core/capability_registry.py` maps each `Intent` type to the capability that handles it (which now lives in `tools/system/*` from Step 5) |
| `awaiting_confirmation` string-flag pattern | `core/policy_engine.py` — a small, explicit "this intent requires confirmation" check, replacing the ad hoc per-branch flag, and fixing the `"yes" in cmd` substring bug flagged in `CURRENT_STATE.md` §6 with a real yes/no intent classification |

**Exit criteria:** identical voice-command behavior; a new unit test
(`test_intent_routing.py`) verifies "open chrome" → `OpenApp("chrome")`
without ever calling `subprocess.Popen`; `test_confirmation.py` verifies the
policy engine requires and correctly parses yes/no without the substring bug.

---

## Step 7 — Orchestrator + state manager

**Goal:** replace the implicit state (which thread holds which flag) with an
explicit `state_manager.py` (an enum: `STANDBY`, `ACTIVE_SESSION`,
`SPEAKING`) and an `orchestrator.py` that's what `_worker`/`start` shrink
down to — the thing that wires voice events → intent router → capability
registry → policy engine → response, using the pieces built in Steps 3-6.

**Exit criteria:** `main.py` (or rather, `app.py` at this point) is now
mostly composition — "build these objects, wire them together, run" — with
the god-object's actual logic living in the modules above. Full manual
regression pass against the "preserve these" list.

---

## Step 8 — `main.py` becomes the thin bootstrap

**Goal:** finally reduce `main.py` to what you specified:

```python
from vortex.app import VortexApplication

def main():
    app = VortexApplication()
    app.start()

if __name__ == "__main__":
    main()
```

Only happens once Steps 1-7 have made this trivially true — not before.

---

## Step 9 — Test suite hardening

By this point unit tests exist per-module (from each step above). This step
adds:
- `tests/integration/` — a few tests that wire multiple real modules
  together (e.g., intent router → capability registry → fake platform
  adapter) without mocking everything.
- CI workflow (`.github/workflows/ci.yml`) running lint + type-check +
  `pytest tests/unit tests/integration` on every push — explicitly
  **excluding** anything requiring a microphone, Ollama, or a Windows GUI
  (those get a separate, manually-triggered workflow, not blocking every PR).
- `pytest` markers (`@pytest.mark.hardware`) on anything that does need real
  audio/Ollama, so CI can skip them by default.

## Step 10 — Feature-parity verification against the original

A checklist run-through of every item in your "preserve current working
features" list, confirmed working end-to-end on the refactored code, before
this is considered done. Only after this passes does
`legacy/main_working_baseline.py` stop being load-bearing (it can stay in
the repo as a historical reference either way — no reason to delete it).

---

## What's deliberately deferred (not part of this refactor)

Per rule 18 — architecture quality over technology count — these get
*interfaces/extension points* where the steps above naturally create them,
but are **not implemented** in this refactor:
- FastAPI service layer / WebSocket API (Phase 9) — `api/` directory isn't
  created until this is actually being built; `core/orchestrator.py`'s
  design (Step 7) is what will make adding an API layer additive later
  rather than requiring a rewrite.
- Mobile client, PWA, Android/Termux/iOS support (your §7) — same
  reasoning; no mobile-facing code is written until there's an API for it
  to talk to.
- RAG/memory persistence (PostgreSQL/Qdrant), Redis, Kafka, Temporal,
  Docker/Kubernetes — none of these are needed by anything in Steps 1-10.
  They arrive when the phase that actually needs them starts.

## Sequencing note

Each step above is a separate PR-sized unit of work. The plan is to do them
one at a time, report file-by-file what changed and why after each, and get
your explicit go-ahead before starting the next one — exactly as you asked.
Nothing past Step 0/1-prep has been executed yet.
