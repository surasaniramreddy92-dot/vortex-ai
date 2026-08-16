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
import queue
import re
import tempfile
import threading
import time

import edge_tts
import pygame

# A chunk boundary: sentence punctuation followed by space, or a line break.
BREAK = re.compile(r'(?:[.!?;:]["\')\]]?\s+|\n+)')
MARKUP = re.compile(r'[*_`#]+')
MIN_CHUNK = 25
MAX_CHUNK = 320
_STOP = object()


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
    """

    def __init__(self, *, voice, tts_volume, barge_in, log, is_running):
        self.voice = voice
        self.tts_volume = tts_volume
        self.barge_in = barge_in
        self.log = log
        self.is_running = is_running

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
        except Exception as e:
            self.log(f'TTS synth error: {e}')
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
