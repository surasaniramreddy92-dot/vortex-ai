"""Unit tests for core/self_knowledge.py - the real, grounded content behind
"VORTEX, demonstrate yourself" (added 2026-09-01). Pure functions, no
mocking needed, matching intent_router.py's own testing style.

build_demo_speech() is spoken directly, not routed through the LLM - see
the module's own docstring for the live-tested reason (llama3.2:1b could
not reliably synthesize several real facts into one coherent answer, and
silently dropped most of them). These tests check the deterministic content
itself, not an LLM's paraphrase of it.
"""
import sys

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.core import intent_router as intents
from vortex.core.self_knowledge import (
    BUILD_STORY, CAPABILITIES_SUMMARY, DIFFERENTIATION_SUMMARY, FUTURE_PLANS, KNOWN_DRAWBACKS,
    build_demo_segments, build_demo_speech, describe_relationship, list_capabilities,
)


def test_capabilities_excludes_unhandled():
    caps = list_capabilities()
    assert intents.Unhandled.description not in caps


def test_capabilities_matches_every_real_routable_intent():
    """Real, auto-derived, always in sync - not a hand-maintained list that
    could silently drift from what execute() actually dispatches."""
    expected = {t.description for t in intents.ALL_INTENT_TYPES if t is not intents.Unhandled}
    assert set(list_capabilities()) == expected


def test_capabilities_nonempty_and_all_strings():
    caps = list_capabilities()
    assert len(caps) > 10
    assert all(isinstance(c, str) and c for c in caps)


def test_describe_relationship_no_prior_conversation():
    result = describe_relationship({'turn_count': 0, 'first_turn_at': None})
    assert "haven't had a conversation yet" in result


def test_describe_relationship_early_days():
    result = describe_relationship({'turn_count': 3, 'first_turn_at': '2026-09-01 10:00:00'})
    assert '3' in result
    assert 'early days' in result


def test_describe_relationship_established_history_includes_real_count_and_date():
    result = describe_relationship({'turn_count': 250, 'first_turn_at': '2026-08-01 09:00:00'})
    assert '250' in result
    assert '2026-08-01 09:00:00' in result


def test_describe_relationship_never_invents_a_date_when_none_is_known():
    result = describe_relationship({'turn_count': 50, 'first_turn_at': None})
    assert '50' in result
    assert 'None' not in result


def test_build_demo_speech_includes_every_topic():
    speech = build_demo_speech({'turn_count': 5, 'first_turn_at': '2026-09-01 08:00:00'})
    assert CAPABILITIES_SUMMARY in speech
    assert BUILD_STORY in speech
    assert FUTURE_PLANS in speech
    assert KNOWN_DRAWBACKS in speech


def test_build_demo_speech_reflects_real_memory_stats():
    speech = build_demo_speech({'turn_count': 42, 'first_turn_at': None})
    assert '42' in speech


def test_build_demo_speech_is_a_single_complete_string_not_truncated():
    speech = build_demo_speech({'turn_count': 0, 'first_turn_at': None})
    assert speech.endswith('.')
    assert len(speech) > 200


def test_build_demo_segments_returns_one_entry_per_sentence_not_per_topic():
    """app.py's demonstrate_self() speaks each of these as its own
    utterance with a real pause after every one - see this function's own
    docstring for why topic-level granularity alone wasn't enough
    (live-tested: CAPABILITIES_SUMMARY alone is six sentences, and a
    barge-in attempt made during it got nothing when the pause only landed
    between topics)."""
    segments = build_demo_segments({'turn_count': 5, 'first_turn_at': '2026-09-01 08:00:00'})
    # None of the multi-sentence constants should appear whole - each must
    # have been split into its individual sentences.
    assert CAPABILITIES_SUMMARY not in segments
    assert BUILD_STORY not in segments
    assert KNOWN_DRAWBACKS not in segments
    # FUTURE_PLANS is a single sentence already, so it DOES appear as-is.
    assert FUTURE_PLANS in segments
    assert len(segments) > 10, 'expected one segment per sentence, not per topic'
    assert all(segment.strip().endswith(('.', '!', '?')) for segment in segments)


def test_build_demo_segments_no_sentence_is_multiple_sentences():
    """Every segment must itself be a single sentence - the whole point of
    the sentence-level split - not a still-multi-sentence topic block."""
    segments = build_demo_segments({'turn_count': 5, 'first_turn_at': '2026-09-01 08:00:00'})
    for segment in segments:
        # A real sentence-ending mark should appear only once, at the very
        # end (allowing a trailing space) - more than one means this
        # "segment" is still multiple sentences glued together.
        interior = segment.rstrip('.!? ')
        assert not any(mark in interior for mark in '.!?'), (
            f'segment contains more than one sentence: {segment!r}')


def test_differentiation_summary_does_not_repeat_the_known_fabrication():
    """Direct user finding (2026-09-01): the plain LLM fallback, asked what
    makes VORTEX different, fabricated "I possess a unique ability to
    understand and respond to subtle emotional cues" - a capability VORTEX
    doesn't have. This real, honest replacement must never claim it."""
    lowered = DIFFERENTIATION_SUMMARY.lower()
    assert 'emotional' not in lowered
    assert 'subtle' not in lowered


def test_differentiation_summary_is_specific_not_generic_marketing():
    """Every claim should be something a generic assistant couldn't equally
    say - grounded in this actual project (local model, tested code,
    honest docs), not vague self-praise."""
    lowered = DIFFERENTIATION_SUMMARY.lower()
    assert 'local' in lowered
    assert 'tested' in lowered or 'test' in lowered
    assert 'honest' in lowered or 'partial' in lowered


def test_build_demo_segments_reflects_real_memory_stats():
    segments = build_demo_segments({'turn_count': 42, 'first_turn_at': None})
    assert any('42' in segment for segment in segments)


def test_build_demo_speech_matches_segments_joined():
    stats = {'turn_count': 7, 'first_turn_at': None}
    assert build_demo_speech(stats) == ' '.join(build_demo_segments(stats))


def test_capabilities_summary_does_not_overclaim_vision():
    """Honest-drawbacks discipline extends to the summary too - must not
    imply image/vision understanding that doesn't exist (see
    KNOWN_DRAWBACKS, which explicitly says vision is text-reading only)."""
    assert 'image' not in CAPABILITIES_SUMMARY.lower()
    assert 'see' not in CAPABILITIES_SUMMARY.lower()
