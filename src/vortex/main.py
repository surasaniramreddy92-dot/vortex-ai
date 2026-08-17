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
from . import files as fileops
from .audit import AuditLog
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
# Structured JSON-lines audit trail (audit.py) for consequential actions -
# separate from, not a replacement for, the plain logging.info() calls below.
AUDIT_LOG_PATH = _cfg.audit_log_path
# Offline STT/TTS fallback (faster-whisper / piper-tts) - see config.py's
# offline_fallback_enabled docstring for the reasoning.
OFFLINE_FALLBACK_ENABLED = _cfg.offline_fallback_enabled
OFFLINE_STT_MODEL = _cfg.offline_stt_model
OFFLINE_STT_MODEL_DIR = _cfg.offline_stt_model_dir
OFFLINE_TTS_VOICE = _cfg.offline_tts_voice
OFFLINE_TTS_MODEL_DIR = _cfg.offline_tts_model_dir
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
        self.audit = AuditLog(AUDIT_LOG_PATH)
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
                                 log=self.log, is_running=lambda: self.running,
                                 offline_enabled=OFFLINE_FALLBACK_ENABLED, offline_voice=OFFLINE_TTS_VOICE,
                                 offline_model_dir=OFFLINE_TTS_MODEL_DIR)
        self.stt = SpeechToText(
            recognizer=self.recognizer, capturing=self.capturing, agc_target_rms=AGC_TARGET_RMS,
            stop_wake_stream=self.wake.stop_stream, recover_wake_stream=self.wake.recover_stream,
            log=self.log, offline_enabled=OFFLINE_FALLBACK_ENABLED, offline_model_size=OFFLINE_STT_MODEL,
            offline_model_dir=OFFLINE_STT_MODEL_DIR)
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

        # Capability registry (see _build_registry docstring below) - built
        # once per instance so its handler closures capture this instance's
        # `self`, not looked up freshly by name on every command.
        self._registry = self._build_registry()

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
            self.audit.record('close_app', target, 'failed', reason='unknown_app')
            self.speak(f"I don't know how to close {target} yet.")
            return
        closed = False
        for p in psutil.process_iter(['name']):
            try:
                if (p.info['name'] or '').lower() == exe.lower():
                    p.terminate()
                    closed = True
            except psutil.Error: pass
        if closed:
            self.audit.record('close_app', target, 'executed')
        else:
            self.audit.record('close_app', target, 'failed', reason='not_running')
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
        self.audit.record('close_all', 'all_non_protected_processes', 'executed', count=count)
        self.speak(f'Closed {count} applications.')

    def system_shutdown(self):
        self.speak('Shutting down the system. See you soon Boss.')
        self.audit.record('shutdown', 'system', 'executed')
        subprocess.Popen('shutdown /s /t 5', shell=True)

    def system_restart(self):
        self.speak('Restarting the system now Boss.')
        self.audit.record('restart', 'system', 'executed')
        subprocess.Popen('shutdown /r /t 5', shell=True)

    def lock_system(self):
        # No confirmation needed, unlike shutdown/restart/close-all: locking is
        # trivially reversible (just log back in) and loses no unsaved work.
        self.speak('Locking the system now Boss.')
        subprocess.Popen('rundll32.exe user32.dll,LockWorkStation', shell=True)

    # ---------- file operations (files.py owns path resolution/safety) ----------
    # list/search are read-only, so they run immediately with no confirmation
    # and no audit entry (audit.py is scoped to consequential actions - see
    # its module docstring). delete/move/rename can destroy or overwrite an
    # existing file, so - exactly like shutdown/restart/close-all above - they
    # go through self.awaiting_confirmation and only actually run from
    # handle_confirmation once the user says yes. copy is immediate: it can't
    # destroy anything that existed before (files.py's move_file/copy_file
    # both refuse to overwrite an existing destination file outright, so
    # copy's only previously-risky outcome is already a hard error, not a
    # silent surprise), so gating it behind a spoken confirmation would add
    # friction without closing a real safety gap.

    def _do_delete_file(self, path):
        try:
            fileops.delete_file(path)
            self.audit.record('delete_file', path, 'executed')
            self.speak(f'Deleted {os.path.basename(path)}. It went to the Recycle Bin.')
        except fileops.PathNotAllowedError:
            self.audit.record('delete_file', path, 'failed', reason='path_not_allowed')
            self.speak("I can't delete that - it's outside the folders I'm allowed to touch.")
        except OSError as e:
            self.audit.record('delete_file', path, 'failed', reason=str(e))
            self.speak(f"I couldn't delete that file: {e}")

    def _do_move_file(self, path, dest_dir):
        try:
            dest = fileops.move_file(path, dest_dir)
            self.audit.record('move_file', path, 'executed', dest=str(dest))
            self.speak(f'Moved {os.path.basename(path)}.')
        except FileExistsError:
            self.audit.record('move_file', path, 'failed', reason='destination_exists')
            self.speak("A file with that name already exists there. I won't overwrite it.")
        except fileops.PathNotAllowedError:
            self.audit.record('move_file', path, 'failed', reason='path_not_allowed')
            self.speak("I can't move that - it's outside the folders I'm allowed to touch.")
        except OSError as e:
            self.audit.record('move_file', path, 'failed', reason=str(e))
            self.speak(f"I couldn't move that file: {e}")

    def _do_rename_file(self, path, new_name):
        try:
            dest = fileops.rename_file(path, new_name)
            self.audit.record('rename_file', path, 'executed', new_name=dest.name)
            self.speak(f'Renamed it to {dest.name}.')
        except FileExistsError:
            self.audit.record('rename_file', path, 'failed', reason='destination_exists')
            self.speak("A file with that name already exists there. I won't overwrite it.")
        except fileops.PathNotAllowedError:
            self.audit.record('rename_file', path, 'failed', reason='path_not_allowed')
            self.speak("I can't rename that - it's outside the folders I'm allowed to touch.")
        except OSError as e:
            self.audit.record('rename_file', path, 'failed', reason=str(e))
            self.speak(f"I couldn't rename that file: {e}")

    def handle_confirmation(self, cmd):
        if not self.awaiting_confirmation:
            return False
        pending = self.awaiting_confirmation
        action = pending['action']
        if 'yes' in cmd:
            self.awaiting_confirmation = None
            if action == 'close_all': self.close_all_apps()
            elif action == 'shutdown': self.system_shutdown()
            elif action == 'restart': self.system_restart()
            elif action == 'delete_file': self._do_delete_file(pending['path'])
            elif action == 'move_file': self._do_move_file(pending['path'], pending['dest_dir'])
            elif action == 'rename_file': self._do_rename_file(pending['path'], pending['new_name'])
        else:
            self.awaiting_confirmation = None
            self.audit.record(action, pending.get('path', action), 'declined')
            self.speak('Action cancelled, Boss.')
        return True

    # ---------- capability registry ----------
    # Dispatch infrastructure only, NOT authorization/policy: this decides
    # which handler a command routes to, replacing what used to be one long
    # if/elif chain in execute() that mixed classification (which pattern
    # matches) with dispatch (what runs) in the same block. It does not add
    # or change any permission check - destructive actions are still exactly
    # as gated (or not) as they were before; `destructive` here is
    # descriptive metadata for readability/future tooling, not an enforced
    # gate. Every matcher/handler pair below is a direct, behavior-preserving
    # translation of the original if/elif chain (verified via
    # tests/unit/test_registry.py asserting each pattern still reaches the
    # same handler) - the *order* below is exactly the original order,
    # since several matchers are intentionally checked before broader ones
    # (documented inline, same as before this refactor).

    def _build_registry(self):
        def literal(*substrings):
            return lambda cmd: any(s in cmd for s in substrings)

        def search(pattern):
            compiled = re.compile(pattern)
            return lambda cmd: compiled.search(cmd)

        def match(pattern):
            compiled = re.compile(pattern)
            return lambda cmd: compiled.match(cmd)

        youtube_patterns = [
            re.compile(r'(?:open |go to )?youtube(?: and)? (?:play|search for|find|watch) (.+)'),
            re.compile(r'play (.+) on youtube'),
            re.compile(r'search youtube for (.+)'),
        ]

        def match_youtube(cmd):
            for p in youtube_patterns:
                m = p.match(cmd)
                if m:
                    return m
            return None

        def h_shutdown_vortex(cmd, m):
            self.speak('Shutting down. See you soon Boss.')
            self.stop()

        def h_time(cmd, m):
            self.speak(f"The current time is {datetime.datetime.now().strftime('%I:%M %p')}")

        def h_date(cmd, m):
            self.speak(f"Today's date is {datetime.datetime.now().strftime('%d %B %Y')}")

        def h_close_all_prompt(cmd, m):
            self.awaiting_confirmation = {'action': 'close_all'}
            self.audit.record('close_all', 'all_non_protected_processes', 'prompted')
            self.speak('This may close unsaved work. Should I proceed?')

        def h_restart_prompt(cmd, m):
            self.awaiting_confirmation = {'action': 'restart'}
            self.audit.record('restart', 'system', 'prompted')
            self.speak('Should I restart the system now Boss?')

        def h_shutdown_prompt(cmd, m):
            self.awaiting_confirmation = {'action': 'shutdown'}
            self.audit.record('shutdown', 'system', 'prompted')
            self.speak('Should I shut down the system now Boss?')

        def h_lock(cmd, m):
            self.lock_system()

        def h_close_browser(cmd, m):
            self.browser.close()
            self.speak('Closed the browser.')

        def h_read_page(cmd, m):
            self.speak(self.browser.read_page())

        def h_youtube(cmd, m):
            self.speak(self.browser.play_youtube(m.group(1).strip()))

        def h_web_search(cmd, m):
            self.speak(self.browser.search(m.group(1).strip()))

        def h_browse(cmd, m):
            self.speak(self.browser.open(m.group(1).strip()))

        def h_click(cmd, m):
            self.speak(self.browser.click_text(m.group(1).strip()))

        # ---- file operations / search (Phase 2, 2026-08-16) ----
        # "search for file(s)..."/"find file..." is checked before the
        # generic web-search entry above's pattern (`search(?: the web)? for
        # (.+)`), which would otherwise swallow "search for files containing
        # report" whole and run a web search for "files containing report"
        # instead of a local filename search - same reasoning the file's
        # existing YouTube-before-generic-search ordering already documents.

        def h_search_files(cmd, m):
            query = m.group(1).strip()
            matches = fileops.search_files(query)
            if not matches:
                self.speak(f"I couldn't find any files matching {query}.")
                return
            names = ', '.join(p.name for p in matches[:5])
            plural = 's' if len(matches) != 1 else ''
            self.speak(f'I found {len(matches)} matching file{plural}: {names}.')

        def h_list_files(cmd, m):
            dir_name = m.group(1).strip() if m.group(1) else None
            names, err = fileops.list_files(dir_name)
            if err:
                self.speak(err)
                return
            if not names:
                self.speak('No files found.')
                return
            shown = ', '.join(names[:10])
            more = ', and more.' if len(names) > 10 else '.'
            plural = 's' if len(names) != 1 else ''
            self.speak(f'Found {len(names)} file{plural}: {shown}{more}')

        def h_delete_file(cmd, m):
            name = m.group(1).strip()
            path = fileops.resolve_file(name)
            if not path:
                self.speak(f"I couldn't find a file called {name}.")
                return
            self.awaiting_confirmation = {'action': 'delete_file', 'path': str(path)}
            self.audit.record('delete_file', str(path), 'prompted')
            self.speak(f'This will move {path.name} to the Recycle Bin. Should I proceed?')

        def h_move_file(cmd, m):
            name, dest_name = m.group(1).strip(), m.group(2).strip()
            path = fileops.resolve_file(name)
            if not path:
                self.speak(f"I couldn't find a file called {name}.")
                return
            dest_dir = fileops.resolve_dir(dest_name)
            if not dest_dir:
                self.speak(f"I can only move files between Desktop, Documents, and Downloads - not {dest_name}.")
                return
            self.awaiting_confirmation = {'action': 'move_file', 'path': str(path), 'dest_dir': str(dest_dir)}
            self.audit.record('move_file', str(path), 'prompted', dest=str(dest_dir))
            self.speak(f'This will move {path.name} to {dest_name}. Should I proceed?')

        def h_copy_file(cmd, m):
            name, dest_name = m.group(1).strip(), m.group(2).strip()
            path = fileops.resolve_file(name)
            if not path:
                self.speak(f"I couldn't find a file called {name}.")
                return
            dest_dir = fileops.resolve_dir(dest_name)
            if not dest_dir:
                self.speak(f"I can only copy files between Desktop, Documents, and Downloads - not {dest_name}.")
                return
            try:
                dest = fileops.copy_file(path, dest_dir)
                self.audit.record('copy_file', str(path), 'executed', dest=str(dest))
                self.speak(f'Copied {path.name} to {dest_name}.')
            except FileExistsError:
                self.audit.record('copy_file', str(path), 'failed', reason='destination_exists')
                self.speak(f"A file named {path.name} already exists in {dest_name}. I won't overwrite it.")
            except fileops.PathNotAllowedError:
                self.audit.record('copy_file', str(path), 'failed', reason='path_not_allowed')
                self.speak("I can't copy that - it's outside the folders I'm allowed to touch.")
            except OSError as e:
                self.audit.record('copy_file', str(path), 'failed', reason=str(e))
                self.speak(f"I couldn't copy that file: {e}")

        def h_rename_file(cmd, m):
            name, new_name = m.group(1).strip(), m.group(2).strip()
            path = fileops.resolve_file(name)
            if not path:
                self.speak(f"I couldn't find a file called {name}.")
                return
            self.awaiting_confirmation = {'action': 'rename_file', 'path': str(path), 'new_name': new_name}
            self.audit.record('rename_file', str(path), 'prompted', new_name=new_name)
            self.speak(f'This will rename {path.name} to {new_name}. Should I proceed?')

        def h_close_app(cmd, m):
            self.close_named_app(m.group(1).strip())

        def h_open(cmd, m):
            self.open_target(m.group(1).strip())

        def h_document_question(cmd, m):
            self.answer_document_question(m.group(1).strip(), m.group(2).strip())

        def h_summarize(cmd, m):
            self.summarize_document(m.group(1).strip())

        def h_read_document(cmd, m):
            self.summarize_document(m.group(1).strip())

        # Order matters: this is a straight-line translation of the original
        # if/elif chain in execute(), same order, same "checked before the
        # broader pattern below" comments preserved from the original.
        return [
            {'name': 'shutdown_vortex', 'matcher': literal('shutdown vortex'), 'handler': h_shutdown_vortex,
             'destructive': True, 'description': 'Stop the VORTEX process itself.'},
            {'name': 'time', 'matcher': search(r'\btime\b'), 'handler': h_time,
             'destructive': False, 'description': 'Speak the current time.'},
            {'name': 'date', 'matcher': search(r'\bdate\b'), 'handler': h_date,
             'destructive': False, 'description': 'Speak today\'s date.'},
            {'name': 'close_all_prompt', 'matcher': literal('close all'), 'handler': h_close_all_prompt,
             'destructive': True, 'description': 'Prompt to close every non-protected running app.'},
            {'name': 'restart_prompt', 'matcher': literal('restart system', 'reboot system'),
             'handler': h_restart_prompt, 'destructive': True, 'description': 'Prompt to restart the system.'},
            {'name': 'shutdown_prompt', 'matcher': literal('shutdown system'), 'handler': h_shutdown_prompt,
             'destructive': True, 'description': 'Prompt to shut down the system.'},
            {'name': 'lock', 'matcher': search(r'\block\b.*\b(?:system|computer|screen|pc)\b'), 'handler': h_lock,
             'destructive': False, 'description': 'Lock the workstation (no confirmation - trivially reversible).'},
            # Browser commands are checked before the generic close/open/read
            # patterns below so "close browser" / "read the page" don't get misrouted.
            {'name': 'close_browser', 'matcher': literal('close browser', 'quit browser'),
             'handler': h_close_browser, 'destructive': False, 'description': 'Close the automated browser session.'},
            {'name': 'read_page', 'matcher': search(r"(?:read|what'?s on) (?:the |this )?page"),
             'handler': h_read_page, 'destructive': False, 'description': 'Read back the current browser page.'},
            # YouTube search-and-play is checked before the generic "open (.+)"
            # pattern below, which used to swallow phrases like "open youtube and
            # play X" whole and fall through to a dumb literal-phrase web search
            # (see IMPLEMENTED.md).
            {'name': 'youtube', 'matcher': match_youtube, 'handler': h_youtube,
             'destructive': False, 'description': 'Search and play a YouTube video.'},
            # File search is checked before the generic web-search pattern below -
            # see the comment above h_search_files.
            {'name': 'search_files', 'matcher': match(r'(?:find|search for) files?(?: named| called| containing)? (.+)'),
             'handler': h_search_files, 'destructive': False,
             'description': 'Find files by name in Desktop/Documents/Downloads.'},
            {'name': 'web_search', 'matcher': match(r'(?:search(?: the web)? for|google) (.+)'),
             'handler': h_web_search, 'destructive': False, 'description': 'Search the web.'},
            {'name': 'browse', 'matcher': match(r'(?:go to|browse to|browse) (.+)'), 'handler': h_browse,
             'destructive': False, 'description': 'Navigate the browser to a URL or site name.'},
            {'name': 'click', 'matcher': match(r'click (?:on )?(.+)'), 'handler': h_click,
             'destructive': False, 'description': 'Click matching text on the current page.'},
            {'name': 'list_files', 'matcher': match(r'list files?(?: (?:in|on) (.+))?$'), 'handler': h_list_files,
             'destructive': False, 'description': 'List files in Desktop/Documents/Downloads.'},
            {'name': 'delete_file_prompt', 'matcher': match(r'delete file (.+)'), 'handler': h_delete_file,
             'destructive': True, 'description': 'Prompt to delete a file (Recycle Bin, not permanent).'},
            {'name': 'move_file_prompt', 'matcher': match(r'move file (.+) to (.+)'), 'handler': h_move_file,
             'destructive': True, 'description': 'Prompt to move a file between the allowed directories.'},
            {'name': 'copy_file', 'matcher': match(r'copy file (.+) to (.+)'), 'handler': h_copy_file,
             'destructive': False, 'description': 'Copy a file between the allowed directories (no overwrite).'},
            {'name': 'rename_file_prompt', 'matcher': match(r'rename file (.+) to (.+)'), 'handler': h_rename_file,
             'destructive': True, 'description': 'Prompt to rename a file in place.'},
            {'name': 'close_app', 'matcher': match(r'close (.+)'), 'handler': h_close_app,
             'destructive': True, 'description': 'Close one named running app.'},
            {'name': 'open', 'matcher': match(r'open (.+)'), 'handler': h_open,
             'destructive': False, 'description': 'Open a named app, web app, or web search.'},
            # Document commands, checked after the app-launching "open (.+)"
            # pattern so "open chrome" still launches an app rather than
            # looking for a file.
            {'name': 'document_question', 'matcher': match(r'what does (.+?) say about (.+)'),
             'handler': h_document_question, 'destructive': False, 'description': 'Answer a question about a document.'},
            {'name': 'summarize', 'matcher': match(r'(?:summarize|summarise) (.+)'), 'handler': h_summarize,
             'destructive': False, 'description': 'Summarize a document.'},
            {'name': 'read_document', 'matcher': match(r'read (?:me )?(.+)'), 'handler': h_read_document,
             'destructive': False, 'description': 'Read (summarize) a document.'},
        ]

    def execute(self, cmd):
        if not cmd:
            self.speak('I did not catch that Boss.')
            return
        if self.handle_confirmation(cmd):
            return
        for entry in self._registry:
            m = entry['matcher'](cmd)
            if m:
                entry['handler'](cmd, m)
                return
        # No registered capability matched - fall through to the LLM. Not a
        # registry entry itself: this is the default reasoning path, not a
        # registered capability with its own trigger phrase.
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
        time you actually speak.

        Deliberately does NOT eagerly warm the offline STT/TTS fallback models
        (faster-whisper/piper-tts) anymore - live testing on 2026-08-17 found
        the wake model, run in isolation on a real captured utterance, scored
        meaningfully higher (0.65) than the same audio scored inside the live
        process (near zero) - real evidence of resource contention, and the
        offline models' background thread pools (ctranslate2, onnxruntime,
        alongside openWakeWord's own onnxruntime session, all competing for
        the same 4 physical cores) are a plausible contributor given they're
        now permanently resident once loaded. Wake/barge-in responsiveness is
        this project's constant, everyday priority; offline fallback exists
        for a network outage that hasn't actually happened once in extensive
        testing. Both engines still lazy-load correctly on first real use
        (stt.py/tts.py's _get_offline_model/_get_offline_voice, unchanged) -
        this only removes the *eager* load, trading a few extra seconds on
        the first fallback during a genuine future outage for a lighter,
        more responsive process the rest of the time."""
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
