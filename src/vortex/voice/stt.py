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
import os
import time

import numpy as np
import sounddevice as sd
import speech_recognition as sr

# Sentinel distinguishing "never tried to load" from "tried and it's
# unavailable" (None) - a plain None default would re-attempt the (possibly
# slow, possibly failing) load on every single capture_command call once it
# had already failed once.
_UNSET = object()


class SpeechToText:
    """Judgment call: no separate SpeechToText interface/adapter split
    introduced here either, for the same reason as tts.py - one concrete
    implementation today, today's capture-path fix is specific to it, and
    the interface can wrap this later without disturbing the fix.

    stop_wake_stream/recover_wake_stream are injected callables (bound to
    wake.py's WakeDetector at composition time in app.py) rather than a
    direct import of wake.py, so this module doesn't need to know anything
    about WakeDetector's internals - just "stand the wake stream down" and
    "bring it back up".

    Offline STT fallback (faster-whisper), added 2026-08-16 - see
    config.py's offline_fallback_enabled docstring for the reasoning and
    IMPLEMENTED.md's Phase 1 row for what's actually verified. Kept as a
    fallback *inside* this one concrete class rather than a second
    SpeechToText implementation behind an interface, for the same reason the
    class-level docstring above gives for not splitting an interface out yet:
    one concrete capture path today, this fix is specific to it.
    """

    def __init__(self, *, recognizer, capturing, agc_target_rms,
                 stop_wake_stream, recover_wake_stream, log,
                 offline_enabled=True, offline_model_size='base.en',
                 offline_model_dir=None, offline_compute_type='int8'):
        self.recognizer = recognizer
        self.capturing = capturing
        self.agc_target_rms = agc_target_rms
        self.stop_wake_stream = stop_wake_stream
        self.recover_wake_stream = recover_wake_stream
        self.log = log
        self.offline_enabled = offline_enabled
        self.offline_model_size = offline_model_size
        self.offline_model_dir = offline_model_dir
        self.offline_compute_type = offline_compute_type
        self._offline_model = _UNSET  # lazy singleton - see _get_offline_model

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

    def _get_offline_model(self):
        """Lazy-loaded faster-whisper singleton - loaded at most once per process,
        not once per capture_command() call (model load was measured at 1-4s
        warm / 30-40s on first-ever download, see IMPLEMENTED.md - paying that
        on every fallback would make the fallback itself unusably slow)."""
        if not self.offline_enabled:
            return None
        if self._offline_model is _UNSET:
            try:
                from faster_whisper import WhisperModel
                self._offline_model = WhisperModel(
                    self.offline_model_size, device='cpu',
                    compute_type=self.offline_compute_type,
                    download_root=self.offline_model_dir)
                self.log(f'Offline STT model ready: {self.offline_model_size}')
            except Exception as e:
                # Not installed, no cached model and no network to fetch one,
                # corrupt cache, etc - all degrade to "offline unavailable"
                # rather than raising, same as documents.py's _ocr_available.
                self.log(f'Offline STT unavailable: {type(e).__name__}: {e}')
                self._offline_model = None
        return self._offline_model

    def ensure_offline_ready(self):
        """Explicit warm-up hook (called from app.py's _warm_up_models, off the
        critical path, while the network is presumably still up) so the model
        is downloaded and cached *before* it's ever actually needed - if the
        first attempt to load it happened during capture_command's fallback,
        that would be exactly the moment the network is down, and a fresh
        download would fail too."""
        self._get_offline_model()

    def _recognize_offline(self, audio):
        """Fallback transcription via faster-whisper - local, no network.
        Called from capture_command on both a real network failure and a
        cloud UnknownValueError (see capture_command's docstring for the
        2026-08-17 evidence behind trying it on both).

        Filters segments by no_speech_prob (Whisper's own estimate that a
        segment contains no real speech at all) before joining them - added
        2026-08-17 after live evidence Whisper-family models can hallucinate
        fluent-sounding but entirely fabricated text on quiet/unclear audio
        (observed live: "thanks for watching, and i'll see you in the next
        video" from audio that was mostly near-silence - a well-documented
        Whisper failure mode, not a one-off). Without this filter, a
        hallucinated segment would be indistinguishable from a real
        transcription and get spoken to the LLM as if the user had actually
        said it. 0.6 is a conservative cutoff (segments Whisper itself is
        more likely than not to consider silence get dropped), not tuned
        against a labeled dataset - a starting point, not a validated
        threshold."""
        model = self._get_offline_model()
        if model is None:
            return None
        # faster-whisper accepts a float32 mono waveform directly (no need to
        # round-trip through a file, or through av's ffmpeg-based decoder,
        # since this is already raw 16kHz PCM straight from _record_command).
        samples = np.frombuffer(audio.get_raw_data(), dtype=np.int16).astype(np.float32) / 32768.0
        try:
            segments, _info = model.transcribe(samples, language='en')
            kept = [seg.text for seg in segments if seg.no_speech_prob < 0.6]
            text = ' '.join(kept).strip().lower()
            return text or None
        except Exception as e:
            self.log(f'Offline STT transcription error: {type(e).__name__}: {e}')
            return None

    def capture_command(self, timeout=8, allow_offline_on_unclear=True):
        """allow_offline_on_unclear gates ONLY the UnknownValueError path
        below (a real network failure - sr.RequestError - always tries
        offline regardless of this flag; that part is unaffected and still
        safe in any context).

        Found live 2026-08-17, hours after the UnknownValueError fallback
        above was added and initially looked like a clear win: during
        session-continuation listening (_active_session's loop, following up
        after a fresh wake without requiring "Hey Vortex" again), *any*
        ambient sound Google couldn't parse - room noise, a TV, adjacent
        conversation - now also got a "second opinion" from the offline
        model, which (a well-documented Whisper-family behavior, not unique
        to this integration) can fabricate a plausible-sounding command from
        audio that was never actually speech directed at VORTEX. Because
        executing *any* transcription - even a hallucinated one - keeps the
        active session alive for another session_timeout window, this
        cascaded: one fabricated "command" produced a spoken response, which
        reset the follow-up listening window, which captured more ambient
        noise, which fabricated another "command," repeating until 18s of
        genuine silence finally occurred. Observed live: a long run of
        completely unprompted responses ("Did you know jellyfish are
        immortal?", "It was a pleasure assisting you.", ...) with no real
        user input anywhere in between.

        Fix: the offline "second opinion" is only trustworthy immediately
        after a deliberate, intentional signal - saying "Hey Vortex" - not
        during passive follow-up listening, where the prior odds that any
        given sound is actually a command are much lower. voice/session.py's
        active_session() passes allow_offline_on_unclear=True only for the
        first capture_command() call in a session (right after the wake/
        barge-in acknowledgment), False for every continuation call after
        that."""
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
            try:
                cmd = self.recognizer.recognize_google(audio).lower().strip()
                self.log(f'Heard: {cmd}')
                return cmd
            except sr.RequestError as e:
                # Real network failure - always worth an offline attempt,
                # regardless of session state; see capture_command's own
                # docstring for why this is unaffected by the 2026-08-17 fix.
                self.log(f'Cloud STT failed ({type(e).__name__}: {e}); trying offline fallback')
                cmd = self._recognize_offline(audio)
                if cmd:
                    self.log(f'Heard (offline): {cmd}')
                    return cmd
                self.log('Offline STT fallback unavailable or produced nothing')
                return None
            except sr.UnknownValueError as e:
                if not allow_offline_on_unclear:
                    # Session-continuation capture - see this method's
                    # docstring for the hallucination-cascade this avoids.
                    self._save_debug_capture(audio)
                    self.log('Cloud STT failed (UnknownValueError) during session '
                             'continuation - not trying offline (see 2026-08-17 fix)')
                    return None
                self.log(f'Cloud STT failed ({type(e).__name__}: {e}); trying offline fallback')
                cmd = self._recognize_offline(audio)
                if cmd:
                    self.log(f'Heard (offline): {cmd}')
                    return cmd
                self._save_debug_capture(audio)
                self.log('Offline STT fallback unavailable or produced nothing')
                return None
        except Exception as e:
            # type(e).__name__ matters: sr.UnknownValueError's str() is empty, which
            # is exactly why every past "Capture error:" line here showed nothing.
            self.log(f'Capture error: {type(e).__name__}: {e}')
            return None

    _debug_capture_index = 0

    def _save_debug_capture(self, audio):
        """Writes the boosted audio actually sent to recognize_google() to
        logs/debug_captures/ (created if needed), rotating through a small
        fixed set of filenames so this never grows unbounded. Best-effort -
        a failure here must never break capture_command() itself."""
        try:
            import wave
            debug_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), '..', 'logs', 'debug_captures')
            debug_dir = os.path.normpath(debug_dir)
            os.makedirs(debug_dir, exist_ok=True)
            self.__class__._debug_capture_index = (self.__class__._debug_capture_index + 1) % 5
            path = os.path.join(debug_dir, f'capture_{self.__class__._debug_capture_index}.wav')
            with wave.open(path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(audio.sample_width)
                wf.setframerate(audio.sample_rate)
                wf.writeframes(audio.get_raw_data())
            self.log(f'[diag] saved failed capture to {path}')
        except Exception as e:
            self.log(f'[diag] failed to save debug capture: {e}')
