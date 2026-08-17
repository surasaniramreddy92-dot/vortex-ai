"""Unit tests for src/vortex/voice/stt.py's offline (faster-whisper) fallback.

No microphone or real network involved: _record_command (the sounddevice
capture loop) and the recognizer/offline model are stubbed exactly like
test_barge_in.py stubs TTS synthesis/playback - what's under test here is the
*routing* logic (which exception sends capture_command to the offline path,
which doesn't), not the audio hardware or the models themselves. The offline
model was separately exercised for real (faster-whisper against a real
edge-tts-synthesized clip) outside this fast/hermetic pytest suite - see this
change's report / IMPLEMENTED.md.

Run with: pytest tests/unit/test_stt.py
"""
import sys
import threading
from unittest.mock import Mock

import pytest
import speech_recognition as sr

# Same fix test_barge_in.py already needed: an editable install of vortex-ai
# resolves `import vortex` to wherever `pip install -e .` was originally run
# (the main checkout), not to this worktree's own src/ - without this, tests
# run from a worktree silently exercise the wrong copy of the code.
sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.voice.stt import SpeechToText


def make_stt(**overrides):
    kwargs = dict(
        recognizer=Mock(),
        capturing=threading.Event(),
        agc_target_rms=3500,
        stop_wake_stream=lambda: None,
        recover_wake_stream=lambda: None,
        log=lambda *a, **k: None,
    )
    kwargs.update(overrides)
    stt = SpeechToText(**kwargs)
    # Stand in for the real sounddevice capture loop - returns some non-empty
    # raw int16 PCM bytes so capture_command doesn't short-circuit on
    # WaitTimeoutError before ever reaching recognize_google.
    stt._record_command = lambda timeout, phrase_time_limit, samplerate=16000: b'\x00\x01' * 8000
    return stt


def test_cloud_success_never_touches_offline_path():
    """Matches existing behavior exactly when online: recognize_google succeeds,
    the offline path is never even consulted."""
    stt = make_stt()
    stt.recognizer.recognize_google.return_value = 'turn on the lights'
    stt._recognize_offline = Mock(return_value='should not be used')

    result = stt.capture_command()

    assert result == 'turn on the lights'
    stt._recognize_offline.assert_not_called()


def test_request_error_falls_back_to_offline():
    """sr.RequestError means Google couldn't be reached at all - the one case
    the offline fallback exists for."""
    stt = make_stt()
    stt.recognizer.recognize_google.side_effect = sr.RequestError('no internet connection')
    stt._recognize_offline = Mock(return_value='turn off the lights')

    result = stt.capture_command()

    assert result == 'turn off the lights'
    stt._recognize_offline.assert_called_once()


def test_unknown_value_error_does_not_fall_back_to_offline():
    """sr.UnknownValueError means Google WAS reached and understood the attempt
    fine - the audio itself was unclear. Falling back to a smaller, less
    accurate local model in that case would be a downgrade, not a fallback,
    so this must NOT call the offline path, exactly like before this fallback
    existed."""
    stt = make_stt()
    stt.recognizer.recognize_google.side_effect = sr.UnknownValueError()
    stt._recognize_offline = Mock(return_value='should never be reached')

    result = stt.capture_command()

    assert result is None
    stt._recognize_offline.assert_not_called()


def test_offline_fallback_returning_nothing_is_reported_as_no_capture():
    """If the offline model is unavailable/unclear too, capture_command should
    still return None cleanly (not raise) - same contract as a normal miss."""
    stt = make_stt()
    stt.recognizer.recognize_google.side_effect = sr.RequestError('no internet connection')
    stt._recognize_offline = Mock(return_value=None)

    assert stt.capture_command() is None


def test_offline_disabled_get_offline_model_returns_none():
    """The kill switch (offline_enabled=False, matching VORTEX_OFFLINE_FALLBACK_ENABLED)
    must mean the offline model is never even loaded, regardless of what
    recognize_google raises."""
    stt = make_stt(offline_enabled=False)
    assert stt._get_offline_model() is None


def test_get_offline_model_is_a_lazy_singleton(monkeypatch):
    """The model should load at most once per process - not once per
    capture_command() call, since loading it was measured to cost real
    seconds (see IMPLEMENTED.md); paying that on every fallback would make
    the fallback itself unusably slow."""
    fake_model = Mock()
    fake_ctor = Mock(return_value=fake_model)
    import faster_whisper
    monkeypatch.setattr(faster_whisper, 'WhisperModel', fake_ctor)

    stt = make_stt()
    first = stt._get_offline_model()
    second = stt._get_offline_model()

    assert first is fake_model
    assert second is fake_model
    fake_ctor.assert_called_once()


def test_get_offline_model_unavailable_degrades_gracefully(monkeypatch):
    """If faster-whisper can't load (not installed, no cached model and no
    network to fetch one, corrupt cache, ...) this must log and return None,
    not raise - same "probe and degrade" contract as documents.py's
    _ocr_available."""
    import faster_whisper

    def boom(*a, **k):
        raise RuntimeError('model files not found')
    monkeypatch.setattr(faster_whisper, 'WhisperModel', boom)

    logged = []
    stt = make_stt(log=logged.append)

    assert stt._get_offline_model() is None
    assert any('Offline STT unavailable' in line for line in logged)


def test_recognize_offline_joins_and_normalizes_segments():
    stt = make_stt()
    seg1, seg2 = Mock(text=' Turn off '), Mock(text='the lights.')
    fake_model = Mock()
    fake_model.transcribe.return_value = ([seg1, seg2], Mock())
    stt._get_offline_model = lambda: fake_model

    audio = sr.AudioData(b'\x00\x01' * 8000, 16000, 2)
    text = stt._recognize_offline(audio)

    assert text == 'turn off  the lights.'
    fake_model.transcribe.assert_called_once()
    # Passed a float32 waveform, not the raw bytes/AudioData - faster-whisper's
    # ndarray input contract.
    called_audio = fake_model.transcribe.call_args.args[0]
    assert called_audio.dtype.name == 'float32'


def test_recognize_offline_returns_none_when_model_unavailable():
    stt = make_stt()
    stt._get_offline_model = lambda: None
    audio = sr.AudioData(b'\x00\x01' * 8000, 16000, 2)
    assert stt._recognize_offline(audio) is None
