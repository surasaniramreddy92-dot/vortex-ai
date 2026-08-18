"""docs/REFACTOR_PLAN.md Step 7: core/state_manager.py's current_state() is a
pure, read-only view over caller-supplied is_speaking()/is_in_active_session()
callables - tested here in isolation with plain lambdas, no Vortex/Session/
BargeIn needed at all.
"""
import sys

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.core.state_manager import VortexState, current_state


def test_neither_flag_set_is_standby():
    assert current_state(is_speaking=lambda: False, is_in_active_session=lambda: False) \
        == VortexState.STANDBY


def test_in_active_session_only_is_active_session():
    assert current_state(is_speaking=lambda: False, is_in_active_session=lambda: True) \
        == VortexState.ACTIVE_SESSION


def test_speaking_only_is_speaking():
    assert current_state(is_speaking=lambda: True, is_in_active_session=lambda: False) \
        == VortexState.SPEAKING


def test_speaking_takes_priority_over_active_session():
    """Mid-response, VORTEX is technically both speaking and inside an
    active session's follow-up window - SPEAKING is the more specific,
    more useful answer."""
    assert current_state(is_speaking=lambda: True, is_in_active_session=lambda: True) \
        == VortexState.SPEAKING


def test_computed_fresh_every_call_not_cached():
    flag = {'speaking': False}
    state = current_state(is_speaking=lambda: flag['speaking'], is_in_active_session=lambda: False)
    assert state == VortexState.STANDBY
    flag['speaking'] = True
    state = current_state(is_speaking=lambda: flag['speaking'], is_in_active_session=lambda: False)
    assert state == VortexState.SPEAKING
