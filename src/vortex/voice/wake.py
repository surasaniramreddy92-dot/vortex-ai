"""Wake-word detection: model loading/inference, threshold logic, and the
sd.InputStream that drives it.

Extracted from Vortex._on_audio, _open_wake_stream, _recover_wake_stream
(docs/REFACTOR_PLAN.md Step 3) - identical control flow and thresholds,
verified live on 2026-08-16 (see CHANGELOG.md), just moved off the Vortex
god-object.

Judgment call (documented per the parent task's instructions): the plan's
original Step 3 table doesn't explicitly place _open_wake_stream /
_recover_wake_stream in any one file, and ARCHITECTURE.md's one-line
description of audio.py says "raw stream handling" while wake.py's says only
"wake-model loading/inference/threshold logic". This module keeps the
InputStream lifecycle here instead of in audio.py: wake_model.reset() must
happen at exactly the moment the stream is rebuilt, and the stream's
constructor needs this class's own on_audio bound method as its callback -
splitting stream lifecycle from the model whose state it's coupled to risked
more coordination bugs than it was worth for a mechanical, zero-logic-change
extraction. audio.py stays focused purely on AGC math, matching the
"audio.py (AGC/noise-floor)" split the parent task asked for.
"""
import contextlib
import time

import numpy as np
import sounddevice as sd
from openwakeword.model import Model


class WakeDetector:
    """Owns the openwakeword model and the wake-listening InputStream, and
    decides wake vs. barge-in vs. silence on every audio callback.

    stop_stream()/recover_stream() are also called by stt.py's mic-handoff
    (_own_mic) - the wake stream must stand down while SpeechRecognition/the
    sounddevice command capture owns the mic, then come back up afterward.
    """

    def __init__(self, *, wake_word_path, wake_threshold, barge_in_threshold,
                 wake_cooldown, audio_processor, barge_in, capturing, events,
                 log, is_running):
        self.wake_model = Model(wakeword_models=[wake_word_path], inference_framework='onnx')
        self.wake_threshold = wake_threshold
        self.barge_in_threshold = barge_in_threshold
        self.wake_cooldown = wake_cooldown
        self.audio_processor = audio_processor
        self.barge_in = barge_in
        self.capturing = capturing
        self.events = events
        self.log = log
        self.is_running = is_running

        self.stream = None
        self.last_wake = 0.0
        self.last_audio_at = time.monotonic()

    # ---------- stream lifecycle ----------

    def _open_stream(self):
        return sd.InputStream(channels=1, samplerate=16000, dtype='float32',
                               blocksize=1280, callback=self.on_audio)

    def start(self):
        self.stream = self._open_stream()
        self.stream.start()

    def stop_stream(self):
        """Close and drop the InputStream (used by stt.py's mic handoff).
        Closes and recreates rather than stop()/start() on the same instance
        on purpose - see recover_stream()'s docstring."""
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                self.log(f'Wake stream close failed: {e}')
            self.stream = None

    def recover_stream(self):
        """(Re)build the wake InputStream from scratch. Used both after
        handing the mic back from SpeechRecognition/sounddevice capture and
        by the watchdog in session.py.

        Closes and recreates the InputStream on each handoff rather than
        calling stop()/start() on the same instance. Reusing one stream
        across many stop/start cycles (one pair per command, so hundreds over
        a long-running session) proved to eventually stop delivering audio to
        on_audio with no exception raised anywhere - the process stays alive
        and the tray icon still responds, but the wake word silently stops
        registering. This is exactly what made VORTEX look like it had
        "stopped listening" after running a while, even though nothing in the
        code was erroring."""
        if self.stream is not None:
            with contextlib.suppress(Exception):
                self.stream.stop()
                self.stream.close()
        try:
            self.wake_model.reset()
            self.stream = self._open_stream()
            self.stream.start()
            self.last_audio_at = time.monotonic()
        except Exception as e:
            self.log(f'Wake stream recovery failed: {e}')

    def close(self):
        if self.stream is not None:
            with contextlib.suppress(Exception):
                self.stream.stop()
                self.stream.close()

    # ---------- inference ----------

    def on_audio(self, indata, frames, time_info, status):
        """Audio thread. Stays cheap: detect, flag, hand off. Never speaks or blocks."""
        if not self.is_running():
            raise sd.CallbackStop()
        self.last_audio_at = time.monotonic()
        if self.capturing.is_set():
            return
        audio = self.audio_processor.boost((indata[:, 0] * 32767).astype(np.int16))
        score = max(self.wake_model.predict(audio).values())
        speaking = self.barge_in.speaking.is_set()
        # Ongoing diagnostic, not temporary: false wake activations in standby are
        # still an open issue (see IMPLEMENTED.md Phase 6) - this is what makes any
        # future occurrence tunable from real data instead of guesswork. Barge-in
        # itself was confirmed fixed via a live acoustic test on 2026-08-16 (see
        # CHANGELOG.md), which is what this same logging line helped verify.
        if score > 0.3:
            self.log(f'[diag] score={score:.3f} threshold={self.wake_threshold if not speaking else self.barge_in_threshold} noise_floor={self.audio_processor.noise_floor:.0f}')
        if score < (self.barge_in_threshold if speaking else self.wake_threshold):
            return
        now = time.monotonic()
        if now - self.last_wake < self.wake_cooldown:
            return
        self.last_wake = now
        self.wake_model.reset()
        self.log(f'{"Barge-in" if speaking else "Wake"} triggered: score={score:.3f} noise_floor={self.audio_processor.noise_floor:.0f}')
        if speaking:
            # Cut the audio here, on the spot; the worker picks up the new command.
            self.barge_in.stop_speaking.set()
        self.events.put('barge_in' if speaking else 'wake')
