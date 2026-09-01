"""Self-demonstration content (2026-09-01) - what "VORTEX, demonstrate
yourself" actually has to say, not just a mode switch.

**Deliberately NOT routed through the LLM.** The first version of this did
send a big grounding block (all ~29 raw capability descriptions plus build
history, relationship, plans, and drawbacks) to Ollama and asked it to
narrate them conversationally. Live-tested 2026-09-01 against llama3.2:1b:
even with a raised token budget, the model only recited a handful of the
capability strings near the start of the prompt, silently dropped the
build-history/relationship/plans/drawbacks sections entirely, and closed
with "Would you like to proceed with any of these actions?" - misreading a
self-introduction as an action menu. This is the same class of failure
already documented for this model elsewhere (llm/tools.py's tool-calling
hallucinations): asking a 1B-parameter model to synthesize several distinct
facts into one coherent answer is unreliable, and for a request that
explicitly needs to "explain all" of several topics, completeness matters
more than LLM-generated variety. The content below is hand-composed
instead, spoken directly (app.py's demonstrate_self() calls self.speak(),
not the LLM) - 100% reliable, complete, and still first-person/
conversational because it was written that way, not because a model
happened to phrase it well this one time.

CAPABILITIES_SUMMARY is a curated, hand-written overview grouped by real
category (checked against core/intent_router.py's ALL_INTENT_TYPES, listed
below via list_capabilities() so a test can catch it drifting from reality)
rather than a literal recitation of all ~29 individual Intent descriptions,
which would read like a terms-of-service document, not an introduction.
BUILD_STORY/KNOWN_DRAWBACKS/FUTURE_PLANS are the same kind of hand-written,
factual content IMPLEMENTED.md/CHANGELOG.md already hold themselves to -
not "canned jokes" (the kind of content the feature spec explicitly ruled
out for personality.py's tone directives), a factual project summary a
human maintains and updates as things actually change.
"""
from . import intent_router as intents

CAPABILITIES_SUMMARY = (
    "I can control your desktop - opening and closing applications, locking "
    "the workstation, and shutting down or restarting the system with your "
    "confirmation first. I can browse the web, search, click things, and read "
    "pages back to you. I can find, read, summarize, and answer questions "
    "about your documents. I can check your email and draft replies for your "
    "approval before sending. I remember our past conversations and can "
    "recall them later. And I can change how I sound - switching between "
    "professional, friendly, witty, protective, and this demonstration mode "
    "you just triggered."
)

BUILD_STORY = (
    "I started as a single script wired straight to Ollama and a wake-word "
    "model. Over time I was rebuilt into separate pieces - voice, reasoning, "
    "memory, and a capability system - each one tested on its own, with a "
    "continuous integration pipeline that runs my whole test suite on every "
    "change."
)

KNOWN_DRAWBACKS = (
    "I run on a small local language model, so my reasoning is limited "
    "compared to larger cloud models, and structured tool-calling is "
    "currently switched off by default because it was unreliable in "
    "testing. My vision is limited to reading text off the screen, not "
    "understanding images. I don't yet handle messaging or phone calls, "
    "and I occasionally mishear my own wake word."
)

FUTURE_PLANS = (
    "Planned next steps include more proactive, observational behavior "
    "instead of only responding when spoken to, expanding communication "
    "beyond email, and eventually a multi-agent design where different "
    "models handle different kinds of requests."
)


def list_capabilities():
    """Real capability descriptions, one per routable Intent type (excludes
    Unhandled, the LLM-fallback marker, which isn't a capability). Not
    spoken directly (see module docstring) - kept so a test can verify
    CAPABILITIES_SUMMARY above hasn't silently drifted from what VORTEX
    actually routes, as new capabilities are added over time."""
    return [intent_type.description for intent_type in intents.ALL_INTENT_TYPES
            if intent_type is not intents.Unhandled]


def describe_relationship(memory_stats):
    """memory_stats: the dict MemoryStore.stats() returns. Returns a short,
    honest, real-numbers sentence - "we've barely spoken" is as true an
    answer as a long history, and both are preferable to a made-up one."""
    turn_count = memory_stats['turn_count']
    if turn_count == 0:
        return "We haven't had a conversation yet before this one."
    if turn_count < 10:
        return f"We've exchanged {turn_count} messages so far - still early days."
    return f"We've exchanged {turn_count} messages together" + (
        f" since {memory_stats['first_turn_at']}." if memory_stats['first_turn_at'] else '.')


def build_demo_segments(memory_stats):
    """The complete self-introduction as separate topic segments, in speaking
    order - NOT one joined string. app.py's demonstrate_self() speaks each
    segment as its own utterance with a real pause in between, rather than
    one continuous ~107-second monologue.

    Why this matters, live-tested 2026-09-01: the wake detector logged ZERO
    diagnostic scores - not even a low, failed attempt - for the entire
    duration of a single unbroken demo utterance, while a wake attempt made
    the instant VORTEX fell silent afterward succeeded within milliseconds
    (score 0.752, immediately triggered). This is this project's
    already-documented near-field self-noise limitation (VORTEX's own voice
    masking the mic - see config.py's tts_volume docstring), but no other
    feature had ever produced speech this long in one unbroken block before,
    so nothing had stress-tested it this severely. Splitting into segments
    with real silent gaps between them doesn't fix the underlying acoustic
    problem (true acoustic echo cancellation isn't implemented - see
    KNOWN_DRAWBACKS), but it gives the wake model several genuine quiet
    windows across the introduction instead of none at all."""
    return [
        CAPABILITIES_SUMMARY,
        BUILD_STORY,
        describe_relationship(memory_stats),
        FUTURE_PLANS,
        KNOWN_DRAWBACKS,
    ]


def build_demo_speech(memory_stats):
    """The same content as build_demo_segments(), joined into one string -
    kept for callers that want the full text as a single value (e.g. a
    length/content check in a test), not for speaking aloud in one piece."""
    return ' '.join(build_demo_segments(memory_stats))
