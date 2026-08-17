"""Pytest port of tools/test_barge_in.py's scenarios, against the extracted
voice/ modules (docs/REFACTOR_PLAN.md Step 3 exit criteria).

No microphone, speakers, or network involved: TTS playback/synthesis and
Vortex's worker-thread dependencies are stubbed exactly as the original
standalone script stubbed them, just retargeted at the objects the logic
actually lives in now (voice.tts.TextToSpeech directly, rather than methods
monkeypatched on Vortex itself).

Chunking/interruption scenarios exercise TextToSpeech directly - the fastest,
least-coupled way to hit the exact algorithm being tested. The worker-dispatch
scenario constructs a real Vortex() (no hardware touched: WakeDetector loads
the ONNX model but never opens an InputStream until .start() is explicitly
called, and BrowserAgent/RagStore both degrade gracefully with no real
browser/DB running) to prove that Session's injected callables - lambdas that
call back through self.speak/self.capture_command/self.execute/self.greet at
call time, not pre-bound references captured once - still pick up
instance-level monkeypatching the way the original single-class design did.
"""
import sys
import threading
import time

import pytest

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.voice.barge_in import BargeIn
from vortex.voice.tts import TextToSpeech, MAX_CHUNK


def make_tts():
    barge_in = BargeIn()
    tts = TextToSpeech(voice='en-US-AvaMultilingualNeural', tts_volume=1.0,
                        barge_in=barge_in, log=lambda msg: None, is_running=lambda: True)
    tts.played = []
    tts._synth = lambda loop, text: text  # pass the text through as the "file"
    tts._unlink = lambda path: None

    def fake_play(path):
        tts.played.append(path)
        # Simulate ~0.4s of audio, polling the stop flag exactly like the real player.
        for _ in range(8):
            if barge_in.stop_speaking.is_set():
                return
            time.sleep(0.05)
    tts._play = fake_play
    return tts


# ---------- chunking ----------

def test_splits_a_stream_into_multiple_chunks():
    tts = make_tts()
    chunks = list(tts._chunk_stream(iter(['Java is a language. ', 'It runs on the JVM. ',
                                          'It is **strongly** typed and verbose.'])))
    assert len(chunks) >= 2


def test_strips_markdown():
    tts = make_tts()
    chunks = list(tts._chunk_stream(iter(['Java is a language. ', 'It runs on the JVM. ',
                                          'It is **strongly** typed and verbose.'])))
    assert all('*' not in c for c in chunks)


def test_loses_no_words():
    tts = make_tts()
    chunks = list(tts._chunk_stream(iter(['Java is a language. ', 'It runs on the JVM. ',
                                          'It is **strongly** typed and verbose.'])))
    assert ' '.join(chunks) == 'Java is a language. It runs on the JVM. It is strongly typed and verbose.'


def test_bounds_unpunctuated_text():
    tts = make_tts()
    long_chunks = list(tts._chunk_stream(iter(['word ' * 300])))
    assert all(len(c) <= MAX_CHUNK for c in long_chunks)


# ---------- interruption ----------

def test_barge_in_stops_playback_quickly_and_reports_interrupted():
    tts = make_tts()
    sentences = [f'This is explanation sentence number {i}, and it keeps going for a while. '
                 for i in range(1, 21)]
    done = {}

    def run():
        done['completed'] = tts.speak_stream(iter(sentences))

    t = threading.Thread(target=run)
    start = time.monotonic()
    t.start()
    while not tts.barge_in.speaking.is_set() and time.monotonic() - start < 10:
        time.sleep(0.01)
    assert tts.barge_in.speaking.is_set(), 'speaking flag was never raised'

    time.sleep(1.0)
    spoken_before = len(tts.played)
    cut_at = time.monotonic()
    tts.barge_in.stop_speaking.set()  # exactly what wake.py's on_audio does on barge-in
    t.join(timeout=10)
    latency = time.monotonic() - cut_at

    assert not t.is_alive()
    assert done.get('completed') is False, 'speak_stream should report interrupted (False)'
    assert 0 < spoken_before < len(sentences), (
        f'expected a partial read, got {spoken_before} of {len(sentences)} chunks played')
    assert len(tts.played) == spoken_before, 'no chunks should play after the cut'
    assert latency < 0.5, f'barge-in took {latency:.3f}s to stop playback, expected < 0.5s'
    assert not tts.barge_in.speaking.is_set()


def test_recovers_for_the_next_command_after_an_interruption():
    tts = make_tts()
    tts.barge_in.stop_speaking.set()
    tts.speak_stream(iter(['discarded']))  # drain the interrupted state, like a real cycle would
    tts.barge_in.stop_speaking.clear()
    tts.played = []
    ok = tts.speak_stream(iter(['Opening Chrome now.']))
    assert ok and tts.played == ['Opening Chrome now.']


# ---------- session-continuation offline-fallback gating ----------

def test_active_session_only_allows_offline_fallback_on_first_capture():
    """Direct test of the actual mechanism behind the 2026-08-18
    hallucination-cascade fix: Session.active_session() must pass
    allow_offline_on_unclear=True to capture_command() only on the first
    call in a session (right after the wake/barge-in acknowledgment) and
    False on every call after that, regardless of how many follow-up
    commands are executed."""
    from vortex.voice.barge_in import BargeIn
    from vortex.voice.session import Session

    barge_in = BargeIn()
    calls = []
    commands = ['first command', 'second command', 'third command']

    def fake_capture_command(timeout=8, allow_offline_on_unclear=True):
        calls.append(allow_offline_on_unclear)
        return commands.pop(0) if commands else None

    session = Session(
        events=None, barge_in=barge_in, session_timeout=8, wake_watchdog_timeout=5,
        capture_command=fake_capture_command, execute=lambda cmd: None,
        speak=lambda text: None, greet=lambda: None, warm_up=lambda: None,
        get_last_audio_at=lambda: 0, recover_wake_stream=lambda: None,
        is_capturing=lambda: False, clear_awaiting_confirmation=lambda: None,
        log=lambda msg: None, is_running=lambda: True)

    session.active_session()

    assert calls == [True, False, False, False], (
        f'expected only the first capture to allow offline fallback, got {calls}')


# ---------- worker dispatch ----------

@pytest.fixture
def vortex_instance():
    """A real Vortex() - no hardware touched at construction or by _worker()
    (WakeDetector.start(), which opens the mic InputStream, is only called
    from Vortex.start(), never from the worker loop)."""
    from vortex.main import Vortex
    v = Vortex()
    yield v
    v.memory.close()
    if v.rag is not None:
        v.rag.close()


def test_barge_in_skips_yes_boss_prompt_and_executes_the_new_command(vortex_instance):
    v = vortex_instance
    v.spoken, v.commands = [], ['open chrome']
    v.speak = lambda text: v.spoken.append(text) or True
    v.capture_command = lambda timeout=8, allow_offline_on_unclear=True: (
        v.commands.pop(0) if v.commands else None)
    v.execute = lambda cmd: v.spoken.append(f'EXEC:{cmd}')
    v.greet = lambda: None

    worker = threading.Thread(target=v._worker, daemon=True)
    worker.start()
    time.sleep(1.7)
    v.speaking.set()  # pretend a long answer is on air
    v.events.put('barge_in')
    time.sleep(1.0)
    v.stop()
    worker.join(timeout=5)

    assert 'Yes Boss?' not in v.spoken
    assert 'EXEC:open chrome' in v.spoken
