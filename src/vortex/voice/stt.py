"""Command capture via sounddevice + energy-based VAD, handed off to Google
Web Speech for transcription.

Extracted verbatim from Vortex._own_mic, capture_command, _record_command,
_boost_audio_data (docs/REFACTOR_PLAN.md Step 3). This is the sounddevice-
based capture path that replaced sr.Microphone()/PyAudio on 2026-08-16 (see
CHANGELOG.md, fourth pass) because the old path consistently failed to
transcribe real commands even while the wake word fired reliably on the same
mic - preserved here exactly, not reverted.
"""
import collections
import contextlib
import time

import numpy as np
import sounddevice as sd
import speech_recognition as sr


class SpeechToText:
    """Judgment call: no separate SpeechToText interface/adapter split
    introduced here either, for the same reason as tts.py - one concrete
    implementation today, today's capture-path fix is specific to it, and
    the interface can wrap this later without disturbing the fix.

    stop_wake_stream/recover_wake_stream are injected callables (bound to
    wake.py's WakeDetector at composition time in main.py) rather than a
    direct import of wake.py, so this module doesn't need to know anything
    about WakeDetector's internals - just "stand the wake stream down" and
    "bring it back up".
    """

    def __init__(self, *, recognizer, capturing, agc_target_rms,
                 stop_wake_stream, recover_wake_stream, log):
        self.recognizer = recognizer
        self.capturing = capturing
        self.agc_target_rms = agc_target_rms
        self.stop_wake_stream = stop_wake_stream
        self.recover_wake_stream = recover_wake_stream
        self.log = log

    @contextlib.contextmanager
    def _own_mic(self):
        """Hand the mic to SpeechRecognition; the wake stream stands down meanwhile."""
        self.capturing.set()
        self.stop_wake_stream()
        try:
            yield
        finally:
            self.recover_wake_stream()
            self.capturing.clear()

    def _boost_audio_data(self, audio):
        """Unlike the wake stream (boosted by AudioProcessor), command audio captured via
        sr.Microphone() went straight to Google's STT completely unboosted - a
        real asymmetry: the wake word could fire fine (boosted signal) while the
        actual follow-up command consistently failed to transcribe (raw signal),
        which is exactly what full-volume "Hey Vortex" working while nothing
        after it ever got heard looks like. Boost toward the same target RMS
        used for wake detection before handing audio to recognize_google."""
        if audio.sample_width != 2:
            return audio
        samples = np.frombuffer(audio.get_raw_data(), dtype=np.int16)
        if len(samples) == 0:
            return audio
        rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
        if rms < 40:  # near-total silence - nothing meaningful to boost
            return audio
        gain = min(6.0, self.agc_target_rms / rms)
        if gain <= 1.0:
            return audio
        boosted = np.clip(samples.astype(np.float64) * gain, -32768, 32767).astype(np.int16)
        return sr.AudioData(boosted.tobytes(), audio.sample_rate, audio.sample_width)

    def _record_command(self, timeout, phrase_time_limit, samplerate=16000):
        """Record one command via sounddevice - the same audio path the wake
        detector uses - instead of a separate PyAudio-based sr.Microphone().

        Evidence this split path was the real problem, not VAD timing: across
        many real captures on 2026-08-16, sr.Microphone() raw RMS was mostly
        40-1000 and rarely transcribed, for the *same* physical mic, user, and
        moment where the wake stream was reliably scoring strong signal. A
        standalone probe confirmed this capture path itself is sound (max RMS
        8081 against a real spoken phrase, played and captured independently
        of the app).

        Energy-based VAD, calibrated fresh against a brief ambient sample each
        call. Requires ONSET_FRAMES consecutive frames above threshold before
        committing to "speech started" (a first cut without this required only
        one frame, and a single noise blip - a click, a breath - was enough to
        trigger recording, then time out on silence before real speech even
        began, producing a short, near-silent clip exactly like the ones that
        kept failing). A short pre-roll buffer is prepended so the committed
        onset doesn't clip the first syllable."""
        frame_len = max(1, samplerate // 33)  # ~30ms frames
        onset_frames_needed = 4  # ~120ms of sustained energy before committing
        silence_frames_needed = max(1, int(0.8 * samplerate / frame_len))
        calib_frames_needed = max(1, int(0.3 * samplerate / frame_len))
        preroll_len = onset_frames_needed + 2
        with sd.InputStream(channels=1, samplerate=samplerate, dtype='int16', blocksize=frame_len) as stream:
            calib = []
            while len(calib) < calib_frames_needed:
                data, _ = stream.read(frame_len)
                calib.append(np.sqrt(np.mean(data.astype(np.float64) ** 2)))
            # Capped at both ends: a floor of 60 so near-zero ambient doesn't make
            # the threshold pick up a breath, and a ceiling of 800 so calibration
            # landing on a loud moment (e.g. echo/reverb right after "Yes Boss?"
            # finishes) can't push the bar above what real speech reaches - live
            # evidence this happened: one real capture calibrated to 1947 purely
            # from ambient noise, well above where normal speech (and every
            # successful capture logged so far) actually sits.
            energy_threshold = min(max((float(np.median(calib)) if calib else 0.0) * 2.5, 60.0), 800.0)
            # Diagnostic only (temporary): logs the calibrated threshold and the
            # loudest frame actually seen before giving up, so a real timeout
            # (nothing ever got loud) can be told apart from a miscalibrated
            # threshold (something did get loud, just never 4 frames running).
            max_rms_seen = 0.0
            self.log(f'[diag] capture calib energy_threshold={energy_threshold:.0f}')

            preroll = collections.deque(maxlen=preroll_len)
            frames = []
            speaking = False
            onset_run = 0
            silence_run = 0
            start = time.monotonic()
            while True:
                data, _ = stream.read(frame_len)
                rms = np.sqrt(np.mean(data.astype(np.float64) ** 2))
                loud = rms > energy_threshold
                if not speaking:
                    max_rms_seen = max(max_rms_seen, rms)
                    preroll.append(data.copy())
                    onset_run = onset_run + 1 if loud else 0
                    if onset_run >= onset_frames_needed:
                        speaking = True
                        silence_run = 0
                        frames.extend(preroll)
                        preroll.clear()
                    elif time.monotonic() - start > timeout:
                        self.log(f'[diag] capture timeout: energy_threshold={energy_threshold:.0f} max_rms_seen={max_rms_seen:.0f}')
                        return None
                else:
                    frames.append(data.copy())
                    silence_run = 0 if loud else silence_run + 1
                    if silence_run >= silence_frames_needed:
                        break
                    if (len(frames) * frame_len / samplerate) > phrase_time_limit:
                        break
        if not frames:
            return None
        return np.concatenate(frames, axis=0).flatten().astype(np.int16).tobytes()

    def capture_command(self, timeout=8):
        try:
            with self._own_mic():
                raw_bytes = self._record_command(timeout=timeout, phrase_time_limit=8)
            if raw_bytes is None:
                raise sr.WaitTimeoutError('listening timed out while waiting for phrase to start')
            audio = sr.AudioData(raw_bytes, 16000, 2)
            # Diagnostic only (temporary - remove once real-world reliability is
            # confirmed on this new capture path): logs what was actually
            # captured even when recognize_google() can't transcribe it, so a
            # near-silent/garbled buffer can be told apart from a buffer with
            # real signal that Google just couldn't parse.
            raw = np.frombuffer(audio.get_raw_data(), dtype=np.int16)
            raw_rms = np.sqrt(np.mean(raw.astype(np.float64) ** 2)) if len(raw) else 0.0
            duration = len(raw) / audio.sample_rate if audio.sample_rate else 0.0
            audio = self._boost_audio_data(audio)
            self.log(f'[diag] captured duration={duration:.2f}s raw_rms={raw_rms:.0f}')
            cmd = self.recognizer.recognize_google(audio).lower().strip()
            self.log(f'Heard: {cmd}')
            return cmd
        except Exception as e:
            # type(e).__name__ matters: sr.UnknownValueError's str() is empty, which
            # is exactly why every past "Capture error:" line here showed nothing.
            self.log(f'Capture error: {type(e).__name__}: {e}')
            return None
