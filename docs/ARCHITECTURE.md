# VORTEX — Target Architecture

This described where the codebase was heading, per `docs/REFACTOR_PLAN.md`.
**As of 2026-08-18, that refactor is done (all 11 steps, 0-10)** — nearly
everything below marked "CREATE NOW" actually exists now, not as a target.
The module layout in §2 is annotated to say which; §1's principle and §3's
design patterns describe the shipped architecture accurately as written.
For a narrative status report (not a design doc), see `IMPLEMENTED.md`;
for the step-by-step history of how each piece landed and the judgment
calls made along the way, see `docs/REFACTOR_PLAN.md`.

## 1. Core principle

VORTEX is one assistant. Every capability — voice, OS control, browser
automation, documents, career intelligence, coding, whatever comes later —
is an internal capability behind one interface, never a separate product a
user has to think of separately.

```
Voice/Text
    |
VORTEX Core (orchestrator)
    |
Intent / Context / Planning
    |
Policy & Permission Layer
    |
Agent / Capability Router
    |
Tools / Services / Workflows
    |
Execution
    |
Verification
    |
Response
```

## 2. Module layout — built vs. still deferred

Everything not marked **BUILT** is a documented extension point — the
interface that makes adding it later additive, not a rewrite — created
only when the phase that needs it actually starts (per rule: architecture
quality over technology count; no empty directories for appearance).

```
src/vortex/
├── __init__.py
├── app.py                  # BUILT (Step 8) - the Vortex class: builds & wires everything
├── main.py                 # BUILT (Step 8) - thin bootstrap only, 15 lines
│
├── core/                   # BUILT (Steps 6-7)
│   ├── orchestrator.py     #   BUILT - process lifecycle (tray icon, worker thread, teardown);
│   │                       #   the voice-events->router->registry->response wiring this was
│   │                       #   originally meant to own turned out to already live correctly
│   │                       #   split across voice/session.py (Step 3) and app.py's execute()
│   │                       #   (Step 6) - see REFACTOR_PLAN.md Step 7's done-note
│   ├── intent_router.py    #   BUILT - text -> Intent (pure, no side effects), 26 Intent types
│   ├── capability_registry.py  # BUILT - Intent -> capability dispatch
│   ├── state_manager.py    #   BUILT - explicit VortexState enum (STANDBY/ACTIVE_SESSION/
│   │                       #   SPEAKING/EXECUTING), a read-only view over voice/'s existing
│   │                       #   Events, not a new source of truth (see Step 7's done-note for why)
│   ├── policy_engine.py    #   BUILT - is_affirmative() yes/no classification. A fully
│   │                       #   general confirmation-policy engine was NOT built - deliberately
│   │                       #   out of scope, see §3 below
│   ├── personality.py      #   BUILT (Standby/Activation/Personality foundation, 2026-09-01) -
│   │                       #   PersonalityMode enum + build_system_prompt(), the one live
│   │                       #   integration point into app.py's ask_llm_stream
│   ├── owner_context.py    #   BUILT (2026-09-01) - thin single-owner identity object;
│   │                       #   session_state/personality_mode delegate live to Vortex, not copied
│   ├── social_context.py   #   BUILT (2026-09-01) - SocialLabel enum + a deliberately simple,
│   │                       #   rule-based classify() - see its module docstring for why this is
│   │                       #   honestly scoped as a foundation, not real social understanding
│   └── events.py           #   NOT BUILT - events between pieces are plain strings/queue
│                            #   items; a formal Event-type module was never needed in practice
│
├── voice/                  # BUILT (Step 3)
│   ├── audio.py            #   AGC, noise-floor tracking, raw stream handling
│   ├── wake.py             #   wake-model loading/inference/threshold logic
│   ├── stt.py              #   SpeechToText, Google Web Speech + faster-whisper offline fallback
│   ├── tts.py              #   TextToSpeech, edge-tts + piper-tts offline fallback, chunking, playback
│   ├── barge_in.py         #   shared cancellation-token object
│   └── session.py          #   active-session/inactivity-timeout loop, the worker event loop
│   └── vad.py              # DEFERRED - no VAD model in use yet; wake score is the de facto gate today
│
├── llm/                    # BUILT (Step 4)
│   ├── provider.py         #   abstract LLMProvider.chat_stream(...) + chat_with_tools(...)
│   ├── ollama_provider.py  #   concrete Ollama implementation
│   ├── tools.py            #   BUILT (2026-08-19/20) - tool schemas + tool_call_to_intent(),
│   │                       #   real infrastructure shipped OFF by default (see IMPLEMENTED.md
│   │                       #   Phase 4 for the live-tested reason: unreliable on every model
│   │                       #   available locally)
│   └── prompts.py          #   NOT BUILT - SYSTEM_PROMPT already lived in config.py since Step
│                            #   2, so a second home for the same string was skipped as redundant
│
├── tools/                  # BUILT (Step 5), capability logic only
│   └── system/
│       ├── apps.py         #   open/close-app logic (OS-agnostic)
│       └── process.py      #   bulk-close / allowlist logic (OS-agnostic)
│   ├── browser/            # DEFERRED - browser.py exists as a flat sibling module (Phase 3,
│   │                       #   predates this refactor); folding it under tools/ not done
│   ├── filesystem/         # DEFERRED - files.py exists as a flat sibling module, same reason
│   └── developer/          # DEFERRED - Phase 14, no code yet
│
├── platform/               # BUILT (Step 5), Windows only for now
│   ├── base.py             #   PlatformAdapter abstract interface (shutdown/restart/lock)
│   └── windows/
│       ├── apps.py         #   the .exe name table
│       ├── protected_processes.py  # the process-kill denylist, expanded 12->19 entries
│       └── power.py        #   the concrete WindowsPlatformAdapter
│   ├── linux/               # DEFERRED - not created until Linux is actually targeted
│   ├── macos/                # DEFERRED - same
│   └── mobile/                # DEFERRED - same; mobile is a client, not a platform adapter (see §5)
│
├── config.py                # BUILT (Step 1) - typed VortexConfig
│
├── memory/                  # STILL FLAT, NOT FOLDED - src/vortex/memory.py (SQLite
│                             #   conversation history) and src/vortex/rag.py
│                             #   (Postgres+Qdrant document retrieval) both exist
│                             #   today as flat sibling modules. Folding them into
│                             #   memory/repository.py and memory/retrieval/ was never
│                             #   part of REFACTOR_PLAN.md's actual 11 steps (0-10) -
│                             #   it stayed a documented future option, not something
│                             #   any landed step's exit criteria required
├── agents/                  # DEFERRED - Phase 13, single orchestrator is sufficient at this scale
├── workflows/               # DEFERRED - Phase 10, nothing durable-workflow-shaped exists yet
├── api/                     # DEFERRED - Phase 9, no HTTP/WebSocket surface yet
├── security/                 # DEFERRED - policy_engine.py (core/) covers today's actual need
└── observability/           # NOT BUILT - logging.basicConfig(...) is still one inline line
    └── logging.py           #   in app.py, not its own module. Step 2's own exit criteria
                              #   called for this move and it never happened - the smallest
                              #   genuine leftover gap in the whole refactor, caught while
                              #   writing this note, not fixed here (see REFACTOR_PLAN.md
                              #   Step 2's note)
```

## 3. Design patterns actually in play

**Capability Registry + Intent Router.** Separating "what did the user mean"
(a pure function, easy to unit test) from "what happens when that intent is
executed" (a dispatch table) is the single highest-value structural change
in this refactor — it's what makes `core/intent_router.py` testable without
mocking `subprocess` or `psutil`, and what makes adding a new voice command
later a matter of adding one `Intent` type and one registry entry, not
another `elif` in a 40-line method.

**Platform Adapter.** `PlatformAdapter` (abstract) with a `WindowsAdapter`
(concrete, today) is the seam that turns "everything assumes Windows" into
"the core assumes nothing; Windows is one adapter among possible others."
This is the direct answer to the "platform-independent architecture"
requirement — and it's honestly scoped: only the adapter interface plus the
one concrete implementation that's actually used ship now.

**Provider abstraction (STT/TTS/LLM).** Same idea applied to voice and
reasoning: `SpeechToText`, `TextToSpeech`, and `LLMProvider` are interfaces
with exactly one concrete adapter each today (Google Web Speech, edge-tts,
Ollama). Swapping to an offline STT/TTS engine later, or adding a cloud LLM
adapter, becomes "write a new adapter class," not "edit the core loop."

**Policy Engine (minimal, honest scope).** Centralizes the
"does this intent need human confirmation, and did the user actually say
yes" logic that's currently duplicated/ad hoc in `execute()`/
`handle_confirmation()`. Deliberately *not* built as a generalized
rules-engine with pluggable policies yet — there's exactly one kind of
policy today (destructive-action confirmation), so the engine models that
directly rather than over-abstracting for hypothetical future policy types.

**Explicit state, not implicit thread-and-flag state.** `state_manager.py`
gives STANDBY/ACTIVE_SESSION/SPEAKING an actual name and a place transitions
are validated, replacing "state is whatever combination of Events happens to
be set right now."

## 4. What's explicitly NOT being built yet, and why

Per your rule 18 (don't add infrastructure until the phase that needs it):

| Not built now | Needed starting at | Why it can wait |
|---|---|---|
| FastAPI/WebSocket API layer | Phase 9 | Nothing outside this process needs to talk to VORTEX yet. `core/orchestrator.py`'s design is what makes adding this additive later. |
| Redis / Kafka | Phase 9-10 | No multi-process/multi-service deployment exists yet — there's nothing to cache or stream between. |
| Temporal | Phase 10 | No multi-step workflow exists yet that needs to survive a crash mid-execution. |
| Multi-agent orchestration (LangGraph etc.) | Phase 13 | One orchestrator handling one conversation at a time is sufficient; a supervisor/specialist split solves a coordination problem that doesn't exist yet. |
| Docker/Kubernetes | Phase 16 | Single-machine desktop app; no deployment-scale problem to solve. |

## 5. Multi-platform / mobile strategy (target, not yet built)

```
                 VORTEX CORE
                      |
              FastAPI / WebSocket        <- Phase 9, not built yet
                      |
        +-------------+-------------+
        |                           |
 Windows Desktop Client        Mobile Client
        |                           |
 Windows automation         voice/text/dashboard
 local wake word            remote commands
 screen control              notifications
 local tools                 task status
```

**Windows desktop** keeps everything that genuinely requires local OS
access — wake word, screen/process control, local tool execution — exactly
as it works today, just behind the `PlatformAdapter` seam.

**Mobile is explicitly a client, not a second copy of the core.** A phone
was never going to run Windows process automation, and the architecture
doesn't pretend otherwise. The plan is: once the FastAPI layer exists
(Phase 9), a mobile client talks to the same VORTEX core over HTTP/
WebSocket — voice/text in, remote command execution requests out,
task-status/notifications back. No automation logic gets duplicated on the
phone.

**Termux (Android)** gets treated as "a Linux-like Python environment" for
whatever parts of VORTEX are genuinely platform-independent (the
`core/`, `llm/`, `config.py` layers, once they don't import anything
Windows-specific) — not as a place to run desktop automation. This only
becomes meaningful once `platform/linux/` exists, which per §4 is deferred
until Linux is actually targeted.

**iOS** is client-only, full stop, per your instruction — no attempt at a
native automation layer; a future PWA or native app talking to the FastAPI
layer is the only planned iOS story.

**Today**, none of this exists — the honest current answer is "Windows
desktop only" (see `CURRENT_STATE.md` §7). This section still documents a
genuine future target, not something built - the completed refactor
(Steps 0-10) drew the module boundaries (`core/orchestrator.py`,
`platform/`) in the right place to make this additive later, which is as
far as it goes today.

## 6. Testing architecture (built, Step 9)

```
tests/
├── unit/            # pure logic - intent routing, policy engine, chunking,
│                     # AGC math, config parsing. No mic/speaker/network/Ollama.
├── integration/      # multiple real modules wired together, fake platform
│                     # adapter, fake LLM provider - still no real hardware.
├── e2e/              # DEFERRED until there's something worth running e2e
│                     # against beyond "the whole voice loop," which today
│                     # is still verified manually per REFACTOR_PLAN.md Step 10.
└── fixtures/         # EXISTS, still empty - each test file defines its own
                      # fakes inline (FakePlatformAdapter, FakeBrowser) rather
                      # than sharing them from here; nothing has needed a
                      # second copy of the same fake yet to justify the move.
```

CI (`.github/workflows/ci.yml`, Step 9) runs `unit` + `integration` on every
push. Anything marked `@pytest.mark.hardware` (real mic, real Ollama, or a
real Windows GUI process) is excluded from that gate and only runs via a
separate, manually-triggered workflow
(`.github/workflows/hardware.yml`) — consistent with "CI must not require
microphone, GUI, Ollama, or Windows desktop."
