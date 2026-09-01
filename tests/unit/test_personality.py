"""Unit tests for core/personality.py - Standby/Activation/Personality
foundation, added 2026-09-01. build_system_prompt() is a pure function
(base prompt + mode string in, prompt string out) so these need no mocking,
matching intent_router.py's own testing style.
"""
import re
import sys

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.core.personality import PersonalityMode, build_system_prompt
from vortex.core.social_context import SocialLabel


def test_every_mode_produces_a_distinct_prompt():
    base = 'BASE PROMPT'
    prompts = {mode: build_system_prompt(base, mode) for mode in PersonalityMode}
    assert len(set(prompts.values())) == len(PersonalityMode)
    for mode, prompt in prompts.items():
        assert prompt.startswith(base)


def test_protective_mode_explicitly_permits_disagreeing_with_owner():
    """Non-negotiable principle from the feature spec (Phase 5): VORTEX must
    never blindly defend the owner when the owner is technically wrong. This
    must be literal text reaching the model, not just a comment somewhere."""
    prompt = build_system_prompt('BASE', PersonalityMode.PROTECTIVE)
    assert 'never blindly defend' in prompt
    assert 'factually wrong' in prompt


def test_demo_mode_prohibits_internal_details():
    prompt = build_system_prompt('BASE', PersonalityMode.DEMO)
    assert 'diagnostics' in prompt or 'implementation details' in prompt


_PROHIBITED = re.compile(r'\b(insult|threaten|attack)\w*\b')
_NEGATION_WORDS = ('not ', 'never ', 'nor ', 'or ')


def test_no_mode_ever_produces_an_aggressive_directive():
    """Structural guarantee, not a statistical one - see social_context.py's
    docstring: no label it can produce may map to an aggressive/insulting/
    threatening directive. Checked here across every mode x every label
    combination, not just the default. Every occurrence of a
    prohibited-action word must be immediately preceded by a negation
    ("do not insult", "never threaten", "or threaten") - never an
    instruction to actually do it. Checked per-occurrence via a plain
    substring window (not one combined regex) so an earlier occurrence
    can't be silently swallowed by a greedy match built for a later one."""
    for mode in PersonalityMode:
        for label in SocialLabel:
            prompt = build_system_prompt('BASE', mode, label).lower()
            for match in _PROHIBITED.finditer(prompt):
                window = prompt[max(0, match.start() - 20):match.start()]
                assert any(neg in window for neg in _NEGATION_WORDS), (
                    f'mode={mode}, label={label}: unnegated {match.group()!r} in {prompt!r}')


def test_social_label_accepts_enum_or_raw_string_value():
    via_enum = build_system_prompt('BASE', PersonalityMode.PROFESSIONAL, SocialLabel.FRIENDLY_TEASING)
    via_string = build_system_prompt('BASE', PersonalityMode.PROFESSIONAL, 'friendly_teasing')
    assert via_enum == via_string


def test_normal_and_ambiguous_labels_add_no_extra_directive():
    base_only = build_system_prompt('BASE', PersonalityMode.PROFESSIONAL)
    with_normal = build_system_prompt('BASE', PersonalityMode.PROFESSIONAL, SocialLabel.NORMAL)
    with_ambiguous = build_system_prompt('BASE', PersonalityMode.PROFESSIONAL, SocialLabel.AMBIGUOUS)
    assert base_only == with_normal == with_ambiguous


def test_technical_criticism_directive_says_agree_when_valid():
    prompt = build_system_prompt('BASE', PersonalityMode.PROFESSIONAL, SocialLabel.TECHNICAL_CRITICISM)
    assert 'valid' in prompt.lower()
