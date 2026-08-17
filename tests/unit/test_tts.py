"""Unit tests for src/vortex/voice/tts.py's offline (piper-tts) fallback.

No speakers or real network involved: edge_tts.Communicate is replaced with a
fake whose .save() raises whatever exception the scenario needs, exactly the
same "stub the network call, test the routing" approach test_stt.py takes for
STT. What's under test here is which exception sends _synth to the offline
path and which doesn't - the offline engine itself was separately exercised
for real (piper-tts rendering a real, playable wav) outside this
fast/hermetic pytest suite - see this change's report / IMPLEMENTED.md.

Run with: pytest tests/unit/test_tts.py
"""
import asyncio
import sys
from unittest.mock import Mock

import aiohttp
import edge_tts.exceptions
import pytest

# Same fix test_barge_in.py already needed: an editable install of vortex-ai
# resolves `import vortex` to wherever `pip install -e .` was originally run
# (the main checkout), not to this worktree's own src/ - without this, tests
# run from a worktree silently exercise the wrong copy of the code.
sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.voice import tts as tts_module
from vortex.voice.barge_in import BargeIn
from vortex.voice.tts import TextToSpeech


def make_tts(**overrides):
    kwargs = dict(
        voice='en-US-AvaMultilingualNeural', tts_volume=1.0, barge_in=BargeIn(),
        log=lambda *a, **k: None, is_running=lambda: True,
    )
    kwargs.update(overrides)
    return TextToSpeech(**kwargs)


def fake_communicate_raising(exc):
    class FakeCommunicate:
        def __init__(self, text, voice):
            pass

        async def save(self, path):
            raise exc
    return FakeCommunicate


def test_cloud_success_never_touches_offline_path(monkeypatch, tmp_path):
    """Matches existing behavior exactly when online."""
    class FakeCommunicate:
        def __init__(self, text, voice):
            pass

        async def save(self, path):
            open(path, 'wb').close()
    monkeypatch.setattr(tts_module.edge_tts, 'Communicate', FakeCommunicate)

    tts = make_tts()
    tts._synth_offline = Mock(return_value='should not be used')
    loop = asyncio.new_event_loop()
    try:
        path = tts._synth(loop, 'hello there')
    finally:
        loop.close()

    assert path is not None
    tts._synth_offline.assert_not_called()


def test_network_error_falls_back_to_offline(monkeypatch):
    """aiohttp.ClientConnectionError means edge-tts's server couldn't be
    reached at all - the one case the offline fallback exists for."""
    monkeypatch.setattr(
        tts_module.edge_tts, 'Communicate',
        fake_communicate_raising(aiohttp.ClientConnectorError(Mock(), OSError('no route'))))

    tts = make_tts()
    tts._synth_offline = Mock(return_value='/tmp/offline.wav')
    loop = asyncio.new_event_loop()
    try:
        path = tts._synth(loop, 'hello there')
    finally:
        loop.close()

    assert path == '/tmp/offline.wav'
    tts._synth_offline.assert_called_once_with('hello there')


def test_timeout_error_falls_back_to_offline(monkeypatch):
    monkeypatch.setattr(
        tts_module.edge_tts, 'Communicate', fake_communicate_raising(asyncio.TimeoutError()))

    tts = make_tts()
    tts._synth_offline = Mock(return_value='/tmp/offline.wav')
    loop = asyncio.new_event_loop()
    try:
        path = tts._synth(loop, 'hello there')
    finally:
        loop.close()

    assert path == '/tmp/offline.wav'
    tts._synth_offline.assert_called_once()


def test_edge_tts_service_error_does_not_fall_back_to_offline(monkeypatch):
    """edge_tts's own EdgeTTSException family means the service WAS reached and
    returned something odd - a different, retriable problem, not a
    network-down problem. Falling back to a worse local voice for that would
    be a downgrade, not a fallback, so this must NOT call the offline path,
    exactly like before this fallback existed (falls through to the generic
    'TTS synth error' path and returns None)."""
    monkeypatch.setattr(
        tts_module.edge_tts, 'Communicate',
        fake_communicate_raising(edge_tts.exceptions.NoAudioReceived('no audio')))

    tts = make_tts()
    tts._synth_offline = Mock(return_value='should never be reached')
    loop = asyncio.new_event_loop()
    try:
        path = tts._synth(loop, 'hello there')
    finally:
        loop.close()

    assert path is None
    tts._synth_offline.assert_not_called()


def test_offline_disabled_get_offline_voice_returns_none():
    tts = make_tts(offline_enabled=False)
    assert tts._get_offline_voice() is None


def test_get_offline_voice_is_a_lazy_singleton(monkeypatch, tmp_path):
    """The piper voice should load at most once per process, not once per
    chunk - loading it was measured to cost real seconds (see
    IMPLEMENTED.md); paying that per chunk would defeat _speak_chunks' whole
    synthesize-ahead-while-playing design."""
    model_dir = tmp_path
    voice_file = model_dir / 'en_US-lessac-medium.onnx'
    voice_file.write_bytes(b'not a real model, just needs to exist')

    fake_voice = Mock()
    fake_load = Mock(return_value=fake_voice)
    import piper
    monkeypatch.setattr(piper.PiperVoice, 'load', fake_load)

    tts = make_tts(offline_model_dir=str(model_dir))
    first = tts._get_offline_voice()
    second = tts._get_offline_voice()

    assert first is fake_voice
    assert second is fake_voice
    fake_load.assert_called_once()


def test_get_offline_voice_missing_file_degrades_gracefully(tmp_path):
    """Voice not downloaded/cached yet and (implicitly) no network to fetch it -
    must log and return None, not raise."""
    logged = []
    tts = make_tts(offline_model_dir=str(tmp_path), log=logged.append)

    assert tts._get_offline_voice() is None
    assert any('Offline TTS unavailable' in line for line in logged)


def test_synth_offline_writes_a_wav_and_returns_its_path(tmp_path):
    tts = make_tts()

    def fake_synthesize_wav(text, wav_file):
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b'\x00\x00' * 100)

    fake_voice = Mock()
    fake_voice.synthesize_wav.side_effect = fake_synthesize_wav
    tts._get_offline_voice = lambda: fake_voice

    path = tts._synth_offline('hello there')

    assert path is not None
    import os
    assert os.path.exists(path)
    assert os.path.getsize(path) > 0
    os.remove(path)
