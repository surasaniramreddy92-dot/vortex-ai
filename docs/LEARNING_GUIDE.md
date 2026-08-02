# VORTEX Learning Guide — Concepts by Phase

This is a study document, not a status report (for status, see
[IMPLEMENTED.md](../IMPLEMENTED.md)). It walks through every phase of the
master roadmap and explains the *concepts* behind it in enough depth to
actually learn them — what the idea is, why it exists, why it's the right
tool for this problem, and — where VORTEX has actually built it — how our
code does it concretely. Read it top to bottom as a course, or jump to a
phase you're working on.

---

## Phase 0 — Engineering Foundation & Repository Discipline

**The core idea:** before you build features, you build the scaffolding that
keeps a growing codebase honest — reproducible environments, automated
quality gates, and a visible history of decisions. This isn't bureaucracy for
its own sake; it's what lets you (or anyone else) trust that the code on disk
still does what the last commit said it does.

**Dependency isolation** (venv/Poetry/uv) exists because Python has no
built-in per-project package boundary — without it, installing one project's
dependencies can silently break another's. A virtual environment is just a
private copy of the interpreter's `site-packages`, activated per shell
session.

**Typed configuration** (Pydantic Settings) replaces scattered
`os.getenv("X", "default")` calls with a single validated schema: wrong types
or missing required values fail loudly at startup instead of causing a
confusing crash three function calls later.

**Static analysis and pre-commit hooks** (Ruff, Black, mypy) catch a whole
class of bugs — unused imports, type mismatches, inconsistent formatting —
before they ever reach a test run, let alone production. The point isn't
style preference; it's that a human reviewer's attention is a scarce
resource, and a linter is not.

**CI (GitHub Actions)** re-runs your quality gates on a clean machine for
every push, which is the only way to know your code doesn't secretly depend
on something only present on your laptop.

**Semantic versioning** (`MAJOR.MINOR.PATCH`) is a promise to consumers of
your code about what kind of change just happened — a patch release should
never break someone's integration; a major one might.

*Where VORTEX stands:* none of this exists yet. It's literally being started
in this session (git init, first commit). This is the single most valuable
next investment before the codebase grows further, because every phase after
this one gets more expensive to retrofit with tests and structure the later
you leave it.

---

## Phase 1 — Voice I/O Foundation

**The core idea:** treat voice as an input/output subsystem with a clean
seam, not as "the assistant's brain." Speech-to-text (STT) and
text-to-speech (TTS) are commodity, swappable components; the interesting
logic lives elsewhere.

**Audio streams and buffers:** a microphone doesn't hand you "a recording" —
it hands you a continuous stream of small chunks (frames) at a fixed sample
rate (VORTEX uses 16kHz, mono, 1280-sample/80ms frames, the rate openWakeWord
expects). Anything that wants to reason about "what was just said" has to
accumulate frames into a buffer and decide when enough context exists.

**Voice Activity Detection (VAD):** a lightweight classifier (Silero VAD,
WebRTC VAD) that answers one question per frame — "is this speech or
silence?" — so expensive downstream steps (STT, LLM calls) don't run
constantly on dead air. VORTEX doesn't use one explicitly yet; instead the
wake-word model's own score implicitly gates everything downstream of it.

**STT/TTS as providers, not fixtures:** the blueprint's target is offline
STT (faster-whisper/whisper.cpp) and offline TTS (Piper), with cloud options
as adapters, specifically so the assistant keeps working without internet and
without sending your voice to a third party. VORTEX today does the reverse —
cloud STT (Google Web Speech) and cloud TTS (edge-tts) — which is the honest
gap flagged in IMPLEMENTED.md.

**Latency and cancellation:** a voice assistant that can't be interrupted
feels broken the moment it says something wrong or too long. This is why
barge-in (Phase 6) and cancellable synthesis exist — see that phase for how
VORTEX actually implements mid-sentence interruption via chunked TTS
generation and a shared `stop_speaking` signal.

---

## Phase 2 — Desktop & Operating-System Automation

**The core idea:** giving software the power to open, close, and terminate
other programs is powerful and dangerous in equal measure, so the design
problem isn't "how do I call `terminate()`" — it's "how do I make sure the
assistant never terminates something it shouldn't."

**Capability registry:** instead of one giant if/elif chain interpreting
arbitrary text, a mature system maps recognized intents to a fixed set of
named, individually-authorized actions (`open_app`, `close_app`,
`bulk_close`, `shutdown`...). VORTEX's `execute()` method is an early,
regex-based version of this idea — it recognizes a handful of patterns and
dispatches to dedicated methods, which is the right shape even though it
isn't yet a formal registry with per-capability permissions.

**Allowlists over blind termination:** `psutil.process_iter()` can see and
kill *any* process on the system, including the ones Windows itself depends
on. VORTEX's `protected` set (`explorer.exe`, `lsass.exe`, `dwm.exe`, etc.)
exists because "close all applications" is a wildly destructive command if
taken literally — the allowlist is what turns "close everything" into
"close everything that's safe to close."

**Idempotency and command safety:** an action like "close Chrome" should be
safe to say twice — if Chrome's already closed, that's a no-op, not an
error. Destructive or irreversible actions (shutdown, restart, bulk-close)
get a stateful confirmation step instead (`awaiting_confirmation`), which is
a minimal but real instance of the "human approval for consequential
actions" principle that recurs throughout the whole blueprint (see Phase 15).

**Process lifecycle as a background/tray app:** running as a `pystray` tray
icon rather than a foreground console window is what lets VORTEX be "always
available" without stealing focus — this is table stakes for anything that's
supposed to feel like an assistant rather than a script you run and watch.

---

## Phase 3 — Browser Automation & Web Interaction

**The core idea:** move from "open this URL in a browser" to reliably
performing a *sequence* of actions on a page — click this, fill that, wait
for this to load — the same way a human would, but scriptably.

**DOM and CSS selectors:** every element on a rendered page is a node in the
Document Object Model tree; a selector (`#submit-button`, `.form-input`) is
just an address for one or more of those nodes. Reliable automation lives or
dies on choosing selectors that survive a page's next redesign — prefer
semantic attributes (`data-testid`, ARIA roles) over brittle generated class
names or absolute positions.

**Isolated browser contexts:** Playwright's model gives every automated
session its own cookies, storage, and cache, so one automation run can't
accidentally leak state into (or get confused by) another — this matters a
lot once you're running many jobs (Phase 12) concurrently.

**Explicit waits over sleeps:** a page loading is asynchronous — network
requests, JavaScript execution, and animations all take a variable, unknown
amount of time. `time.sleep(2)` is a guess; an explicit wait
("wait until this selector is visible/enabled") is a correctness guarantee.
This single habit is the difference between browser automation that's
"pretty reliable" and automation that silently breaks the moment a site gets
5% slower.

**API-first, browser-as-fallback:** if a site has an official API, use it —
it's faster, more stable, and doesn't fight the site's anti-automation
defenses. Browser automation is for when no API exists or the workflow is
inherently visual (this is also a *policy* boundary: automating around
CAPTCHA/MFA is explicitly out of bounds, see Phase 18 of the blueprint).

---

## Phase 4 — LLM Brain, Tool Calling & Hybrid Intent Routing

**The core idea:** an LLM is a powerful, unreliable-in-detail reasoning
engine. The architecture question is where to draw the line between "let the
model figure it out" and "run a piece of code that always does the same
thing." VORTEX's answer, like the blueprint's, is: deterministic first, LLM
only for what's genuinely open-ended.

**Intent routing:** before any LLM call happens, a cheap, fast, and
predictable check asks "do I already know exactly what this means?" VORTEX's
`execute()` does this with regex (`open (.+)`, `close (.+)`, "what time is
it") — it's crude, but it means those commands are instant, free, and never
hallucinate a wrong action.

**Structured outputs / tool schemas:** rather than asking an LLM to produce
free text and then trying to regex-parse "the thing it probably meant," you
give it a JSON schema (or Pydantic model) describing exactly the shape of a
valid tool call, and the model is constrained (or strongly guided) to
produce that shape. This turns "hope the model's phrasing is parseable" into
"validate the model's structured output against a schema" — a much smaller
failure surface. VORTEX doesn't do this yet; today's LLM path is pure prose
in, prose out.

**Local-first inference (Ollama):** running `llama3.2:1b` locally means no
per-token cost, no data leaving the machine, and no dependency on a remote
API's uptime — at the cost of being a much smaller, less capable model than
frontier cloud options. The architecture keeps a seam for "provider
adapters" specifically so a cloud model can be swapped in for harder tasks
without rewriting the caller.

**Streaming and cancellation:** `ollama.chat(..., stream=True)` yields
tokens as they're generated instead of waiting for the full response. VORTEX
uses this for two reasons at once: speech can start on the first sentence
instead of waiting for the whole answer, and — combined with a
`stop_speaking` check inside the generation loop — a barge-in can stop token
generation itself, not just the resulting audio.

**Evaluation datasets:** once you have more than a couple of intents, you
need a fixed set of "given this input, this is the expected
action/response" cases you can re-run after every prompt or model change, so
you notice regressions instead of discovering them from a user complaint.

---

## Phase 5 — Memory, Knowledge & Production RAG

**The core idea:** an LLM's context window is not memory — it's short-term
working memory that vanishes the moment the process restarts or the
conversation gets long enough to truncate. Durable memory and grounded
knowledge retrieval are separate infrastructure problems with different
right answers.

**Transactional vs. semantic storage:** PostgreSQL is for facts with a known
shape and strong consistency needs — a user profile, a task's status, a
timestamped history you might query by exact field. Qdrant (a vector
database) is for "find me things that mean something similar to this,"
which relational WHERE clauses can't express at all.

**Embeddings and vector similarity:** an embedding model turns text into a
fixed-length vector such that semantically similar text produces nearby
vectors (by cosine similarity or dot product). This is what lets "how do I
cancel my subscription" retrieve a document titled "Ending your plan" even
though they share almost no literal words.

**Chunking:** documents are split into passages small enough to embed
meaningfully and retrieve granularly, but large enough to preserve context —
this is a real design tradeoff, not a mechanical step, and bad chunking
(splitting mid-sentence, losing headers) quietly degrades every downstream
answer.

**Hybrid retrieval and reranking:** pure semantic search misses exact
keyword/ID matches; pure keyword search misses paraphrases. Combining both
(dense + sparse/BM25) and then reranking the merged candidates with a
cross-encoder is how production RAG systems close the gap between "looks
relevant" and "is actually the best answer."

**Grounded generation with provenance:** the whole point of RAG is that the
model answers *from* retrieved text and cites it, rather than answering from
its own possibly-wrong training-time memory — provenance is what makes an
answer checkable instead of just plausible-sounding.

*Where VORTEX stands:* `self.history` is a plain Python list capped at the
last 10 turns, held in RAM, gone on restart. None of the above infrastructure
exists yet.

---

## Phase 6 — Wake Word, Session Mode & Barge-In

**The core idea:** you want an assistant that's always listening for its
name without always running expensive inference — and once activated, you
want it to feel like a conversation, not a sequence of separately-summoned
interactions.

**Keyword spotting (KWS):** a wake-word model is a small, cheap classifier
built specifically to answer one narrow question extremely fast — "was that
phrase just said?" — as opposed to a general STT model, which is orders of
magnitude more expensive and answers a much harder question ("what did they
say, exactly, word for word"). This asymmetry (cheap gate, expensive engine
behind it) is why wake-word architectures exist at all instead of just
running STT continuously.

**How VORTEX's wake model actually works:** openWakeWord's pipeline turns
raw audio into a mel-spectrogram, then a shared embedding model turns a
window of that spectrogram into a 96-dimensional feature vector per 80ms
frame. A wake-word-specific model looks at a sliding window of 16 such
frames (1.28 seconds of embedded audio) and outputs one probability: "does
this window contain the target phrase?" `tools/wakeword/build_hey_vortex.py`
trains that final small classifier from scratch for the phrase "Hey Vortex,"
using synthetic TTS audio across ~20 voices as positive examples and a much
larger, denser set of confusable/generic phrases as negatives — see that
script's own docstring for the two hard-won lessons (label only the
phrase-containing middle of a clip as positive; sample negatives as densely
as real-time streaming will actually evaluate them, or a tiny per-window
false-positive rate compounds into near-certain false triggers over a
long clip).

**Finite-state machines for audio:** IDLE → ACTIVE/LISTENING → THINKING →
SPEAKING, with defined transitions, is what prevents microphone/speaker race
conditions — you don't want the app trying to listen for a wake word through
its own TTS output at full sensitivity (VORTEX handles this with a *stricter*
threshold while speaking, `BARGE_IN_THRESHOLD`, rather than disabling
detection entirely, so barge-in still works).

**Concurrency and cancellation primitives:** VORTEX uses a `threading.Event`
(`stop_speaking`) as a shared cancellation flag checked at every yield point
in TTS synthesis, audio playback, and LLM token streaming — this is the
mechanism, not the state machine, that actually stops speech within
milliseconds of a barge-in being detected. A `Queue` connects the always-on
audio callback (which must never block) to a slower worker thread that owns
every expensive operation (STT, LLM, TTS).

**Session mode:** requiring the wake word before *every single sentence* —
including yes/no answers to VORTEX's own confirmation questions — is exactly
the friction the blueprint calls out. VORTEX's `_active_session()` loop
keeps listening for a configurable inactivity window after any wake event,
so a whole multi-turn exchange (and any confirmation within it) happens
without re-summoning the assistant.

---

## Phase 7 — Document Intelligence

**The core idea:** documents are structured data wearing an unstructured
costume — a PDF has pages, headers, tables, and reading order, even though
it's "just text" to a naive parser. Preserving that structure is what makes
downstream answers trustworthy rather than approximately right.

**Layout-aware extraction:** naive PDF text extraction can scramble
multi-column layouts or lose which cell of a table a number belonged to.
Tools like PyMuPDF/pdfplumber expose position and structure, not just a flat
text blob, specifically so a summarizer or QA system can say *where* a fact
came from.

**Format-specific parsers:** DOCX (`python-docx`), spreadsheets
(`openpyxl`/pandas), and PDFs each have a different internal model — a
spreadsheet's "structure" is rows/columns/formulas; a DOCX's is styles and
paragraphs. There's no universal parser because there's no universal
document; you write (or pick) one adapter per format and normalize *outputs*
into a common representation.

**OCR as a fallback, not a first resort:** running Tesseract over every page
is slow and lossy compared to a PDF's native text layer, so OCR should
trigger only when that native layer is missing (i.e., the "PDF" is actually
a scanned image).

**Provenance into RAG:** feeding parsed documents into the Phase 5 retrieval
pipeline is what turns "read this PDF" into "answer questions grounded in
this PDF, with page citations" — the same chunking/embedding/retrieval
machinery, just fed a new kind of source.

---

## Phase 8 — Vision & Screen Understanding

**The core idea:** an assistant that can act on your desktop needs to
perceive it first — but "perceive" should mean the most precise, cheapest
available signal, not "take a screenshot and ask a vision model to guess,"
every time.

**Accessibility trees and DOM before pixels:** Windows UI Automation and a
browser's DOM both expose *structured* descriptions of what's on screen —
this button's label, that field's current value — which is exact and
machine-readable. A screenshot is comparatively ambiguous (a vision model has
to *infer* that a rectangle of pixels is a clickable button); reach for
vision only when no structured API is available.

**Visual verification:** after taking an action, checking that the expected
UI state actually occurred (a dialog closed, a field now contains the typed
text) is what separates "I clicked and I assume it worked" from software
that actually knows whether it succeeded — this closes the loop that Phase 2
already needed for OS-level actions and Phase 3 needed for browser actions.

**Scoped, deliberate capture:** grabbing the whole screen and holding onto
the image is a privacy liability by default. Capturing only the relevant
window/region, and not persisting the image longer than the action needs it,
is a design constraint as much as a technical one.

---

## Phase 9 — FastAPI Service Layer & Event-Driven Core

**The core idea:** a single-process desktop app has a ceiling — one user, one
machine, one thing happening at a time conceptually. Exposing internal
capabilities through a typed API is what lets a dashboard, a second client,
or a background worker all talk to the same VORTEX brain without duplicating
its logic.

**REST + OpenAPI:** FastAPI generates an OpenAPI schema from your typed
Python function signatures (via Pydantic models), which means the API's
contract is enforced by the type system, not just documented in a wiki that
drifts out of date.

**WebSockets/SSE for progress:** a long-running task (like Phase 12's job
applications) can't just return a single HTTP response — the caller wants
live updates as it works. Persistent connections (WebSocket) or a one-way
event stream (Server-Sent Events) are the two standard answers to
"push updates to a client without polling."

**Events vs. calls:** a direct function call is synchronous and tightly
coupled — the caller waits and knows exactly who handles it. Publishing a
domain event ("task.completed", "agent.action.taken") is asynchronous and
decoupled — any number of listeners can react without the publisher knowing
or caring who they are. This is the shift from "call a function" to
"something happened."

**Redis vs. Kafka — different jobs, not tiers of the same job:** Redis is
fast, mostly-ephemeral shared memory — cache, session state, a distributed
lock — accessed with sub-millisecond latency. Kafka is a durable,
replayable, ordered log of events meant for high-volume streams where you
need to reprocess history or fan a single event out to many independent
consumers. Reaching for Kafka because it sounds more serious than Redis,
without an actual durability/replay/fan-out requirement, is the anti-pattern
the blueprint explicitly warns against.

---

## Phase 10 — Durable Workflow Orchestration

**The core idea:** a Python `for` loop with `try/except` around each step is
a workflow engine until the process crashes halfway through step 4 — at
which point it's nothing, and you've lost track of what happened. Durable
execution is what survives that.

**Durable execution (Temporal):** a workflow's code runs as normal-looking
Python, but the *history of events* (which steps ran, what they returned) is
persisted outside the process. If the worker crashes, a new worker replays
that history to reconstruct exactly where execution was and continues from
there — you get resumability without hand-rolling a state machine and a
database table for every workflow you write.

**Sagas:** a multi-step process where some steps have side effects (charge a
card, send an email) needs an explicit compensation plan for what to undo if
a later step fails — a saga is that plan, made explicit rather than hoped
for.

**Idempotent activities:** because a crashed-and-resumed workflow might retry
a step that actually *did* complete before the crash was recorded, every
individual activity (an "activity" is Temporal's term for one side-effecting
step) has to be safe to run twice with the same input — this is a design
discipline (use idempotency keys, check-before-act) more than a library
feature.

**Human-in-the-loop as a first-class primitive:** "pause until a person
approves this" is not a special case bolted on top — durable workflow
engines support waiting on an external signal indefinitely (hours, days) as
a normal workflow state, which is exactly what Phase 12's application
approval gate needs.

---

## Phase 11 — Resume & Career Intelligence Capability

**The core idea:** turning a resume and a job description into a defensible
match score is an information-extraction and semantic-matching problem, not
a black-box "ATS score" — and it has to explain itself, because a number with
no reasoning behind it isn't useful to act on.

**Structured extraction:** turning free-text resume prose into typed fields
(skills, dates, employers, achievements) is what makes comparison possible at
all — you can't semantically compare "5 years" against a requirement without
first extracting that "5 years" is a duration attached to a specific role.

**Hard requirements vs. semantic fit:** some JD requirements are binary gates
("must have security clearance") that no amount of semantic similarity
should override; others are genuinely fuzzy ("strong communication skills")
where embedding-based matching adds real value. Conflating the two produces
a system that's either too rigid or dangerously lenient.

**Explainability over a single opaque number:** a score with no breakdown
("matched 8/10 required skills, missing: X, Y") isn't actionable — the
system has to expose *why* it scored something a way, which is what makes
the tailoring suggestions downstream meaningful instead of guesswork.

**No fabrication, ever:** tailoring wording to better match a JD is fine;
inventing experience the person doesn't have is not — this is a hard
boundary the blueprint repeats in multiple phases (11, 12, 18) because it's
the one that turns a helpful tool into professional fraud if crossed.

---

## Phase 12 — Job Discovery & Safe Application Automation

**The core idea:** everything from Phase 3 (browser automation), Phase 10
(durable workflows), and Phase 11 (matching) composes here into an
end-to-end pipeline — with human approval gates at every point where a
mistake would be hard to undo.

**Normalization and deduplication:** job listings scraped from many sources
describe the same role in different shapes and sometimes literally the same
posting twice — a normalization layer maps everything to one schema before
ranking, or you're ranking noise.

**Ranking as a first-class output, not just a filter:** the system should be
able to say *why* a job ranked where it did (same explainability principle
as Phase 11), so a user can trust or override the ordering.

**Pausing at the right boundaries:** CAPTCHA, MFA, and ambiguous free-text
application questions are exactly the points where automation should stop
and hand control back — this is a hard boundary in the blueprint's own
non-goals (Phase 18), not just a nice-to-have.

**Auditability:** every submitted application needs a durable, timestamped
record (what was submitted, when, using which resume variant) — this is what
lets Phase 8's email intelligence later correlate an interview invite back
to the specific application it's about.

---

## Phase 13 — Multi-Agent Orchestration

**The core idea:** once you have several genuinely different domains of
capability (system control, browsing, career, coding...), a single planner
trying to hold all of that context and all of those tools at once becomes
unreliable. Splitting into specialized agents behind one supervisor is a
reliability technique, not a buzzword — the user should never see the seams.

**Supervisor/router pattern:** one component decomposes a task and delegates
pieces to specialists, tracking shared state and enforcing budgets/timeouts
so one runaway agent can't consume unlimited tool calls or tokens.

**State graphs vs. plain function calls:** a framework like LangGraph
represents an agent's execution as an explicit graph of states and
transitions (including loops and conditionals), which makes complex,
branching agent behavior inspectable and debuggable in a way a tangle of
nested function calls isn't.

**Deterministic workflows vs. agentic planning:** the same principle as
Phase 4, one level up — a known, repeatable business process (submit an
application, ingest a document) should be a durable *workflow* (Phase 10)
with a fixed shape; genuinely open-ended tasks ("help me plan a career
pivot") are where an LLM-driven planner earns its cost. Using an agent where
a workflow would do is slower, less predictable, and harder to test.

---

## Phase 14 — Developer Agent & High-End Software Generation

**The core idea:** generating code that compiles is easy; generating code
that's *correct*, tested, and safely integrated into an existing repository
is a much harder, iterative problem that looks like how a careful engineer
actually works — read context first, plan, implement, verify, repair.

**Repository-aware context before edits:** an agent that edits code without
reading the surrounding conventions, existing abstractions, and tests
produces code that's locally plausible and globally wrong — this is the
same discipline any engineer joining a new codebase needs.

**Sandboxed execution:** running LLM-generated code — even to just test
it — on the host machine with full privileges is a real security risk. An
isolated workspace or Docker sandbox with resource/time limits contains the
blast radius of both bugs and (deliberately or not) malicious generated code.

**Bounded repair loops:** "run tests, if they fail analyze and patch, try
again" is powerful but needs an explicit iteration cap — without one, a
stuck agent can loop indefinitely, burning tokens and time without
converging.

**Human approval before Git side effects:** generating a branch and a diff
is safe to do autonomously; pushing, opening a PR, or merging are visible,
hard-to-fully-reverse actions that should always pass through explicit
approval, exactly like the destructive-OS-action confirmations in Phase 2.

---

## Phase 15 — Security, Identity & Policy Enforcement

**The core idea:** security controls that get added once "everything's
already built" are usually wrong, because they get bolted onto an
architecture that wasn't designed to be checked. This phase formalizes
controls that should already exist in some form from Phase 2 onward — it's
the phase where they become systematic instead of ad hoc.

**Least privilege:** each agent or tool should be authorized for exactly the
capabilities it needs and nothing more — VORTEX's `protected` process
allowlist (Phase 2) is a small, concrete instance of this principle applied
to one capability (process termination).

**Secrets outside source code:** a `.env` file loaded via `dotenv` is fine
for local development but is still a plaintext file — production-grade
secret handling means OS keyring or a Vault-class secret manager, and never,
ever a committed file.

**Policy engine for consequential actions:** rather than scattering
"should I confirm this?" logic across every feature, a central policy module
evaluates every high-risk action (destructive command, external submission,
data share) against a consistent rule set — this is the generalization of
the `awaiting_confirmation` pattern already in Phase 2's code.

**Threat modeling and dependency scanning:** systematically asking "what
could go wrong here, and who would benefit from it going wrong" for each new
capability, plus automated scanning (Dependabot, pip-audit, Trivy) for known
vulnerabilities in the packages you depend on — security debt compounds
quietly if nobody's watching for it.

---

## Phase 16 — Production Platform: Observability, Performance, Deployment & Evolution

**The core idea:** a system nobody can see into can't be reliably operated,
debugged, or improved — observability isn't a nice dashboard, it's the
precondition for knowing whether anything is actually working.

**Distributed tracing:** a single user command might touch the wake
detector, the STT call, the LLM, and a tool execution — a trace ID that
follows the request through all of them is what lets you answer "why was
this slow" instead of guessing from disconnected logs.

**Metrics and SLOs:** deciding in advance what "good" looks like (wake
latency, command latency, task success rate) turns "it feels slow lately"
into "p95 command latency crossed our 2-second SLO on Tuesday," which is
actionable.

**CI/CD and staged rollout:** automated tests and evaluation suites gating
every merge, plus the ability to roll back a bad release, is what makes
shipping changes routine instead of terrifying — this is Phase 0's
foundation, matured into an actual pipeline with multiple environments.

**Load testing and continuous evaluation:** performance and correctness both
degrade silently under conditions you didn't test for (concurrent users,
edge-case inputs, a model update) — periodic, automated re-evaluation is
what catches that before a user does.

---

## A closing note on the vision (train new models, "everything possible")

It's worth being precise about what's realistic here. VORTEX already has one
genuine instance of "trains its own model" — the wake-word pipeline in
`tools/wakeword/`, which really does synthesize data, extract features, train
a classifier, and export a working model with no manual model-authoring.
That's real and worth being proud of. It is a long way, architecturally and
in effort, from "trains arbitrary new AI models on demand" or "can do
everything possible" — those are north-star statements, not near-term
scope. The blueprint's own framing is the right one to hold onto: VORTEX
becomes "complete" not by having every fashionable technology installed, but
by reliably executing well-scoped tasks end-to-end, with verification,
recovery, and audit at every step. Scope creep toward "ultimate" and
"everything" is exactly what phase-by-phase, acceptance-scenario-gated
development (Phase 0's own contract) is designed to prevent.
