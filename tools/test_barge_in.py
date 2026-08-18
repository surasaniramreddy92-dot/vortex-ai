"""Headless checks for VORTEX chunking and mid-speech interruption.

Runs without a microphone or speakers: playback is stubbed with a sleep, and
synthesis is stubbed unless --live is passed (which exercises real edge-tts).

Updated for docs/REFACTOR_PLAN.md Step 3: chunking/synthesis/playback now
live on Vortex.tts (voice/tts.py) instead of directly on Vortex, so those
three monkeypatches target v.tts.* instead of v.* directly. speak_stream,
speaking/stop_speaking, _worker, events, and stop() are unchanged - Vortex
still exposes all of those (as thin passthroughs/properties over the
extracted voice/ modules), so everything else below is untouched. See also
tests/unit/test_barge_in.py for the pytest-discoverable port of these same
scenarios against the new module structure directly.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from vortex.app import Vortex
from vortex.voice.tts import MAX_CHUNK

LIVE = '--live' in sys.argv
failures = []


def check(name, cond, detail=''):
    print(f'{"PASS" if cond else "FAIL"}  {name}{"  -> " + detail if detail else ""}')
    if not cond:
        failures.append(name)


def make_vortex():
    v = Vortex()
    v.played = []
    if not LIVE:
        v.tts._synth = lambda loop, text: text          # pass the text through as the "file"
        v.tts._unlink = lambda path: None

    def fake_play(path):
        v.played.append(path)
        # Simulate ~0.4s of audio, polling the stop flag exactly like the real player.
        for _ in range(8):
            if v.stop_speaking.is_set() or not v.running:
                return
            time.sleep(0.05)
    v.tts._play = fake_play
    return v


print('--- chunking ---')
v = make_vortex()
chunks = list(v.tts._chunk_stream(iter(['Java is a language. ', 'It runs on the JVM. ',
                                        'It is **strongly** typed and verbose.'])))
check('splits a stream into multiple chunks', len(chunks) >= 2, repr(chunks))
check('strips markdown', all('*' not in c for c in chunks), repr(chunks))
check('loses no words', ' '.join(chunks) ==
      'Java is a language. It runs on the JVM. It is strongly typed and verbose.',
      repr(' '.join(chunks)))

v = make_vortex()
long_chunks = list(v.tts._chunk_stream(iter(['word ' * 300])))
check('bounds unpunctuated text', all(len(c) <= MAX_CHUNK for c in long_chunks),
      f'max={max(len(c) for c in long_chunks)}')

print('\n--- interruption ---')
v = make_vortex()
sentences = [f'This is explanation sentence number {i}, and it keeps going for a while. '
             for i in range(1, 21)]
done = {}


def run():
    done['completed'] = v.speak_stream(iter(sentences))


t = threading.Thread(target=run)
start = time.monotonic()
t.start()
while not v.speaking.is_set() and time.monotonic() - start < 10:
    time.sleep(0.01)
check('speaking flag raised', v.speaking.is_set())

time.sleep(1.0)
spoken_before = len(v.played)
cut_at = time.monotonic()
v.stop_speaking.set()          # exactly what the audio callback does on barge-in
t.join(timeout=10)
latency = time.monotonic() - cut_at

check('speak_stream returned', not t.is_alive())
check('reported as interrupted', done.get('completed') is False, repr(done))
check('stopped mid-explanation', 0 < spoken_before < len(sentences),
      f'{spoken_before} of {len(sentences)} chunks played before the cut')
check('no chunks played after the cut', len(v.played) == spoken_before,
      f'{len(v.played)} total')
check('stops in under 0.5s', latency < 0.5, f'{latency:.3f}s')
check('speaking flag cleared', not v.speaking.is_set())

print('\n--- recovers for the next command ---')
v.stop_speaking.clear()
v.played = []
ok = v.speak_stream(iter(['Opening Chrome now.']))
check('speaks again after an interruption', ok and len(v.played) == 1, f'played={v.played}')

print('\n--- worker dispatch ---')
v = make_vortex()
v.spoken, v.commands = [], ['open chrome']
v.speak = lambda text: v.spoken.append(text) or True
v.capture_command = lambda timeout=8: v.commands.pop(0) if v.commands else None
v.execute = lambda cmd: v.spoken.append(f'EXEC:{cmd}')
v.greet = lambda: None
worker = threading.Thread(target=v._worker, daemon=True)
worker.start()
time.sleep(1.7)
v.speaking.set()               # pretend a long answer is on air
v.events.put('barge_in')
time.sleep(1.0)
v.stop()
check('barge-in skips the "Yes Boss?" prompt', 'Yes Boss?' not in v.spoken, repr(v.spoken))
check('barge-in executes the new command', 'EXEC:open chrome' in v.spoken, repr(v.spoken))

print('\n' + ('ALL CHECKS PASSED' if not failures else f'FAILURES: {failures}'))
sys.exit(1 if failures else 0)
