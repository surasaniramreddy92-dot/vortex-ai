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
    BUILD_STORY, CAPABILITIES_SUMMARY, FUTURE_PLANS, KNOWN_DRAWBACKS,
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


def test_build_demo_segments_returns_five_separate_topics_not_one_string():
    """app.py's demonstrate_self() speaks each of these as its own
    utterance with a real pause in between - see this function's own
    docstring for the live-tested barge-in reason a single joined
    ~107-second utterance is no longer used for speaking."""
    segments = build_demo_segments({'turn_count': 5, 'first_turn_at': '2026-09-01 08:00:00'})
    assert len(segments) == 5
    assert CAPABILITIES_SUMMARY in segments
    assert BUILD_STORY in segments
    assert FUTURE_PLANS in segments
    assert KNOWN_DRAWBACKS in segments


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
