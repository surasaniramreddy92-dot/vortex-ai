# VORTEX — Current State Audit

Ground-truth inventory of the repository as it exists today, before any
refactor work begins. Everything here is verified against the actual files
on disk, not the aspirational roadmap. See `docs/REFACTOR_PLAN.md` for what
changes and in what order, and `docs/ARCHITECTURE.md` for the target design.

Audited at: commit `9ed5444` + uncommitted working-tree changes (AGC
noise-floor fix, wake threshold 0.8/0.9, diagnostic logging), 2026-08-02.

---

## 1. Repository inventory

```
VORTEX/
├── .env                              # real local config (gitignored)
├── .env.example                      # documents shape, no real values
├── .gitignore
├── IMPLEMENTED.md                    # phase status matrix (0/1/2/4/6/15 only)
├── README.md
├── requirements.txt                  # pinned runtime deps (see §3 - incomplete)
├── docs/
│   ├── LEARNING_GUIDE.md             # phase concept explanations
│   └── INTERVIEW_PREP.md             # personal notes, untracked, NOT pushed
├── logs/
│   └── vortex.log                    # plaintext runtime log (gitignored)
├── src/
│   └── vortex/
│       └── main.py                   # the entire application, ~513 lines
├── tools/
│   ├── test_barge_in.py              # standalone script, not pytest-discoverable
│   └── wakeword/
│       ├── build_hey_vortex.py       # training pipeline for the custom wake model
│       ├── validate_hey_vortex.py    # held-out-voice scoring/calibration
│       ├── cache/                    # gitignored, regeneratable synthetic audio
│       └── models/
│           └── hey_vortex.onnx       # the trained model, committed (394KB)
```

No `pyproject.toml`, no `tests/` directory, no `.github/workflows/`, no
`CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE`, or `VERSION`.
No `src/vortex/__init__.py` (works today only because Python 3 namespace
packages don't strictly require one — a real gap for a proper package).

---

## 2. Feature audit (Implemented / Partial / Planned)

This restates `IMPLEMENTED.md` but ties each line to the actual function(s)
implementing it, which is what the refactor's module-mapping in
`REFACTOR_PLAN.md` is built from.

| Capability | Status | Where it lives today |
|---|---|---|
| Continuous wake-word listening | Implemented | `Vortex._on_audio`, `Vortex.start` (persistent `sd.InputStream`) |
| Custom "Hey Vortex" ONNX model | Implemented | `tools/wakeword/*`, loaded via `Model(wakeword_models=[WAKE_WORD], ...)` |
| Automatic gain control w/ noise-floor gating | Implemented | `Vortex._agc` |
| Barge-in (mid-speech interruption) | Implemented | `_on_audio` (stricter threshold while `speaking`), `stop_speaking` Event checked in `_speak_chunks`, `_play`, `ask_llm_stream` |
| Multi-turn active session + inactivity timeout | Implemented | `Vortex._active_session` |
| Confirmation flow for destructive actions | Partial | `Vortex.handle_confirmation` — substring check on `"yes" in cmd`, no structured policy engine |
| Speech-to-text | Partial | `Vortex.capture_command` — cloud-only (Google Web Speech via `speech_recognition`) |
| Streaming TTS with cancellable chunked playback | Implemented | `Vortex._chunk_stream`, `_synth`, `_speak_chunks`, `_play` |
| LLM integration (Ollama, streamed) | Partial | `Vortex.ask_llm_stream` — no structured tool-calling, no eval suite |
| Deterministic intent routing | Partial | `Vortex.execute` — a single `if`/`elif`-style regex chain doing classification *and* dispatch in one method |
| App launching (native + web fallback) | Implemented | `Vortex.open_target` |
| App termination (single + bulk) | Implemented | `Vortex.close_named_app`, `close_all_apps` |
| Process-kill allowlist ("protected" set) | Partial | hardcoded `self.protected` set, 11 entries, not exhaustive (see §5) |
| Shutdown/restart w/ confirmation | Implemented (Windows only) | `Vortex.system_shutdown`, `system_restart` — `shutdown /s` / `/r` via `subprocess.Popen(..., shell=True)` |
| Tray icon + manual overrides | Implemented | `Vortex.tray_icon`, `start`, menu callbacks |
| Logging | Partial | `logging.basicConfig` to a flat file, no structure/rotation/task IDs |
| Conversation memory | Partial | `self.history`, in-RAM Python list, capped at 10 turns, lost on restart |
| Everything else in the master blueprint (browser automation, RAG, documents, vision, FastAPI/API layer, durable workflows, career/job agents, multi-agent orchestration, developer agent, full observability) | **Not started** | no code |

---

## 3. Dependency audit

**Declared in `requirements.txt`:** numpy, psutil, pygame, pystray,
sounddevice, SpeechRecognition, PyAudio, Pillow, python-dotenv, edge-tts,
ollama, openwakeword, onnxruntime.

**Used but NOT declared anywhere** (installed ad hoc into `venv` during this
session, only discoverable by reading `tools/wakeword/build_hey_vortex.py`'s
imports): `onnx`, `imageio-ffmpeg`, `scikit-learn`. This is a real bug for
reproducibility — cloning this repo fresh and running
`pip install -r requirements.txt` would leave the wake-word training
pipeline broken with `ModuleNotFoundError`. **Fix in Step 2** of the refactor
(pyproject.toml with a `wakeword-training` or `dev` optional-dependency
group).

**External runtime dependencies not pip-installable:** Ollama (must be
separately installed and running locally, with `llama3.2:1b` pulled) and a
system/portable ffmpeg (currently satisfied via the `imageio-ffmpeg`
package, only used by the training tools, not the runtime app).

---

## 4. Platform-specific code (blocks non-Windows use today)

| Location | What it does | Why it's Windows-only |
|---|---|---|
| `ROOT = r'E:\VORTEX'` (line 32) | Hardcoded absolute path used to build `LOG_DIR` and the default wake-model path | A literal Windows drive-letter path. Breaks on any machine where the repo isn't cloned to exactly `E:\VORTEX`, and breaks outright on Linux/macOS/mobile. |
| `system_shutdown`, `system_restart` | `subprocess.Popen('shutdown /s /t 5', shell=True)` / `'shutdown /r /t 5'` | Windows `shutdown.exe` command-line syntax. Linux/macOS use different commands (`shutdown -h`, `shutdown -r`) entirely. |
| `native_apps` dict | Maps app names to `.exe` filenames (`chrome.exe`, `code.exe`, ...) | Windows executable naming convention; meaningless on Linux/macOS. |
| `close_named_app`, `close_all_apps` | Matches `psutil` process names against `.exe`-suffixed strings | Same issue — process names differ by OS. |
| `protected` set | All entries are Windows system process names (`explorer.exe`, `lsass.exe`, `dwm.exe`, `csrss.exe`, ...) | Meaningless (and unsafe if left as-is) on another OS — there's no equivalent protection list for Linux/macOS system processes. |
| `PyAudio` dependency | Backend for `speech_recognition.Microphone` | Installed here via a prebuilt Windows wheel; needs PortAudio dev headers to build from source on Linux, and different install steps on macOS (`brew install portaudio`). Not a hard Windows-only blocker, but not currently verified elsewhere. |
| `pystray` tray icon | Uses whatever native tray backend is available | Nominally cross-platform, but behavior differs meaningfully by OS (some Linux desktop environments don't support a tray at all) and this has only ever been run/tested on Windows. |

**Net effect:** essentially the entire `Vortex` class assumes Windows. This
is the single biggest reason the current design can't move toward the
"Windows Desktop Client vs. Mobile Client" split the blueprint wants — there
is no seam anywhere between "OS automation" and "everything else."

---

## 5. Architectural problems

**One God object.** `Vortex` is a single class that owns: continuous audio
capture, wake-word inference, STT, TTS synthesis and playback, LLM calls,
conversation history, regex-based intent routing, OS process
management, app launching, the tray icon, and logging — all as methods on
one object, coordinating through shared instance attributes
(`self.speaking`, `self.stop_speaking`, `self.capturing`, `self.events`,
`self.awaiting_confirmation`). Nothing is independently testable or
independently swappable. This is the primary driver for the whole
modularization effort.

**Module-level global configuration, read once at import time.** `VOICE`,
`WAKE_THRESHOLD`, `MODEL`, etc. are all `os.getenv(...)` calls executed the
moment `main.py` is imported. There's no config *object* — you can't
construct two differently-configured instances in the same process (which
matters for testing), and a typo in an env var name silently falls back to
the default rather than failing loudly.

**Intent classification and execution are fused.** `execute()` is 37 lines
of sequential regex checks that both *decide what the user meant* and
*immediately perform the action* in the same method. There's no
`Intent` value you could unit-test independently of actually running
`subprocess.Popen` or `psutil.terminate()`.

**No provider abstraction for STT/TTS/LLM**, despite the blueprint
explicitly calling for one (Phase 1). `speak()` and `capture_command()` are
concrete implementations tied directly to `edge_tts` and
`speech_recognition.recognize_google` — swapping to an offline engine later
means editing these methods in place, not implementing a new adapter class.

**No formal state machine.** The IDLE → ACTIVE → SPEAKING lifecycle exists
only implicitly, as an emergent property of which thread is running and
which `threading.Event`s are set. It works (this has been verified live),
but there's no single object you could point to and ask "what state is
VORTEX in right now" or unit-test transitions against.

**Minimal automated testing.** `tools/test_barge_in.py` is a real, useful
smoke test for the chunking/interruption logic — but it's a standalone
script with `print()`-based pass/fail output, not something `pytest`
discovers or CI can gate on. Nothing else is tested at all (regex routing,
confirmation logic, app-name mapping, close-all allowlist behavior are all
completely uncovered).

---

## 6. Security review

**Secrets hygiene: clean so far.** `.env` has never been committed (verified
against the initial commit); `.env.example` contains no real values. The
repo is public — this must stay true going forward, and needs to be
actively re-checked (not just assumed) before every future push, especially
once any capability that touches real credentials (email, browser sessions,
career/job automation) is added.

**`shell=True` subprocess calls — safe today, dangerous as a precedent.**
`system_shutdown`/`system_restart` pass a fixed literal string to
`subprocess.Popen(..., shell=True)`. No user- or LLM-supplied text reaches
that string today, so there's no injection vector *right now*. But this
pattern must not be copied forward: the moment any future capability builds
a shell command from user input or LLM output without strict
allowlisting/escaping, this becomes a command-injection vulnerability. Flag
for the security review checklist in every future PR that touches
`subprocess`.

**The process-kill allowlist is incomplete, not just minimal.** `protected`
covers 11 process names. It does **not** include several other processes
whose termination can crash or destabilize Windows (e.g. `svchost.exe`,
`wininit.exe`, `smss.exe`, `spoolsv.exe`, `registry`, `fontdrvhost.exe`,
antivirus/EDR processes). Today's `close_all_apps` is safer than "kill
everything," but "safer" isn't the same as "safe" — this needs to become
either a much more conservative denylist-of-known-safe-to-kill (invert the
logic: only close things explicitly known to be user applications) or a
properly maintained, larger protected list, ideally sourced from a
documented reference rather than hand-typed.

**Confirmation logic has a real correctness bug, which is also a safety
bug.** `handle_confirmation` checks `if 'yes' in cmd` — a raw substring
match. STT mishears are common (already observed in production logs, e.g.
"why do i need to show to call you"); a misheard phrase that happens to
contain the substring "yes" anywhere (e.g., a misheard "no, not yes I
don't want that") would incorrectly confirm a shutdown/restart/bulk-close.
This should be an exact-match/intent classification, not a substring check,
**before** any capability with real-world consequences is added on top of
this pattern.

**Logs are plaintext, unbounded, and contain full conversation
transcripts.** `logs/vortex.log` is gitignored (good — never gets to the
public repo) but grows without rotation or retention policy locally, and
will contain anything ever said to or by VORTEX. Not a public-repo risk
today, but worth a retention/rotation policy before this log ever contains
something like email content or personal document text (Phase 7+).

**No policy engine — by design, at this scale, that's fine for now.** The
`awaiting_confirmation` pattern is a legitimate, minimal implementation of
"human approval for consequential actions." It does not yet generalize
(each new destructive capability needs its own hand-written confirmation
branch in `execute()`), which is exactly the gap Phase 15's policy engine is
meant to close — correctly deferred, not currently a live risk, since
there's nothing yet with real destructive external side effects beyond
local process/OS control.

---

## 7. What actually runs where, today

| Platform | Status |
|---|---|
| Windows | The only platform this has ever run on. Everything works here. |
| Linux | Would fail immediately: hardcoded `E:\VORTEX` path, Windows `shutdown` syntax, `.exe` process-name matching, unverified PyAudio/PortAudio build. |
| macOS | Same failures as Linux, plus macOS-specific audio permission prompts never handled. |
| Android/Termux | No server/API layer exists to connect to; nothing to run there today. |
| Mobile web/PWA | Doesn't exist yet — no API, no web client. |
| iOS | Client-only is the only sane model per the blueprint; no client exists yet. |

This is the accurate baseline the "what can run where" table in
`REFACTOR_PLAN.md` and `ARCHITECTURE.md` starts from — today, the honest
answer is "Windows only, everywhere else requires the platform-abstraction
work this refactor sets up."
