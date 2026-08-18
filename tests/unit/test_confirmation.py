"""docs/REFACTOR_PLAN.md Step 6 exit criteria: verifies core/policy_engine's
is_affirmative() correctly parses yes/no, in particular without the
`'yes' in cmd` substring bug flagged in docs/CURRENT_STATE.md §6 (a misheard
"no, not yes I don't want that" used to confirm instead of decline). Tests
the pure classification function in isolation - end-to-end confirmation-gate
behavior through a real Vortex() (close-all/shutdown/restart/delete/move/
rename) is covered separately in test_registry.py and
test_file_confirmation.py, unaffected by this move.
"""
import sys

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.core.policy_engine import is_affirmative


def test_plain_yes_confirms():
    assert is_affirmative('yes') is True


def test_yes_with_trailing_words_confirms():
    assert is_affirmative('yes boss') is True


def test_yeah_yep_yup_sure_confirm_all_confirm():
    for word in ('yeah', 'yep', 'yup', 'sure', 'confirm', 'confirmed', 'proceed'):
        assert is_affirmative(word) is True, word


def test_go_ahead_and_do_it_confirm():
    assert is_affirmative('go ahead') is True
    assert is_affirmative('do it') is True


def test_plain_no_declines():
    assert is_affirmative('no') is False


def test_no_thanks_declines():
    assert is_affirmative('no thanks') is False


def test_nope_nah_cancel_stop_all_decline():
    for word in ('nope', 'nah', 'cancel', 'stop', 'negative'):
        assert is_affirmative(word) is False, word


def test_unrelated_speech_declines_same_as_before_the_fix():
    """Anything that isn't a recognized affirmative word declines - matches
    the original `'yes' in cmd` check's behavior for ordinary unclear
    captures, unaffected by this fix."""
    assert is_affirmative('why do i need to show to call you') is False


# ---------- the actual flagged bug ----------

def test_misheard_no_not_yes_declines_instead_of_confirming():
    """The exact scenario docs/CURRENT_STATE.md §6 flagged: the old
    `'yes' in cmd` substring check would have matched this and incorrectly
    confirmed a shutdown/restart/close-all/delete. Negation must win."""
    assert is_affirmative("no, not yes I don't want that") is False


def test_dont_want_that_declines():
    assert is_affirmative("don't want that") is False


def test_negative_word_wins_even_when_an_affirmative_word_is_also_present():
    assert is_affirmative('yes no wait actually no') is False
