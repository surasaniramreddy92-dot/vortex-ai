"""Unit tests for core/social_context.py - Standby/Activation/Social-Context
foundation, added 2026-08-20. classify() is pure (text in, SocialLabel out),
tested the same way intent_router.route() is: plain strings in, no mocking.

These are also the direct tests for feature-spec Phase 8 scenarios 11-13
(technical criticism doesn't trigger protective behavior, friendly teasing
can trigger a lightweight policy, owner-directed abuse doesn't cause
aggressive behavior).
"""
import sys

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.core.social_context import SocialLabel, classify


def test_plain_conversation_is_normal():
    assert classify('what is the capital of france') == SocialLabel.NORMAL


def test_technical_criticism_detected():
    assert classify('this approach is wrong, the logic has a bug') == SocialLabel.TECHNICAL_CRITICISM


def test_friendly_teasing_detected():
    assert classify("you're so dumb sometimes lol") == SocialLabel.FRIENDLY_TEASING


def test_friendly_teasing_without_insult_also_detected():
    assert classify('haha nice one') == SocialLabel.FRIENDLY_TEASING


def test_owner_directed_disrespect_detected():
    assert classify('your boss is an idiot') == SocialLabel.OWNER_DIRECTED_DISRESPECT


def test_genuine_abuse_detected():
    assert classify('you are so stupid and useless') == SocialLabel.GENUINE_ABUSE


def test_insult_plus_technical_language_is_ambiguous_not_a_guess():
    """Conflicting signals (harsh criticism that could be blunt technical
    feedback or could be abuse) must fail closed to AMBIGUOUS, not pick a
    side - see this module's docstring."""
    assert classify('this stupid code is broken and wrong') == SocialLabel.AMBIGUOUS


def test_classify_never_returns_anything_but_a_social_label():
    for text in ('', 'hello', 'you idiot', 'lol jk', 'the bug is in your boss code stupid'):
        assert isinstance(classify(text), SocialLabel)
