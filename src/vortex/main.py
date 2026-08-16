# VORTEX main.py - custom wake word + barge-in + multi-turn session build
# Windows + ONNX-only wake architecture
import contextlib
import datetime
import logging
import os
import queue
import re
import subprocess
import sys
import threading

import psutil
import pygame
import pystray
import speech_recognition as sr
from PIL import Image, ImageDraw
from dotenv import load_dotenv
import ollama

from .memory import MemoryStore
from .documents import resolve_document, extract_text, build_document_prompt
from .browser import BrowserAgent
from .rag import RagStore, build_rag_prompt
from .config import VortexConfig
from .voice.barge_in import BargeIn
from .voice.audio import AudioProcessor
from .voice.wake import WakeDetector
from .voice.tts import TextToSpeech, MAX_CHUNK
from .voice.stt import SpeechToText
from .voice.session import Session

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
# diagnostic logging in voice/wake.py's on_audio if false barge-ins actually start
# showing up.
BARGE_IN_THRESHOLD = _cfg.barge_in_threshold
WAKE_COOLDOWN = _cfg.wake_cooldown
# Safety net: callbacks should fire every ~80ms while listening. If none arrive for
# this long outside of an active SpeechRecognition handoff, the stream is presumed
# dead and gets rebuilt - see voice/wake.py's WakeDetector.recover_stream docstring
# for why this is needed.
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

# Used only by _poll_stream's token queue below - voice/tts.py has its own,
# independent _STOP sentinel for its own audio queue. Two separate objects on
# purpose: each queue's consumer only ever compares against the sentinel its
# own producer put there, so there's no need (and no benefit) to share one.
_STOP = object()

class Vortex:
    def __init__(self):
        self.running = True
        self.icon = None
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

        # ---------- voice subsystem (docs/REFACTOR_PLAN.md Step 3) ----------
        # barge_in: shared speaking/stop_speaking Events, checked across wake
        # detection, TTS playback, and LLM token streaming (_poll_stream below).
        # capturing: SpeechRecognition/the sounddevice capture owns the mic, so
        # the wake stream stands down meanwhile.
        self.barge_in = BargeIn()
        self.capturing = threading.Event()
        self.events = queue.Queue()

        self.audio = AudioProcessor(AGC_TARGET_RMS, AGC_MAX_GAIN, AGC_NOISE_MARGIN)
        self.wake = WakeDetector(
            wake_word_path=WAKE_WORD, wake_threshold=WAKE_THRESHOLD,
            barge_in_threshold=BARGE_IN_THRESHOLD, wake_cooldown=WAKE_COOLDOWN,
            audio_processor=self.audio, barge_in=self.barge_in, capturing=self.capturing,
            events=self.events, log=self.log, is_running=lambda: self.running)
        self.tts = TextToSpeech(voice=VOICE, tts_volume=TTS_VOLUME, barge_in=self.barge_in,
                                 log=self.log, is_running=lambda: self.running)
        self.stt = SpeechToText(
            recognizer=self.recognizer, capturing=self.capturing, agc_target_rms=AGC_TARGET_RMS,
            stop_wake_stream=self.wake.stop_stream, recover_wake_stream=self.wake.recover_stream,
            log=self.log)
        # Callables below are lambdas closing over `self` and calling back
        # through Vortex's own (dynamically-dispatched) methods, not pre-bound
        # references captured once - so instance-level monkeypatching of
        # v.speak / v.capture_command / v.execute / v.greet (as
        # tools/test_barge_in.py's worker-dispatch scenario does) is still
        # picked up at call time, exactly as it was before this extraction.
        self.session = Session(
            events=self.events, barge_in=self.barge_in, session_timeout=SESSION_TIMEOUT,
            wake_watchdog_timeout=WAKE_WATCHDOG_TIMEOUT,
            capture_command=lambda timeout=8: self.capture_command(timeout=timeout),
            execute=lambda cmd: self.execute(cmd),
            speak=lambda text: self.speak(text),
            greet=lambda: self.greet(),
            warm_up=lambda: self._warm_up_models(),
            get_last_audio_at=lambda: self.wake.last_audio_at,
            recover_wake_stream=self.wake.recover_stream,
            is_capturing=self.capturing.is_set,
            clear_awaiting_confirmation=self._clear_awaiting_confirmation,
            log=self.log, is_running=lambda: self.running)

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

    def _clear_awaiting_confirmation(self):
        self.awaiting_confirmation = None

    # ---------- speaking/stop_speaking: back-compat passthroughs ----------
    # Kept as properties (not moved-and-gone) because they're checked/set from
    # several places still in this file (execute()'s callers, tray callbacks,
    # _poll_stream/ask_llm_stream's barge-in-aware LLM streaming below) and by
    # external callers (tools/test_barge_in.py) that predate this extraction -
    # same two Event objects, now owned by self.barge_in.

    @property
    def speaking(self):
        return self.barge_in.speaking

    @property
    def stop_speaking(self):
        return self.barge_in.stop_speaking

    # ---------- speech output (voice/tts.py owns the actual logic) ----------

    def speak(self, text):
        return self.tts.speak(text)

    def speak_stream(self, fragments):
        return self.tts.speak_stream(fragments)

    def greet(self):
        hour = datetime.datetime.now().hour
        greet = 'Good morning' if hour < 12 else 'Good afternoon' if hour < 18 else 'Good evening'
        self.speak(f'{greet} {USER_NAME}. Vortex AI is online.')

    # ---------- speech input (voice/stt.py owns the actual logic) ----------

    def capture_command(self, timeout=8):
        return self.stt.capture_command(timeout=timeout)

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
        checked at least every 0.1s regardless of how slow Ollama is.

        Stays in main.py rather than moving to voice/tts.py (judgment call,
        docs/REFACTOR_PLAN.md Step 3): it polls Ollama's LLM stream, not TTS
        audio - its domain is LLM streaming (Step 4's llm/ package), not
        voice synthesis, even though it shares the barge-in cancellation
        pattern with voice/tts.py's _synth/_speak_chunks. Moving it into
        voice/tts.py would misfile LLM logic into the TTS module and leave
        Step 4 to untangle it later; leaving it here keeps it available to
        move again, as one piece, when the LLM provider is actually
        extracted."""
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

    # ---------- wake / dispatch (voice/wake.py, voice/session.py own the actual logic) ----------

    def _worker(self):
        return self.session.worker()

    def _active_session(self):
        return self.session.active_session()

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
        self.wake.close()
        with contextlib.suppress(Exception):
            self.browser.close()
        with contextlib.suppress(Exception):
            self.memory.close()
        if self.rag is not None:
            with contextlib.suppress(Exception):
                self.rag.close()

    def start(self):
        self.wake.start()
        threading.Thread(target=self.session.worker, daemon=True).start()
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
