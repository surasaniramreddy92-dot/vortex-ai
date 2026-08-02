# VORTEX Learning Guide — Concepts by Phase

This is a study document, not a status report (for status, see
[IMPLEMENTED.md](../IMPLEMENTED.md)). It only covers phases with real work in
the repo so far, and explains the *concepts* behind each in enough depth to
actually learn them — what the idea is, why it exists, why it's the right
tool for this problem, and how VORTEX's own code does it concretely. New
phase sections get added here in the same batch as the phase's code, so this
file grows alongside the repo instead of describing work that doesn't exist
yet.

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

*Where VORTEX stands:* a git repo, `.gitignore`, and a first commit exist.
No typed config, no CI, no tests, no lint gate yet — the single most valuable
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

**STT/TTS as providers, not fixtures:** the long-term target is offline STT
(faster-whisper/whisper.cpp) and offline TTS (Piper), with cloud options as
adapters, specifically so the assistant keeps working without internet and
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
performing a *sequence* of actions on a page — navigate, read, click — the
same way a human would, but voice-triggered.

**DOM and locators:** Playwright's `page.locator(...)`/`get_by_text(...)`
find elements by matching visible text or structure rather than brittle
pixel coordinates or auto-generated class names — this is what lets "click
sign in" work regardless of exactly where that button is laid out on the
page.

**Why the browser is visible, not headless:** `BrowserAgent` launches
Chromium with `headless=False` on purpose. This is a personal assistant
meant to be watched acting on your behalf, not a silent background scraper —
seeing the page actually navigate when you say "go to github.com" is part of
the point.

**The Google bot-detection story (a genuinely useful thing to understand,
not just a workaround):** the first version of `search()` queried Google
directly and parsed `#search h3` results. In practice, Google served the
automated browser its "unusual traffic detected" bot-check page almost
immediately — verified directly by inspecting the response (`page.url`
redirected to `google.com/sorry/index`, the body was literally the bot-check
copy). The fix was **not** to add stealth plugins or fingerprint spoofing to
get past that — doing so would mean deliberately bypassing an anti-abuse
mechanism, which is explicitly off-limits (both in this project's own
blueprint and more broadly). The actual fix: switch to
`html.duckduckgo.com`, a simple, server-rendered results page designed to be
lightweight, that returns clean results with no bot wall for a normal,
low-volume, single query. **The lesson generalizes:** when automation hits a
site's anti-bot defense, the correct engineering response is "use a
different, compliant path to the same information," not "defeat the
defense" — this is the same non-negotiable boundary the Career/Job-Discovery
phases (11-12) call out explicitly around CAPTCHA and MFA.

**Lazy initialization:** the browser only launches on first actual use
(`_ensure_started()`), not at VORTEX startup — no reason to pay Chromium's
startup cost and hold a browser process open for a session that never
touches the web.

**A second real bug, worth understanding rather than just noting as fixed:**
"open youtube and play some badminton tournament" was, for a while, silently
swallowed whole by the *old* app-launcher's generic `open (.+)` regex — not
by anything in `browser.py`. `open_target()` checked whether the entire
phrase was a known app or an exact `web_apps` dictionary key (it wasn't —
the dictionary only has the bare word `"youtube"`, not a sentence containing
it), found nothing, and fell back to opening a literal Google search of the
whole phrase in the *system's default browser* — a second, uncontrolled
browser window entirely separate from the Playwright one, with no click, no
video, and no spoken feedback about what actually happened. Two lessons:
first, a regex that matches too broadly (`open (.+)` matches almost
anything) needs a fallback that's actually good, because it *will* end up
handling inputs nobody designed it for. Second, once a project has one
properly controlled, automatable browser session, *every* web-opening code
path should route through it — having two different browsers fire depending
on which regex happened to match is confusing and much harder to reason
about than "the browser" always meaning one specific, observable thing. The
fix added an explicit YouTube search-and-click-first-result action ahead of
the generic fallback, and made the fallback itself route through the same
`BrowserAgent` instead of the system browser.

---

## Phase 5 — Memory & RAG (conversation history + real document retrieval)

This phase ended up in two genuinely different pieces, built at different
times, and it's worth understanding why they're separate rather than one
system.

### Part 1: conversation memory (SQLite)

**Why SQLite, not a client-server database, for this:** SQLite is a single
file, no server process, no configuration, built into Python's standard
library (`sqlite3`) — for "persist a list of conversation turns for one
desktop user," a client-server database would be pure overhead with zero
benefit. `MemoryStore.add_turn(role, content)` writes a row; `.recent(n)`
reads the last `n` turns back out, oldest-first, in exactly the
`{'role', 'content'}` shape Ollama's `messages` list expects — so
`ask_llm_stream` barely changed, it just reads/writes through the store
instead of a Python list. This is plain chronological recall, not
retrieval — there's no search over *which* past turns are relevant to a new
question.

### Part 2: document RAG (PostgreSQL + Qdrant, the "full production stack")

**Why this got a real client-server stack when conversation memory didn't:**
because there was an actual, evidenced problem it solves. Document Q&A
originally worked by extracting a document's full text, truncating it to
12,000 characters, and stuffing whatever was left into the prompt — a real,
named limitation (a 50-page document would silently lose everything past
the cutoff). Fixing that properly means: don't send the whole document,
send *only the parts relevant to the question*. That's what retrieval is
for, and it's what justified adding real infrastructure rather than another
SQLite file.

**Embeddings and cosine similarity, concretely:** an embedding model turns a
piece of text into a fixed-length vector (here, 768 numbers, from
`nomic-embed-text` running locally via Ollama) such that semantically
similar text produces vectors that point in a similar direction. "Similar
direction" is measured by cosine similarity — the cosine of the angle
between two vectors, 1.0 for identical direction, 0 for unrelated, negative
for opposite. This is what lets a question like "what course did they
complete?" retrieve a chunk containing "successfully completed Getting
Started with Amazon S3" even though the words barely overlap — the model
learned that those phrases mean related things.

**Chunking is a real design decision, not a mechanical split:**
`_chunk_text()` cuts text into ~800-character windows with 100 characters of
overlap, snapped to the nearest paragraph or sentence boundary where one
exists nearby (`text.rfind('\n\n', ...)`, then `. `, then `\n`, whichever is
closest to the target size). Plain fixed-size slicing would cut sentences in
half at chunk boundaries, and a lost sentence fragment at a boundary can
mean the one fact a question needed ends up split across two chunks and
found well in neither. The overlap exists for the same reason: a fact that
straddles where a chunk *would* end without overlap has a second chance to
be fully captured in the next chunk instead.

**Why Postgres for one thing and Qdrant for another, not just Qdrant for
everything:** Qdrant is excellent at "find the nearest vectors to this
query" and mediocre at being a general-purpose durable datastore with rich
querying, joins, and constraints. Postgres is the reverse. Here: Postgres
holds `documents` (path, filename, a content hash) and `chunks` (the actual
chunk text, tied to a document by foreign key) — the durable, re-derivable
source of truth. Qdrant holds the same chunk text a second time, denormalized
into each point's payload, purely so a retrieval query doesn't need a second
round-trip to Postgres in the hot path. If the Qdrant collection were ever
lost or rebuilt, Postgres has everything needed to re-embed and re-index
without re-reading any files from disk. This is exactly the blueprint's own
stated division of labor (relational/transactional truth in Postgres,
vectors in Qdrant), applied at the smallest scale that still demonstrates it
honestly — one machine, one user, not a distributed production deployment.

**Content-hash-based re-ingestion:** `ensure_ingested()` hashes the extracted
text (SHA-256) and compares it against what's stored for that path. If
unchanged, it's a no-op — re-asking a question about the same file doesn't
re-chunk, re-embed, and re-upsert everything from scratch every time,
because nothing about the document changed since it was last indexed. If the
file *has* changed, the old chunks are deleted and replaced, keyed by the
new hash.

**Filtered vector search, not global search:** `retrieve()` passes a
`Filter(must=[FieldCondition(key='document_id', match=MatchValue(...))])`
alongside the query vector — Qdrant only searches chunks belonging to *this*
document, not the whole collection. Without that filter, asking about one
document could retrieve suspiciously relevant-sounding chunks from an
entirely different one.

**Graceful degradation as a first-class design requirement, not an
afterthought:** Postgres and Qdrant are separate local services someone has
to actually have running — they are not guaranteed to be up the way, say,
`sqlite3` (built into Python) always is. `RagStore()`'s construction is
wrapped in a `try/except` in `Vortex.__init__`; if it fails, `self.rag` is
`None` and document Q&A silently falls back to the older truncated-
whole-document approach instead of preventing VORTEX from starting at all.
A capability that depends on optional external infrastructure has to define
what "infrastructure is down" looks like *before* it ships, not discover it
the first time someone forgets to start Qdrant.

**What's still not built:** hybrid dense+sparse search, reranking,
cross-document retrieval (asking a question across your whole document
folder at once, rather than one named file), and migrating conversation
history onto this same stack. Each is a real, separate piece of further
work, not implied by what exists today.

---

## Phase 7 — Document Intelligence (read/summarize/QA)

**The core idea, scoped honestly:** the full blueprint phase includes
layout-aware extraction with page/table provenance and OCR fallback for
scanned documents. What's built now is the concrete slice that's useful
immediately: "read this PDF/DOCX/XLSX aloud" and "answer a question about
this specific document," for one file at a time.

**Format-specific extraction, one adapter per type:** `extract_text()`
dispatches on file extension — PyMuPDF (imported as `fitz`) for PDFs,
`python-docx` for `.docx` paragraphs, `openpyxl` for spreadsheet cells (each
row joined with `|` so tabular structure survives as plain text well enough
for an LLM to reason about it). There's no universal document parser
because there's no universal document — each format has a genuinely
different internal model, so each gets its own small adapter, normalized to
one plain-text output. It returns the *full* text, unmodified — no
truncation happens inside this module; that's a decision left to whoever
calls it, and the two callers now make different decisions (see below).

**Two different documents-related problems, deliberately solved
differently:** "summarize this document" and "what does this document say
about X" sound similar but aren't. Summarization inherently wants the whole
document — you can't summarize what you didn't read — so `summarize_document()`
still truncates to `SUMMARY_MAX_CHARS` (12,000) and accepts the same
named limitation as before: a long document loses information past that
cutoff for a plain summary. Targeted questions are exactly what semantic
retrieval is good at, so `answer_document_question()` now routes through
Phase 5's RAG stack instead — the document gets chunked/embedded/indexed
(once, cached by content hash) and only the chunks relevant to *this
specific question* get sent to the LLM, regardless of how long the source
document is. Recognizing that these are different problems, rather than
reaching for one mechanism to force onto both, is the actual engineering
decision here.

**Filename resolution as its own small problem:** `resolve_document(name)`
searches Desktop/Documents/Downloads for a file whose name or stem matches
what was said, preferring an exact match and falling back to the most
recently modified candidate — because voice input gives you "my resume,"
not a full file path, and something has to bridge that gap.

**Reused the same streaming/barge-in pattern as chat, deliberately:**
`_stream_document_answer` is structurally identical to `ask_llm_stream` —
same `stream=True` Ollama call, same `stop_speaking` check per token. A
document summary can take a while to generate; interrupting it mid-sentence
had to work exactly the same way interrupting a normal answer does, so the
existing pattern was reused rather than inventing a second, less-consistent
cancellation mechanism.

---

## Phase 4 — LLM Brain, Tool Calling & Hybrid Intent Routing

**The core idea:** an LLM is a powerful, unreliable-in-detail reasoning
engine. The architecture question is where to draw the line between "let the
model figure it out" and "run a piece of code that always does the same
thing." VORTEX's answer is: deterministic first, LLM only for what's
genuinely open-ended.

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
conditions — you don't want the app either fully deafened to its own name
while talking (barge-in would be impossible) or exactly as sensitive as
standby (false triggers while VORTEX's own voice plays through the speakers
would be more likely). VORTEX distinguishes the two with a separate
`BARGE_IN_THRESHOLD`, but **a stricter-than-standby threshold turned out to
be the wrong instinct**: an early version set it *higher* than
`WAKE_THRESHOLD` as a blanket "extra safety margin," which broke genuine
interruptions instead — barge-in is inherently *harder* to score high on
(the mic hears VORTEX's own speech at the same time as the user's voice,
diluting the signal), and every false trigger actually observed in practice
happened during standby, none during barge-in. Making the hardest-to-reach
case also the highest bar was backwards; `BARGE_IN_THRESHOLD` was lowered
below `WAKE_THRESHOLD` once the evidence (from the diagnostic logging below)
didn't support the stricter value. The lesson: a "safety margin" added out
of caution, without evidence, is still a guess — and guesses should be
correctable the moment real data disagrees with them.

**Concurrency and cancellation primitives:** VORTEX uses a `threading.Event`
(`stop_speaking`) as a shared cancellation flag checked at every yield point
in TTS synthesis, audio playback, and LLM token streaming — this is the
mechanism, not the state machine, that actually stops speech within
milliseconds of a barge-in being detected. A `Queue` connects the always-on
audio callback (which must never block) to a slower worker thread that owns
every expensive operation (STT, LLM, TTS).

**Session mode:** requiring the wake word before *every single sentence* —
including yes/no answers to VORTEX's own confirmation questions — is a real
usability tax. VORTEX's `_active_session()` loop keeps listening for a
configurable inactivity window after any wake event, so a whole multi-turn
exchange (and any confirmation within it) happens without re-summoning the
assistant.

---

## Phase 15 — Security, Identity & Policy Enforcement

**The core idea:** security controls that get added once "everything's
already built" are usually wrong, because they get bolted onto an
architecture that wasn't designed to be checked. Baking in a few of these
controls early, even in a small form, is cheaper than retrofitting them
later.

**Least privilege:** each agent or tool should be authorized for exactly the
capabilities it needs and nothing more — VORTEX's `protected` process
allowlist (Phase 2) is a small, concrete instance of this principle applied
to one capability (process termination).

**Secrets outside source code:** a `.env` file loaded via `dotenv` is fine
for local development but is still a plaintext file — production-grade
secret handling means OS keyring or a Vault-class secret manager, and never,
ever a committed file. VORTEX's `.gitignore` excludes `.env`; there's an
`.env.example` with no real values instead.

**Policy engine for consequential actions:** rather than scattering
"should I confirm this?" logic across every feature, a central policy module
would evaluate every high-risk action (destructive command, external
submission, data share) against a consistent rule set. VORTEX today has the
minimal version of this idea — the `awaiting_confirmation` pattern in
`execute()` — but not yet a central policy module.

**Threat modeling and dependency scanning:** systematically asking "what
could go wrong here, and who would benefit from it going wrong" for each new
capability, plus automated scanning (Dependabot, pip-audit, Trivy) for known
vulnerabilities in the packages you depend on — security debt compounds
quietly if nobody's watching for it. Not set up yet in this repo.

---

## A closing note on the vision (train new models, "everything possible")

It's worth being precise about what's realistic here. VORTEX already has one
genuine instance of "trains its own model" — the wake-word pipeline in
`tools/wakeword/`, which really does synthesize data, extract features, train
a classifier, and export a working model with no manual model-authoring.
That's real and worth being proud of. It is a long way, architecturally and
in effort, from "trains arbitrary new AI models on demand" or "can do
everything possible" — those are north-star statements, not near-term scope.
Reliably executing well-scoped tasks end-to-end, with verification, recovery,
and audit at every step, is a better goalpost than "everything" — and it's
exactly what building and documenting this repo phase-by-phase, in verified
batches, is meant to demonstrate.
