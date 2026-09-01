"""Lifecycle tests for the Standby/Activation/Personality/Owner-Context
foundation (2026-09-01) - directly exercises the feature spec's Phase 8
scenario list (1-10, plus 15). Scenarios 11-14 (social-context/personality
non-aggression behavior) are covered in test_social_context.py and
test_personality.py, which test the same guarantees at the unit level
without needing a real Vortex().

Follows test_barge_in.py's `vortex_instance` fixture pattern (a real
Vortex() - no hardware touched at construction) for the dispatch-level
scenarios, and test_barge_in.py's direct-Session-construction pattern for
the pure timing/response-text scenarios that don't need a full Vortex().
"""
import sys
import threading
import time

import pytest

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.core.personality import PersonalityMode
from vortex.core.state_manager import VortexState
from vortex.voice.barge_in import BargeIn
from vortex.voice.session import Session


def make_session(**overrides):
    barge_in = overrides.pop('barge_in', None) or BargeIn()
    defaults = dict(
        events=None, barge_in=barge_in, session_timeout=8, wake_watchdog_timeout=5,
        capture_command=lambda timeout=8, allow_offline_on_unclear=True: None,
        execute=lambda cmd: None, speak=lambda text: None, greet=lambda: None,
        warm_up=lambda: None, get_last_audio_at=lambda: 0, recover_wake_stream=lambda: None,
        is_capturing=lambda: False, clear_awaiting_confirmation=lambda: None,
        log=lambda msg: None, is_running=lambda: True)
    defaults.update(overrides)
    return Session(**defaults)


# ---------- 4. Activation produces the configured response (pure Session-level) ----------

def test_activation_response_is_configurable():
    session = make_session(activation_response='Custom hello')
    assert session.activation_response == 'Custom hello'


def test_barge_in_response_is_configurable_and_distinct_from_activation():
    session = make_session(activation_response='A', barge_in_response='B')
    assert session.activation_response == 'A'
    assert session.barge_in_response == 'B'
    assert session.activation_response != session.barge_in_response


# ---------- 6/7/8. "stand down": ACTIVE -> STANDBY, no response, no further commands ----------

def test_stand_down_ends_the_session_immediately_with_no_capture_after():
    calls = []

    def fake_capture(timeout=8, allow_offline_on_unclear=True):
        calls.append(1)
        return 'first command'

    executed = []

    def fake_execute(cmd):
        executed.append(cmd)
        session.end_session_now.set()  # simulates the "stand down" capability handler

    session = make_session(capture_command=fake_capture, execute=fake_execute)
    session.active_session()

    assert executed == ['first command']
    assert len(calls) == 1, 'no further capture_command call should happen after stand down'


def test_stand_down_speaks_nothing():
    spoken = []
    session = make_session(
        speak=lambda text: spoken.append(text),
        capture_command=lambda timeout=8, allow_offline_on_unclear=True: 'stand down',
        execute=lambda cmd: session.end_session_now.set())
    session.active_session()
    assert spoken == []


def test_stand_down_returns_to_standby_state():
    session = make_session(
        capture_command=lambda timeout=8, allow_offline_on_unclear=True: 'stand down',
        execute=lambda cmd: session.end_session_now.set())
    session.active_session()
    assert not session.in_active_session.is_set()


# ---------- Vortex-level: real dispatch through the registry ----------

@pytest.fixture
def v():
    from vortex.app import Vortex
    inst = Vortex()
    inst.spoken = []
    inst.speak = lambda text: inst.spoken.append(text)
    yield inst
    inst.memory.close()
    if inst.rag is not None:
        inst.rag.close()


# ---------- 1. Initial state is STANDBY ----------

def test_initial_state_is_standby(v):
    assert v.state == VortexState.STANDBY


# ---------- 3/5. Wake -> ACTIVE_SESSION; commands work while active ----------

def test_state_is_active_session_during_a_session(v):
    assert v.session.in_active_session.is_set() is False
    v.session.in_active_session.set()
    assert v.state == VortexState.ACTIVE_SESSION
    v.session.in_active_session.clear()
    assert v.state == VortexState.STANDBY


# ---------- 10. Personality mode can be changed ----------

def test_personality_mode_can_be_changed_via_voice_command(v):
    assert v.personality_mode == PersonalityMode.PROFESSIONAL
    v.execute('switch to witty mode')
    assert v.personality_mode == PersonalityMode.WITTY
    assert any('Witty' in s for s in v.spoken)


def test_demonstrate_yourself_enters_presentation_mode(v):
    """The literal phrase from the feature spec ("VORTEX to demonstrate
    yourself") must actually work end to end through execute(), not just
    the more mechanical "switch to demo mode" wording. demonstrate_self()
    itself is mocked here (its real content is covered by
    test_self_knowledge.py and a live Ollama verification, not by hitting
    the network in a fast unit test) - this test isolates "does entering
    demo mode trigger the actual demonstration, not just a mode switch."""
    calls = []
    v.demonstrate_self = lambda: calls.append('demonstrated')
    assert v.presentation_mode is False
    v.execute('demonstrate yourself')
    assert v.personality_mode == PersonalityMode.DEMO
    assert v.presentation_mode is True
    assert v.spoken == ['Switched to Demo mode.']
    assert calls == ['demonstrated']


def test_switching_to_a_non_demo_mode_does_not_demonstrate(v):
    calls = []
    v.demonstrate_self = lambda: calls.append('demonstrated')
    v.execute('switch to witty mode')
    assert calls == []


def test_stand_down_dispatches_through_execute_with_no_response(v):
    v.execute('stand down')
    assert v.spoken == []
    assert v.session.end_session_now.is_set()
    v.session.end_session_now.clear()  # cleanup - nothing else consumes it in this test


# ---------- EXECUTING state, set for real work, cleared even on error ----------

def test_executing_state_is_set_during_dispatch_and_cleared_after(v):
    from vortex.core import intent_router as intents

    seen_during = {}

    def fake_speak_time(intent):
        seen_during['state'] = v.state
    v._registry._handlers[intents.SpeakTime] = fake_speak_time

    v.execute('what is the time')
    assert seen_during.get('state') == VortexState.EXECUTING
    assert not v._executing.is_set()


def test_executing_state_cleared_even_if_a_handler_raises(v):
    def boom(intent):
        raise RuntimeError('handler blew up')
    from vortex.core import intent_router as intents
    v._registry._handlers[intents.SpeakTime] = boom

    with pytest.raises(RuntimeError):
        v.execute('what is the time')
    assert not v._executing.is_set()


# ---------- 15. Presentation mode doesn't affect internal diagnostics ----------

def test_presentation_mode_derives_from_demo_personality(v):
    assert v.presentation_mode is False
    v.personality_mode = PersonalityMode.DEMO
    assert v.presentation_mode is True


def test_log_behavior_is_unaffected_by_presentation_mode(v, caplog):
    import logging
    caplog.set_level(logging.INFO)
    v.personality_mode = PersonalityMode.PROFESSIONAL
    v.log('diagnostic message one')
    v.personality_mode = PersonalityMode.DEMO
    v.log('diagnostic message two')
    messages = [r.message for r in caplog.records]
    assert 'diagnostic message one' in messages
    assert 'diagnostic message two' in messages


# ---------- 2/9. Full worker-thread flow: no response to silence, wake works, re-wake after stand down ----------

def test_no_event_means_no_execute_call_no_matter_how_long_idle(v):
    v.commands = []
    v.capture_command = lambda timeout=8, allow_offline_on_unclear=True: None
    executed = []
    v.execute = lambda cmd: executed.append(cmd)
    v.greet = lambda: None

    worker = threading.Thread(target=v._worker, daemon=True)
    worker.start()
    time.sleep(2.0)  # past worker's 1.5s startup delay, well under any session_timeout
    v.stop()
    worker.join(timeout=5)

    assert executed == [], 'execute() must never be called without a real wake/barge-in event'


def test_wake_then_stand_down_then_wake_again_works(v):
    """Full 6/9 scenario end-to-end: wake -> activation response -> "stand
    down" -> silent return to standby -> a second wake still works (only
    the wake mechanism reactivates VORTEX, and it keeps working after a
    stand-down, unlike a full "shutdown vortex" process exit)."""
    v.commands = ['stand down']
    v.capture_command = lambda timeout=8, allow_offline_on_unclear=True: (
        v.commands.pop(0) if v.commands else None)
    v.greet = lambda: None

    worker = threading.Thread(target=v._worker, daemon=True)
    worker.start()
    time.sleep(1.7)
    v.events.put('wake')
    time.sleep(1.0)
    assert v.spoken.count('Yes Boss?') == 1, f'expected exactly one activation response, got {v.spoken}'

    v.events.put('wake')
    time.sleep(1.0)
    v.stop()
    worker.join(timeout=5)

    assert v.spoken.count('Yes Boss?') == 2, f'wake should work again after stand down, got {v.spoken}'
