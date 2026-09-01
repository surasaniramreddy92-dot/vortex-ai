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

**Barge-in, round two.** The first fix here (same day) grouped these four
constants into five TOPIC-level segments, each spoken with a pause after
it. Still failed on direct user re-test: CAPABILITIES_SUMMARY alone is six
sentences (~25-30s of continuous speech, since it's the constant a user
naturally tries to interrupt first), and the pause only ever landed
*between* topics, never within one - the log showed six "Speaking:" lines
fire back-to-back with no real gap, all from inside that single topic
block. build_demo_segments() now splits every constant down to individual
SENTENCES (see _sentences()), so the pause lands after literally every
sentence spoken, not just every topic.
"""
import re

from . import intent_router as intents

# Splits after a sentence-ending punctuation mark followed by whitespace,
# keeping the mark attached to the sentence before it - used by
# build_demo_segments() to break every multi-sentence constant below down
# to one sentence per spoken segment (see that function's docstring for why
# topic-level granularity alone wasn't enough).
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?]) +')


def _sentences(text):
    return _SENTENCE_SPLIT.split(text)


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

# Added 2026-09-02 - see WhatMakesYouDifferent's docstring in
# intent_router.py for the exact fabrication this replaces ("I possess a
# unique ability to understand and respond to subtle emotional cues" - not
# a real capability). Honest and specific on purpose, not generic
# assistant-marketing language: every claim here is independently checkable
# against this actual codebase (a local Ollama model, a real test suite,
# documentation that says "Partial" instead of overclaiming) rather than
# something a model would say about any generic assistant.
DIFFERENTIATION_SUMMARY = (
    "Unlike most assistants, I run entirely on your own machine, on a local "
    "language model - no cloud subscription, and your conversations don't "
    "leave this computer by default. I'm built from real, tested code with "
    "a public commit history, not a black box, and my own documentation "
    "marks features as partial or in progress rather than pretending "
    "everything works. I'd rather tell you honestly what I can't do yet "
    "than make something up."
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
    """The complete self-introduction as separate SENTENCE-level segments, in
    speaking order - NOT one joined string, and NOT one entry per broad
    topic either. app.py's demonstrate_self() speaks each segment as its
    own utterance with a real pause after every one, rather than one
    continuous ~107-second monologue.

    Why per-SENTENCE, not just per-topic, live-tested 2026-09-01: the first
    fix here grouped by topic (CAPABILITIES_SUMMARY/BUILD_STORY/etc. as five
    segments) and still failed - CAPABILITIES_SUMMARY alone is six
    sentences (~25-30s of continuous speech with zero internal breaks), and
    a real barge-in attempt made during exactly that block still got
    nothing: the log showed six "Speaking:" lines fire back-to-back with no
    real gap between them, all from *inside* that one topic-level segment,
    because the pause only ever landed *between* topics, never within one.
    Splitting every constant down to individual sentences means the pause
    lands after literally every sentence spoken, including within what used
    to be one long topic block.

    None of this fixes the underlying acoustic problem (true acoustic echo
    cancellation isn't implemented - see KNOWN_DRAWBACKS) - VORTEX's own
    voice still masks the mic while any one sentence is actually playing.
    It gives the wake model many more genuine quiet windows across the
    introduction than either previous version did."""
    topics = [CAPABILITIES_SUMMARY, BUILD_STORY, describe_relationship(memory_stats),
              FUTURE_PLANS, KNOWN_DRAWBACKS]
    return [sentence for topic in topics for sentence in _sentences(topic)]


def build_demo_speech(memory_stats):
    """The same content as build_demo_segments(), joined into one string -
    kept for callers that want the full text as a single value (e.g. a
    length/content check in a test), not for speaking aloud in one piece."""
    return ' '.join(build_demo_segments(memory_stats))
