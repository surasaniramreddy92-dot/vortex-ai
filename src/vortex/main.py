# VORTEX main.py - custom wake word + barge-in + multi-turn session build
# Windows + ONNX-only wake architecture
import asyncio
import contextlib
import datetime
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
import psutil
import pygame
import pystray
import sounddevice as sd
import speech_recognition as sr
from PIL import Image, ImageDraw
from dotenv import load_dotenv
import edge_tts
import ollama
from openwakeword.model import Model

from .memory import MemoryStore
from .documents import resolve_document, extract_text, build_document_prompt
from .browser import BrowserAgent
from .rag import RagStore, build_rag_prompt
from .config import VortexConfig

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
load_dotenv()

# Refactor Step 2 (docs/REFACTOR_PLAN.md): every value below now comes from one
# typed VortexConfig instead of its own os.getenv(...) call. Deliberately kept as
# module-level constants for now, not self.config.* attribute access throughout
# the class - that's a larger, separate change (Step 3+, extracting subsystems
# into their own files). This step is scoped to "read from config.py", not
# "restructure every call site"; every default below is unchanged from before,
# verified by tests/unit/test_config.py asserting each one against main.py's
# prior values.
_cfg = VortexConfig.from_env()
ROOT = _cfg.root
VOICE = _cfg.voice
USER_NAME = _cfg.user_name
TTS_VOLUME = _cfg.tts_volume
# Custom-trained model (tools/wakeword/build_hey_vortex.py) so the phrase matches the assistant's name.
WAKE_WORD = _cfg.wake_word
# Calibrated against held-out synthetic clips (tools/wakeword/validate_hey_vortex.py),
# which only covers clean TTS audio, not real mic/room noise - raised from 0.7 after
# real-world false activations (background noise, amplified by AGC, crossing 0.7).
WAKE_THRESHOLD = _cfg.wake_threshold
# NOT stricter than WAKE_THRESHOLD, on purpose: every observed false trigger so far
# happened in standby, none during barge-in, and barge-in is inherently *harder* to
# score high on (the mic also hears our own speakers, diluting the user's voice) -
# so making it a *higher* bar than standby (an earlier 0.9 attempt) just made
# genuine interruptions fail. Revisit with real numbers from the score/noise_floor
# diagnostic logging in _on_audio if false barge-ins actually start showing up.
BARGE_IN_THRESHOLD = _cfg.barge_in_threshold
WAKE_COOLDOWN = _cfg.wake_cooldown
# Safety net: callbacks should fire every ~80ms while listening. If none arrive for
# this long outside of an active SpeechRecognition handoff, the stream is presumed
# dead and gets rebuilt - see _recover_wake_stream's docstring for why this is needed.
WAKE_WATCHDOG_TIMEOUT = _cfg.wake_watchdog_timeout
# Laptop mics are quiet by default; boost normal speaking volume up to a target
# level before wake-word inference so you don't have to raise your voice. Gated
# against a rolling ambient-noise-floor estimate so steady background noise
# doesn't get amplified into a false trigger - only signal that stands out
# above the floor (a real voice-like transient) gets boosted.
AGC_TARGET_RMS = _cfg.agc_target_rms
AGC_MAX_GAIN = _cfg.agc_max_gain
AGC_NOISE_MARGIN = _cfg.agc_noise_margin
# How long an active session stays open for follow-ups (confirmations, next
# command) before requiring the wake word again.
SESSION_TIMEOUT = _cfg.session_timeout
MODEL = _cfg.llm_model
SYSTEM_PROMPT = _cfg.system_prompt
LLM_MAX_TOKENS = _cfg.llm_max_tokens
LOG_DIR = _cfg.log_dir
os.makedirs(LOG_DIR, exist_ok=True)
DATA_DIR = _cfg.data_dir
os.makedirs(DATA_DIR, exist_ok=True)
MEMORY_DB_PATH = _cfg.memory_db_path
HISTORY_TURNS = _cfg.history_turns
SUMMARY_MAX_CHARS = _cfg.summary_max_chars  # plain-summarize path only; RAG-backed Q&A doesn't need this cap
logging.basicConfig(filename=os.path.join(LOG_DIR, 'vortex.log'), level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
pygame.mixer.init()

# A chunk boundary: sentence punctuation followed by space, or a line break.
BREAK = re.compile(r'(?:[.!?;:]["\')\]]?\s+|\n+)')
MARKUP = re.compile(r'[*_`#]+')
MIN_CHUNK = 25
MAX_CHUNK = 320
_STOP = object()

class Vortex:
    def __init__(self):
        self.running = True
        self.icon = None
        self.stream = None
        self.recognizer = sr.Recognizer()
        self.current_pid = os.getpid()
        self.awaiting_confirmation = None
        self.memory = MemoryStore(MEMORY_DB_PATH)
        self.browser = BrowserAgent()
        try:
            self.rag = RagStore()
        except Exception as e:
            # Postgres/Qdrant not running is a real, expected possibility (they're
            # separate local services, not bundled with VORTEX) - degrade to the
            # simpler truncated-document approach rather than failing to start.
            self.log(f'RAG store unavailable, falling back to plain document reads: {e}')
            self.rag = None
        # speaking: TTS is on air. stop_speaking: cut it off now.
        # capturing: SpeechRecognition owns the mic, so the wake stream stands down.
        self.speaking = threading.Event()
        self.stop_speaking = threading.Event()
        self.capturing = threading.Event()
        self.events = queue.Queue()
        self.last_wake = 0.0
        self.noise_floor = 250.0
        self.last_audio_at = time.monotonic()
        self.wake_model = Model(wakeword_models=[WAKE_WORD], inference_framework='onnx')
        self.protected = {
            'python.exe','pythonw.exe','ollama.exe','explorer.exe','winlogon.exe','csrss.exe',
            'services.exe','lsass.exe','dwm.exe','system','taskhostw.exe','shellhost.exe'
        }
        self.native_apps = {
            'outlook': 'outlook.exe', 'chrome': 'chrome.exe', 'edge': 'msedge.exe',
            'vscode': 'code.exe', 'vs code': 'code.exe', 'visual studio code': 'code.exe',
            'notepad': 'notepad.exe', 'calculator': 'calc.exe', 'paint': 'mspaint.exe',
            'whatsapp': 'whatsapp.exe', 'teams': 'teams.exe', 'spotify': 'spotify.exe'
        }
        self.web_apps = {
            'youtube': 'https://youtube.com', 'gmail': 'https://mail.google.com',
            'github': 'https://github.com', 'chatgpt': 'https://chatgpt.com',
            'google': 'https://google.com', 'whatsapp': 'https://web.whatsapp.com'
        }

    def log(self, msg):
        logging.info(msg)

    def _agc(self, audio_i16):
        """Boost voice-level audio toward a target RMS before wake-word inference.
        Tracks a slow-moving ambient noise floor and only boosts frames that stand
        out above it - steady background noise gets left alone (and folded into
        the floor estimate) instead of amplified into a false wake trigger."""
        rms = np.sqrt(np.mean(audio_i16.astype(np.float64) ** 2))
        if rms < self.noise_floor * AGC_NOISE_MARGIN:
            self.noise_floor = 0.98 * self.noise_floor + 0.02 * rms
            return audio_i16
        gain = min(AGC_MAX_GAIN, AGC_TARGET_RMS / rms)
        if gain <= 1.0:
            return audio_i16
        return np.clip(audio_i16.astype(np.float64) * gain, -32768, 32767).astype(np.int16)

    # ---------- speech output ----------

    def _chunk_stream(self, fragments):
        """Turn a stream of text fragments into speakable sentence-sized chunks."""
        buf = ''
        for fragment in fragments:
            if self.stop_speaking.is_set() or not self.running:
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
        task = loop.create_task(edge_tts.Communicate(text, VOICE).save(path))
        try:
            while not task.done():
                if self.stop_speaking.is_set() or not self.running:
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
        pygame.mixer.music.set_volume(TTS_VOLUME)
        pygame.mixer.music.play()
        t0 = time.monotonic()
        stopped_early = False
        while pygame.mixer.music.get_busy():
            if self.stop_speaking.is_set() or not self.running:
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
                    if self.stop_speaking.is_set() or not self.running:
                        break
                    path = self._synth(loop, chunk)
                    if path is None:
                        continue
                    while True:
                        if self.stop_speaking.is_set() or not self.running:
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

        self.speaking.set()
        threading.Thread(target=producer, daemon=True).start()
        interrupted = False
        try:
            # Always drain to _STOP so the producer never blocks on a full queue.
            while True:
                item = audio_q.get()
                if item is _STOP:
                    break
                if self.stop_speaking.is_set() or not self.running:
                    self._unlink(item)
                    interrupted = True
                    continue
                self._play(item)
                self._unlink(item)
                if self.stop_speaking.is_set():
                    interrupted = True
        finally:
            self.speaking.clear()
        if interrupted:
            self.log('Speech interrupted')
        return not interrupted

    def speak(self, text):
        # No log call here - _synth logs each chunk as it's actually synthesized,
        # which covers both this method and speak_stream() uniformly.
        return self._speak_chunks(self._chunk_stream(iter([text])))

    def speak_stream(self, fragments):
        return self._speak_chunks(self._chunk_stream(fragments))

    def greet(self):
        hour = datetime.datetime.now().hour
        greet = 'Good morning' if hour < 12 else 'Good afternoon' if hour < 18 else 'Good evening'
        self.speak(f'{greet} {USER_NAME}. Vortex AI is online.')

    # ---------- speech input ----------

    def _open_wake_stream(self):
        return sd.InputStream(channels=1, samplerate=16000, dtype='float32',
                              blocksize=1280, callback=self._on_audio)

    def _recover_wake_stream(self):
        """(Re)build the wake InputStream from scratch. Used both after handing
        the mic back from SpeechRecognition and by the watchdog in _worker()."""
        if self.stream is not None:
            with contextlib.suppress(Exception):
                self.stream.stop()
                self.stream.close()
        try:
            self.wake_model.reset()
            self.stream = self._open_wake_stream()
            self.stream.start()
            self.last_audio_at = time.monotonic()
        except Exception as e:
            self.log(f'Wake stream recovery failed: {e}')

    @contextlib.contextmanager
    def _own_mic(self):
        """Hand the mic to SpeechRecognition; the wake stream stands down meanwhile.

        Closes and recreates the InputStream on each handoff rather than calling
        stop()/start() on the same instance. Reusing one stream across many
        stop/start cycles (one pair per command, so hundreds over a long-running
        session) proved to eventually stop delivering audio to _on_audio with no
        exception raised anywhere - the process stays alive and the tray icon
        still responds, but the wake word silently stops registering. This is
        exactly what made VORTEX look like it had "stopped listening" after
        running a while, even though nothing in the code was erroring."""
        self.capturing.set()
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                self.log(f'Wake stream close failed: {e}')
            self.stream = None
        try:
            yield
        finally:
            self._recover_wake_stream()
            self.capturing.clear()

    @staticmethod
    def _boost_audio_data(audio):
        """Unlike the wake stream (boosted by _agc), command audio captured via
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
        gain = min(6.0, AGC_TARGET_RMS / rms)
        if gain <= 1.0:
            return audio
        boosted = np.clip(samples.astype(np.float64) * gain, -32768, 32767).astype(np.int16)
        return sr.AudioData(boosted.tobytes(), audio.sample_rate, audio.sample_width)

    def capture_command(self, timeout=8):
        try:
            with self._own_mic():
                with sr.Microphone() as src:
                    self.recognizer.adjust_for_ambient_noise(src, duration=0.3)
                    audio = self.recognizer.listen(src, timeout=timeout, phrase_time_limit=8)
            # Diagnostic only (temporary - remove once the frequent post-"Yes Boss?"
            # UnknownValueError failures are root-caused): logs what was actually
            # captured even when recognize_google() can't transcribe it, so a near-
            # silent/garbled buffer (mic handoff timing) can be told apart from a
            # buffer with real signal that Google just couldn't parse.
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

    # ---------- reasoning ----------

    def _poll_stream(self, stream):
        """Consume an Ollama streaming response on a background thread and yield
        tokens through a polled queue, instead of a plain `for part in stream`.

        A plain for-loop blocks on the network read for the *next* token, and
        only re-checks stop_speaking once one arrives - fine when Ollama is
        warm, but this session's model failed to warm at startup (Ollama
        wasn't up yet when VORTEX tried), so the first real request paid a
        cold-load cost mid-generation. Live evidence: a barge-in was logged as
        "triggered" but the current answer kept playing for another ~20s
        before "yielding the floor" actually appeared - the generator was
        blocked waiting on Ollama's next token the whole time, deaf to
        stop_speaking. Mirrors the same producer-thread-plus-polled-queue
        pattern _speak_chunks already uses for TTS, so stop_speaking is now
        checked at least every 0.1s regardless of how slow Ollama is."""
        token_q = queue.Queue(maxsize=8)

        def pump():
            try:
                for part in stream:
                    token_q.put(part['message']['content'])
            except Exception as e:
                token_q.put(e)
            finally:
                token_q.put(_STOP)

        threading.Thread(target=pump, daemon=True).start()
        while True:
            if self.stop_speaking.is_set() or not self.running:
                return
            try:
                item = token_q.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is _STOP:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    def ask_llm_stream(self, query):
        """Yield reply text as Ollama produces it, so speech can start early and
        generation stops the moment we are interrupted."""
        self.memory.add_turn('user', query)
        messages = [{'role': 'system', 'content': SYSTEM_PROMPT}] + self.memory.recent(HISTORY_TURNS)
        reply = ''
        try:
            stream = ollama.chat(model=MODEL, messages=messages, stream=True, keep_alive='30m',
                                 options={'num_predict': LLM_MAX_TOKENS})
        except Exception as e:
            self.log(f'LLM error: {e}')
            yield 'Sorry Boss, my reasoning engine is currently offline.'
            return
        try:
            for token in self._poll_stream(stream):
                reply += token
                yield token
        except Exception as e:
            self.log(f'LLM stream error: {e}')
            if not reply:
                yield 'Sorry Boss, my reasoning engine is currently offline.'
        finally:
            with contextlib.suppress(Exception):
                stream.close()
            if reply:
                self.memory.add_turn('assistant', reply)

    # ---------- documents ----------

    def _stream_llm_answer(self, system_prompt, user_content):
        """Shared streaming/barge-in-friendly pattern: one system+user message
        in, tokens out, stoppable mid-generation exactly like ask_llm_stream."""
        messages = [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_content}]
        try:
            stream = ollama.chat(model=MODEL, messages=messages, stream=True, keep_alive='30m',
                                 options={'num_predict': LLM_MAX_TOKENS})
        except Exception as e:
            self.log(f'Document LLM error: {e}')
            yield 'Sorry Boss, my reasoning engine is currently offline.'
            return
        try:
            yield from self._poll_stream(stream)
        except Exception as e:
            self.log(f'Document LLM stream error: {e}')
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    def summarize_document(self, name):
        """Whole-document (truncated) summary - summarization wants the whole
        document, not similarity-retrieved snippets, so this doesn't use RAG."""
        path = resolve_document(name)
        if not path:
            self.speak(f"I couldn't find a document called {name}.")
            return
        self.speak(f'Reading {os.path.basename(path)}, one moment.')
        text = extract_text(path)
        if not text.strip():
            self.speak("That document appears to be empty, or I couldn't read its text.")
            return
        prompt = build_document_prompt(text[:SUMMARY_MAX_CHARS], 'summarize the document in a few sentences')
        self.speak_stream(self._stream_llm_answer(
            'You answer questions strictly using the provided document text. '
            'If the answer is not in the document, say so plainly. Answer in short spoken sentences.',
            prompt))

    def answer_document_question(self, name, question):
        """Targeted questions retrieve only the relevant chunks via RagStore,
        so a long document doesn't silently lose everything past a truncation
        cutoff. Falls back to the old truncated-whole-document approach if
        Postgres/Qdrant aren't running."""
        path = resolve_document(name)
        if not path:
            self.speak(f"I couldn't find a document called {name}.")
            return
        text = extract_text(path)
        if not text.strip():
            self.speak("That document appears to be empty, or I couldn't read its text.")
            return
        if self.rag is not None:
            try:
                doc_id = self.rag.ensure_ingested(path, text)
                chunks = self.rag.retrieve(doc_id, question)
                if chunks:
                    prompt = build_rag_prompt(chunks, question)
                    self.speak_stream(self._stream_llm_answer(
                        'Answer strictly using the provided excerpts. If they do not contain '
                        'the answer, say so plainly. Answer in short spoken sentences.', prompt))
                    return
            except Exception as e:
                self.log(f'RAG retrieval failed, falling back to plain document read: {e}')
        prompt = build_document_prompt(text[:SUMMARY_MAX_CHARS], question)
        self.speak_stream(self._stream_llm_answer(
            'You answer questions strictly using the provided document text. '
            'If the answer is not in the document, say so plainly. Answer in short spoken sentences.',
            prompt))

    # ---------- actions ----------

    def open_target(self, target):
        """Native apps launch locally. Anything web-related routes through the
        one Playwright-controlled browser (self.browser) instead of the system's
        default browser, so there's a single consistent, automatable browser
        session rather than two different ones depending which path fires -
        and so an unmatched multi-word phrase gets an actual web search with
        results read back, not a silent literal-phrase Google search window."""
        target = target.lower().strip()
        if target in self.native_apps:
            try:
                subprocess.Popen(self.native_apps[target])
                self.speak(f'Opening {target}.')
                return True
            except OSError: pass
        if target in self.web_apps:
            self.browser.open(self.web_apps[target])
            self.speak(f'Opening {target}.')
            return True
        self.speak(self.browser.open(target))
        return True

    def close_named_app(self, target):
        exe = self.native_apps.get(target.lower())
        if not exe:
            self.speak(f"I don't know how to close {target} yet.")
            return
        closed = False
        for p in psutil.process_iter(['name']):
            try:
                if (p.info['name'] or '').lower() == exe.lower():
                    p.terminate()
                    closed = True
            except psutil.Error: pass
        self.speak(f'Closed {target}.' if closed else f'{target} was not running.')

    def close_all_apps(self):
        count = 0
        for p in psutil.process_iter(['pid', 'name']):
            try:
                name = (p.info['name'] or '').lower()
                pid = p.info['pid']
                if not name or pid == self.current_pid or name in self.protected:
                    continue
                p.terminate()
                count += 1
            except psutil.Error: pass
        self.speak(f'Closed {count} applications.')

    def system_shutdown(self):
        self.speak('Shutting down the system. See you soon Boss.')
        subprocess.Popen('shutdown /s /t 5', shell=True)

    def system_restart(self):
        self.speak('Restarting the system now Boss.')
        subprocess.Popen('shutdown /r /t 5', shell=True)

    def lock_system(self):
        # No confirmation needed, unlike shutdown/restart/close-all: locking is
        # trivially reversible (just log back in) and loses no unsaved work.
        self.speak('Locking the system now Boss.')
        subprocess.Popen('rundll32.exe user32.dll,LockWorkStation', shell=True)

    def handle_confirmation(self, cmd):
        if not self.awaiting_confirmation:
            return False
        if 'yes' in cmd:
            action = self.awaiting_confirmation
            self.awaiting_confirmation = None
            if action == 'close_all': self.close_all_apps()
            elif action == 'shutdown': self.system_shutdown()
            elif action == 'restart': self.system_restart()
        else:
            self.awaiting_confirmation = None
            self.speak('Action cancelled, Boss.')
        return True

    def execute(self, cmd):
        if not cmd:
            self.speak('I did not catch that Boss.')
            return
        if self.handle_confirmation(cmd):
            return
        if 'shutdown vortex' in cmd:
            self.speak('Shutting down. See you soon Boss.')
            self.stop()
            return
        if re.search(r'\btime\b', cmd):
            self.speak(f"The current time is {datetime.datetime.now().strftime('%I:%M %p')}")
            return
        if re.search(r'\bdate\b', cmd):
            self.speak(f"Today's date is {datetime.datetime.now().strftime('%d %B %Y')}")
            return
        if 'close all' in cmd:
            self.awaiting_confirmation = 'close_all'
            self.speak('This may close unsaved work. Should I proceed?')
            return
        if 'restart system' in cmd or 'reboot system' in cmd:
            self.awaiting_confirmation = 'restart'
            self.speak('Should I restart the system now Boss?')
            return
        if 'shutdown system' in cmd:
            self.awaiting_confirmation = 'shutdown'
            self.speak('Should I shut down the system now Boss?')
            return
        if re.search(r'\block\b.*\b(?:system|computer|screen|pc)\b', cmd):
            self.lock_system()
            return
        # Browser commands are checked before the generic close/open/read patterns
        # below so "close browser" / "read the page" don't get misrouted.
        if 'close browser' in cmd or 'quit browser' in cmd:
            self.browser.close()
            self.speak('Closed the browser.')
            return
        if re.search(r"(?:read|what'?s on) (?:the |this )?page", cmd):
            self.speak(self.browser.read_page())
            return
        # YouTube search-and-play is checked before the generic "open (.+)" pattern
        # below, which used to swallow phrases like "open youtube and play X" whole
        # and fall through to a dumb literal-phrase web search (see IMPLEMENTED.md).
        m = (re.match(r'(?:open |go to )?youtube(?: and)? (?:play|search for|find|watch) (.+)', cmd)
             or re.match(r'play (.+) on youtube', cmd)
             or re.match(r'search youtube for (.+)', cmd))
        if m:
            self.speak(self.browser.play_youtube(m.group(1).strip()))
            return
        m = re.match(r'(?:search(?: the web)? for|google) (.+)', cmd)
        if m:
            self.speak(self.browser.search(m.group(1).strip()))
            return
        m = re.match(r'(?:go to|browse to|browse) (.+)', cmd)
        if m:
            self.speak(self.browser.open(m.group(1).strip()))
            return
        m = re.match(r'click (?:on )?(.+)', cmd)
        if m:
            self.speak(self.browser.click_text(m.group(1).strip()))
            return
        m = re.match(r'close (.+)', cmd)
        if m:
            self.close_named_app(m.group(1).strip())
            return
        m = re.match(r'open (.+)', cmd)
        if m:
            self.open_target(m.group(1).strip())
            return
        # Document commands, checked after the app-launching "open (.+)" pattern
        # so "open chrome" still launches an app rather than looking for a file.
        m = re.match(r'what does (.+?) say about (.+)', cmd)
        if m:
            self.answer_document_question(m.group(1).strip(), m.group(2).strip())
            return
        m = re.match(r'(?:summarize|summarise) (.+)', cmd)
        if m:
            self.summarize_document(m.group(1).strip())
            return
        m = re.match(r'read (?:me )?(.+)', cmd)
        if m:
            self.summarize_document(m.group(1).strip())
            return
        self.speak_stream(self.ask_llm_stream(cmd))

    # ---------- wake / dispatch ----------

    def _on_audio(self, indata, frames, time_info, status):
        """Audio thread. Stays cheap: detect, flag, hand off. Never speaks or blocks."""
        if not self.running:
            raise sd.CallbackStop()
        self.last_audio_at = time.monotonic()
        if self.capturing.is_set():
            return
        audio = self._agc((indata[:, 0] * 32767).astype(np.int16))
        score = max(self.wake_model.predict(audio).values())
        speaking = self.speaking.is_set()
        # Ongoing diagnostic, not temporary: false wake activations in standby are
        # still an open issue (see IMPLEMENTED.md Phase 6) - this is what makes any
        # future occurrence tunable from real data instead of guesswork. Barge-in
        # itself was confirmed fixed via a live acoustic test on 2026-08-16 (see
        # CHANGELOG.md), which is what this same logging line helped verify.
        if score > 0.3:
            self.log(f'[diag] score={score:.3f} threshold={WAKE_THRESHOLD if not speaking else BARGE_IN_THRESHOLD} noise_floor={self.noise_floor:.0f}')
        if score < (BARGE_IN_THRESHOLD if speaking else WAKE_THRESHOLD):
            return
        now = time.monotonic()
        if now - self.last_wake < WAKE_COOLDOWN:
            return
        self.last_wake = now
        self.wake_model.reset()
        self.log(f'{"Barge-in" if speaking else "Wake"} triggered: score={score:.3f} noise_floor={self.noise_floor:.0f}')
        if speaking:
            # Cut the audio here, on the spot; the worker picks up the new command.
            self.stop_speaking.set()
        self.events.put('barge_in' if speaking else 'wake')

    def _active_session(self):
        """Keep listening for follow-ups (confirmations, next command) without
        requiring the wake word again, until SESSION_TIMEOUT of silence."""
        while self.running:
            cmd = self.capture_command(timeout=SESSION_TIMEOUT)
            if cmd is None:
                self.log('Session timed out, returning to standby')
                self.awaiting_confirmation = None
                return
            self.execute(cmd)

    def _warm_up_models(self):
        """Ollama unloads a model from memory after ~5 min idle; the next request
        then pays a cold-load cost (measured ~9s for llama3.2:1b on this machine)
        on top of normal STT/network latency, which is exactly what makes VORTEX
        look like it isn't responding on the first real command after a restart
        or a pause. Firing a trivial request per model right at startup, off the
        greeting's critical path, means that cost is usually already paid by the
        time you actually speak."""
        try:
            ollama.chat(model=MODEL, messages=[{'role': 'user', 'content': 'hi'}], keep_alive='30m')
        except Exception as e:
            self.log(f'Model warm-up failed (LLM): {e}')
        if self.rag is not None:
            try:
                ollama.embeddings(model=_cfg.embed_model, prompt='warm up', keep_alive='30m')
            except Exception as e:
                self.log(f'Model warm-up failed (embeddings): {e}')

    def _worker(self):
        """Owns every slow operation: speaking, listening, executing."""
        time.sleep(1.5)
        self.stop_speaking.clear()
        threading.Thread(target=self._warm_up_models, daemon=True).start()
        self.greet()
        while self.running:
            try:
                event = self.events.get(timeout=0.5)
            except queue.Empty:
                if (not self.capturing.is_set()
                        and time.monotonic() - self.last_audio_at > WAKE_WATCHDOG_TIMEOUT):
                    self.log(f'Wake stream watchdog: no audio for over {WAKE_WATCHDOG_TIMEOUT}s, rebuilding stream')
                    self._recover_wake_stream()
                continue
            while not self.events.empty():
                with contextlib.suppress(queue.Empty):
                    self.events.get_nowait()
            if not self.running:
                break
            self.stop_speaking.clear()
            if event == 'barge_in':
                # Speech is already cut. Drop any pending prompt and take the new order.
                self.log('Barge-in: yielding the floor')
                self.awaiting_confirmation = None
            # Always spoken, for both a fresh wake and a barge-in: cutting off
            # mid-sentence with total silence gave no audible confirmation that
            # the interruption actually registered - hearing "Yes Boss?" right
            # after the cutoff is the confirmation.
            self.speak('Yes Boss?')
            self._active_session()

    def request_stop_speaking(self, icon=None, item=None):
        self.stop_speaking.set()

    def listen_now(self, icon=None, item=None):
        self.stop_speaking.set()
        self.events.put('barge_in')

    def stop(self, icon=None, item=None):
        """The single shutdown path - used by both the "shutdown vortex" voice
        command and the tray's Exit item. Must stop the tray icon itself, or
        icon.run() (blocking the main thread in start()) never returns and the
        process lingers as a zombie: no longer listening or responding, but
        never actually exiting."""
        self.running = False
        self.stop_speaking.set()
        if self.icon is not None:
            self.icon.stop()

    def tray_exit(self, icon, item):
        self.stop()

    def tray_icon(self):
        img = Image.new('RGB', (64,64), 'black')
        d = ImageDraw.Draw(img)
        d.ellipse((16,16,48,48), fill='white')
        return img

    def shutdown(self):
        self.running = False
        self.stop_speaking.set()
        with contextlib.suppress(Exception):
            pygame.mixer.music.stop()
        if self.stream is not None:
            with contextlib.suppress(Exception):
                self.stream.stop()
                self.stream.close()
        with contextlib.suppress(Exception):
            self.browser.close()
        with contextlib.suppress(Exception):
            self.memory.close()
        if self.rag is not None:
            with contextlib.suppress(Exception):
                self.rag.close()

    def start(self):
        self.stream = self._open_wake_stream()
        self.stream.start()
        threading.Thread(target=self._worker, daemon=True).start()
        self.icon = pystray.Icon('VORTEX', self.tray_icon(), 'VORTEX Assistant', menu=pystray.Menu(
            pystray.MenuItem('Stop talking', self.request_stop_speaking),
            pystray.MenuItem('Listen now', self.listen_now),
            pystray.MenuItem('Exit', self.tray_exit)))
        try:
            self.icon.run()
        finally:
            self.shutdown()

if __name__ == '__main__':
    Vortex().start()
