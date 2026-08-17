"""Streaming, cancellable text-to-speech: chunking, edge-tts synthesis, and
pygame playback.

Extracted verbatim from Vortex._chunk_stream, _synth, _unlink, _play,
_speak_chunks, speak, speak_stream (docs/REFACTOR_PLAN.md Step 3) - the
cancellable-asyncio-task synthesis, the _play timing diagnostics, and the
double-checked `interrupted` flag in _speak_chunks are all fixes from the
2026-08-16 live debugging session (see CHANGELOG.md) and are preserved here
exactly, not "cleaned up" while moving.
"""
import asyncio
import os
import pathlib
import queue
import re
import tempfile
import threading
import time
import wave

import aiohttp
import edge_tts
import pygame

# A chunk boundary: sentence punctuation followed by space, or a line break.
BREAK = re.compile(r'(?:[.!?;:]["\')\]]?\s+|\n+)')
MARKUP = re.compile(r'[*_`#]+')
MIN_CHUNK = 25
MAX_CHUNK = 320
_STOP = object()
# Sentinel distinguishing "never tried to load" from "tried and it's
# unavailable" (None) - see the matching sentinel in stt.py for why.
_UNSET = object()
# aiohttp.ClientConnectionError covers ClientConnectorError (DNS/refused/no
# route) and ServerTimeoutError - i.e. "couldn't reach the service at all".
# asyncio.TimeoutError covers this module's own connect/receive timeouts.
# Deliberately NOT edge_tts's own EdgeTTSException family (WebSocketError,
# UnexpectedResponse, NoAudioReceived, ...) - those mean the service WAS
# reached and returned something odd, which is a different, retriable
# problem, not a network-down problem; falling back to a worse local voice
# for that would be a downgrade, not a fallback. Same reasoning as stt.py's
# sr.RequestError-vs-sr.UnknownValueError split.
_NETWORK_ERRORS = (aiohttp.ClientConnectionError, asyncio.TimeoutError)


class TextToSpeech:
    """Judgment call: this module does NOT introduce a separate
    TextToSpeech-interface-plus-adapter split (the plan's "New seam
    introduced" note) - there is exactly one concrete implementation
    (edge-tts + pygame) today, today's fixes are deeply specific to that
    implementation's cancellation behavior, and adding an abstraction layer
    on top right after a hard-won live-debugging session adds a place a fix
    could get lost in translation for no near-term benefit. This class *is*
    the concrete implementation; a future SpeechToText/TextToSpeech interface
    can wrap it later without touching this logic.

    Offline TTS fallback (piper-tts), added 2026-08-16 - see config.py's
    offline_fallback_enabled docstring for the reasoning and IMPLEMENTED.md's
    Phase 1 row for what's actually verified. Kept inside this one concrete
    class for the same reason as above, not split behind a second
    TextToSpeech implementation.
    """

    def __init__(self, *, voice, tts_volume, barge_in, log, is_running,
                 offline_enabled=True, offline_voice='en_US-lessac-medium',
                 offline_model_dir=None):
        self.voice = voice
        self.tts_volume = tts_volume
        self.barge_in = barge_in
        self.log = log
        self.is_running = is_running
        self.offline_enabled = offline_enabled
        self.offline_voice = offline_voice
        self.offline_model_dir = offline_model_dir
        self._offline_piper_voice = _UNSET  # lazy singleton - see _get_offline_voice

    def _chunk_stream(self, fragments):
        """Turn a stream of text fragments into speakable sentence-sized chunks."""
        buf = ''
        for fragment in fragments:
            if self.barge_in.stop_speaking.is_set() or not self.is_running():
                break
            buf += fragment
            while True:
                # First break long enough to be worth speaking on its own.
                m = next((b for b in BREAK.finditer(buf) if b.end() >= MIN_CHUNK), None)
                if m:
                    chunk, buf = buf[:m.end()], buf[m.end():]
                elif len(buf) > MAX_CHUNK:
                    cut = buf.rfind(' ', 0, MAX_CHUNK)
                    cut = cut if cut > MIN_CHUNK else MAX_CHUNK
                    chunk, buf = buf[:cut], buf[cut:]
                else:
                    break
                chunk = MARKUP.sub('', chunk).strip()
                if chunk:
                    yield chunk
        buf = MARKUP.sub('', buf).strip()
        if buf:
            yield buf

    def _synth(self, loop, text):
        """Render one chunk to an mp3 and return its path, or None if it failed.
        Logs the chunk here (not just in speak()) so speak_stream() - every LLM
        and document answer - is actually visible in the log too; previously only
        speak()'s deterministic replies ("Yes Boss?", date/time) were logged at
        all, leaving every AI-generated response completely unlogged.

        Runs the edge-tts network call as a cancellable task instead of a plain
        `run_until_complete`, polling stop_speaking every 0.1s - a plain await
        blocks until the network call itself returns, deaf to stop_speaking in
        the meantime. Live evidence this mattered: a barge-in landing while a
        chunk was still being synthesized (not yet playing) took ~9s to
        actually register, exactly one edge-tts round trip's worth of delay,
        even after fixing the equivalent gap in the LLM token stream."""
        self.log(f'Speaking: {text}')
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
        path = tmp.name
        tmp.close()
        task = loop.create_task(edge_tts.Communicate(text, self.voice).save(path))
        try:
            while not task.done():
                if self.barge_in.stop_speaking.is_set() or not self.is_running():
                    task.cancel()
                    loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
                    self._unlink(path)
                    return None
                loop.run_until_complete(asyncio.wait({task}, timeout=0.1))
            task.result()
            return path
        except _NETWORK_ERRORS as e:
            # Couldn't reach edge-tts at all (network/DNS/timeout) - the one
            # case the offline fallback is for. edge_tts's own EdgeTTSException
            # family (malformed/empty response, the service WAS reached) is
            # deliberately NOT caught here and falls through to the generic
            # except below, unchanged from before this fallback existed.
            self._unlink(path)
            self.log(f'Cloud TTS unreachable ({type(e).__name__}: {e}); trying offline fallback')
            offline_path = self._synth_offline(text)
            if offline_path:
                self.log('Synthesized offline (fallback)')
                return offline_path
            self.log('Offline TTS fallback unavailable or failed')
            return None
        except Exception as e:
            self.log(f'TTS synth error: {e}')
            self._unlink(path)
            return None

    def _get_offline_voice(self):
        """Lazy-loaded piper-tts singleton - loaded at most once per process,
        not once per chunk (load was measured at ~4s warm, see IMPLEMENTED.md
        - paying that per chunk would make streamed speech far choppier than
        the pipelined producer/consumer design in _speak_chunks intends)."""
        if not self.offline_enabled:
            return None
        if self._offline_piper_voice is _UNSET:
            try:
                from piper import PiperVoice
                model_path = os.path.join(self.offline_model_dir or '.', f'{self.offline_voice}.onnx')
                if not os.path.exists(model_path):
                    raise FileNotFoundError(
                        f'offline TTS voice not cached at {model_path} - call '
                        'ensure_offline_ready() while online first, or run '
                        '`python -m piper.download_voices` for this voice')
                self._offline_piper_voice = PiperVoice.load(model_path)
                self.log(f'Offline TTS voice ready: {self.offline_voice}')
            except Exception as e:
                # Not installed, not downloaded yet and no network to fetch it,
                # corrupt file, etc - all degrade to "offline unavailable"
                # rather than raising, same as documents.py's _ocr_available.
                self.log(f'Offline TTS unavailable: {type(e).__name__}: {e}')
                self._offline_piper_voice = None
        return self._offline_piper_voice

    def ensure_offline_ready(self):
        """Explicit warm-up hook (called from main.py's _warm_up_models, off the
        critical path, while the network is presumably still up): downloads
        the piper voice if it isn't cached yet, then loads it. If the first
        load attempt happened during _synth's fallback instead, that would be
        exactly the moment the network is down, and a fresh download would
        fail too."""
        if not self.offline_enabled:
            return
        model_path = os.path.join(self.offline_model_dir or '.', f'{self.offline_voice}.onnx')
        if not os.path.exists(model_path):
            try:
                from piper.download_voices import download_voice
                os.makedirs(self.offline_model_dir or '.', exist_ok=True)
                # download_voice does path / "filename" internally - it requires
                # an actual pathlib.Path, not a plain str (confirmed the hard
                # way: passing a str raised TypeError: unsupported operand
                # type(s) for /: 'str' and 'str').
                download_voice(self.offline_voice, pathlib.Path(self.offline_model_dir or '.'))
                self.log(f'Offline TTS voice downloaded: {self.offline_voice}')
            except Exception as e:
                self.log(f'Offline TTS voice download failed: {type(e).__name__}: {e}')
                return
        self._get_offline_voice()

    def _synth_offline(self, text):
        """Render one chunk via piper-tts (local, no network) to a wav and
        return its path, or None if it failed. Only ever called from _synth
        when edge-tts raised a network-reachability error - see _NETWORK_ERRORS."""
        voice = self._get_offline_voice()
        if voice is None:
            return None
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
        path = tmp.name
        tmp.close()
        try:
            with wave.open(path, 'wb') as wav_file:
                voice.synthesize_wav(text, wav_file)
            return path
        except Exception as e:
            self.log(f'Offline TTS synth error: {type(e).__name__}: {e}')
            self._unlink(path)
            return None

    def _unlink(self, path):
        try: os.remove(path)
        except OSError: pass

    def _play(self, path):
        pygame.mixer.music.load(path)
        pygame.mixer.music.set_volume(self.tts_volume)
        pygame.mixer.music.play()
        t0 = time.monotonic()
        stopped_early = False
        while pygame.mixer.music.get_busy():
            if self.barge_in.stop_speaking.is_set() or not self.is_running():
                pygame.mixer.music.stop()
                stopped_early = True
                break
            time.sleep(0.05)
        # Diagnostic only (temporary): a barge-in logged as "triggered" today was
        # observed taking 15-25s to actually silence VORTEX, even after fixing the
        # one confirmed cause (LLM token stream not polled for stop_speaking). This
        # pins down whether _play() itself ever sees the flag - if playback always
        # ends here with stopped_early=False regardless of when stop_speaking was
        # set elsewhere, the bug is in this loop or the flag's visibility, not in
        # how long synthesis/generation took upstream.
        self.log(f'[diag] _play took {time.monotonic()-t0:.2f}s stopped_early={stopped_early}')
        pygame.mixer.music.unload()

    def _speak_chunks(self, chunks):
        """Synthesise ahead on a worker thread while playing, so speech starts fast
        and stops the instant stop_speaking is set. Returns False if interrupted."""
        audio_q = queue.Queue(maxsize=2)

        def producer():
            loop = asyncio.new_event_loop()
            try:
                for chunk in chunks:
                    if self.barge_in.stop_speaking.is_set() or not self.is_running():
                        break
                    path = self._synth(loop, chunk)
                    if path is None:
                        continue
                    while True:
                        if self.barge_in.stop_speaking.is_set() or not self.is_running():
                            self._unlink(path)
                            return
                        try:
                            audio_q.put(path, timeout=0.1)
                            break
                        except queue.Full:
                            continue
            except Exception as e:
                self.log(f'TTS producer error: {e}')
            finally:
                loop.close()
                audio_q.put(_STOP)

        self.barge_in.speaking.set()
        threading.Thread(target=producer, daemon=True).start()
        interrupted = False
        try:
            # Always drain to _STOP so the producer never blocks on a full queue.
            while True:
                item = audio_q.get()
                if item is _STOP:
                    break
                if self.barge_in.stop_speaking.is_set() or not self.is_running():
                    self._unlink(item)
                    interrupted = True
                    continue
                self._play(item)
                self._unlink(item)
                if self.barge_in.stop_speaking.is_set():
                    interrupted = True
        finally:
            self.barge_in.speaking.clear()
        # A chunk cancelled mid-synthesis (_synth returning None because
        # stop_speaking got set while it was still awaiting edge-tts) never
        # reaches this loop's own interrupted=True checks - it's discarded by
        # the producer before ever reaching the queue. Checking the flag
        # directly here catches that path too, so "Speech interrupted" is
        # logged whenever stop_speaking ended up set, regardless of exactly
        # which stage the interruption landed in.
        interrupted = interrupted or self.barge_in.stop_speaking.is_set()
        if interrupted:
            self.log('Speech interrupted')
        return not interrupted

    def speak(self, text):
        # No log call here - _synth logs each chunk as it's actually synthesized,
        # which covers both this method and speak_stream() uniformly.
        return self._speak_chunks(self._chunk_stream(iter([text])))

    def speak_stream(self, fragments):
        return self._speak_chunks(self._chunk_stream(fragments))
