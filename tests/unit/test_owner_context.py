"""Unit tests for core/owner_context.py - Standby/Activation/Owner-Context
foundation, added 2026-09-01. session_state/personality_mode are live
properties delegating to injected callables, not copied fields - the point
of these tests is proving that delegation is real (a change on the "host"
side is visible immediately, never a stale snapshot).
"""
import sys

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.core.owner_context import OwnerContext


def test_identity_fields_are_static():
    owner = OwnerContext(owner_id='primary_owner', display_name='Boss', preferred_address='Boss',
                          get_session_state=lambda: 'STANDBY', get_personality_mode=lambda: 'PROFESSIONAL')
    assert owner.owner_id == 'primary_owner'
    assert owner.display_name == 'Boss'
    assert owner.preferred_address == 'Boss'


def test_session_state_delegates_live_not_a_stale_snapshot():
    state = {'value': 'STANDBY'}
    owner = OwnerContext(owner_id='x', display_name='Boss', preferred_address='Boss',
                          get_session_state=lambda: state['value'], get_personality_mode=lambda: 'PROFESSIONAL')
    assert owner.session_state == 'STANDBY'
    state['value'] = 'ACTIVE_SESSION'
    assert owner.session_state == 'ACTIVE_SESSION'


def test_personality_mode_delegates_live_not_a_stale_snapshot():
    mode = {'value': 'PROFESSIONAL'}
    owner = OwnerContext(owner_id='x', display_name='Boss', preferred_address='Boss',
                          get_session_state=lambda: 'STANDBY', get_personality_mode=lambda: mode['value'])
    assert owner.personality_mode == 'PROFESSIONAL'
    mode['value'] = 'WITTY'
    assert owner.personality_mode == 'WITTY'
