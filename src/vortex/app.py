# VORTEX Vortex application class - custom wake word + barge-in + multi-turn
# session build. Windows + ONNX-only wake architecture.
# docs/REFACTOR_PLAN.md Step 8: this file is what used to be main.py's whole
# contents; main.py itself is now purely the bootstrap (`from .app import
# Vortex; Vortex().start()`), per the plan's exit criteria for this step.
import datetime
import logging
import os
import queue
import threading

import pygame
import speech_recognition as sr
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
from .voice.tts import TextToSpeech
from .voice.stt import SpeechToText
from .voice.session import Session
from .llm.ollama_provider import OllamaProvider
from .platform.windows.power import WindowsPlatformAdapter
from .platform.windows.apps import NATIVE_APPS, WEB_APPS
from .platform.windows.protected_processes import PROTECTED_PROCESSES
from .tools.system import apps as system_apps
from .tools.system import process as system_process
from .core import intent_router
from .core.capability_registry import CapabilityRegistry
from .core.policy_engine import is_affirmative
from .core.orchestrator import Orchestrator
from .core.state_manager import current_state

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


class Vortex:
    def __init__(self):
        self.running = True
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
        # docs/REFACTOR_PLAN.md Step 4: Ollama specifics live behind
        # LLMProvider now, not inline in ask_llm_stream/_stream_llm_answer.
        self.llm = OllamaProvider(model=MODEL, max_tokens=LLM_MAX_TOKENS, barge_in=self.barge_in,
                                   is_running=lambda: self.running)
        # Callables below are lambdas closing over `self` and calling back
        # through Vortex's own (dynamically-dispatched) methods, not pre-bound
        # references captured once - so instance-level monkeypatching of
        # v.speak / v.capture_command / v.execute / v.greet (as
        # tools/test_barge_in.py's worker-dispatch scenario does) is still
        # picked up at call time, exactly as it was before this extraction.
        self.session = Session(
            events=self.events, barge_in=self.barge_in, session_timeout=SESSION_TIMEOUT,
            wake_watchdog_timeout=WAKE_WATCHDOG_TIMEOUT,
            capture_command=lambda timeout=8, allow_offline_on_unclear=True: self.capture_command(
                timeout=timeout, allow_offline_on_unclear=allow_offline_on_unclear),
            execute=lambda cmd: self.execute(cmd),
            speak=lambda text: self.speak(text),
            greet=lambda: self.greet(),
            warm_up=lambda: self._warm_up_models(),
            get_last_audio_at=lambda: self.wake.last_audio_at,
            recover_wake_stream=self.wake.recover_stream,
            is_capturing=self.capturing.is_set,
            clear_awaiting_confirmation=self._clear_awaiting_confirmation,
            log=self.log, is_running=lambda: self.running)

        # docs/REFACTOR_PLAN.md Step 5: the *what* (open/close/shutdown) lives
        # in tools/system/* and this class; the *how* (Windows exe/URL tables,
        # the protected-process denylist, the shutdown/restart/lock commands)
        # lives in platform/windows/* - moved as-is except protected_processes,
        # expanded per the security-review gap in docs/CURRENT_STATE.md §6.
        self.protected = PROTECTED_PROCESSES
        self.native_apps = NATIVE_APPS
        self.web_apps = WEB_APPS
        self.platform = WindowsPlatformAdapter()

        # docs/REFACTOR_PLAN.md Step 6: classification (core/intent_router.py,
        # a pure text -> Intent function, no `self` needed) and dispatch
        # (core/capability_registry.py, built once per instance so its
        # handlers reach back into this instance) now live in their own
        # modules instead of one fused matcher+handler chain in main.py.
        self._registry = CapabilityRegistry(self)

        # docs/REFACTOR_PLAN.md Step 7: process lifecycle (tray icon, worker
        # thread, clean teardown) - what start()/stop()/shutdown() shrink
        # down to.
        self.orchestrator = Orchestrator(self)

    def log(self, msg):
        logging.info(msg)

    def _clear_awaiting_confirmation(self):
        self.awaiting_confirmation = None

    # ---------- speaking/stop_speaking: back-compat passthroughs ----------
    # Kept as properties (not moved-and-gone) because they're checked/set from
    # several places still in this file (execute()'s callers, tray callbacks,
    # ask_llm_stream/_stream_llm_answer's barge-in-aware LLM streaming below,
    # and llm/ollama_provider.py via the injected barge_in object) and by
    # external callers (tools/test_barge_in.py) that predate this extraction -
    # same two Event objects, now owned by self.barge_in.

    @property
    def speaking(self):
        return self.barge_in.speaking

    @property
    def stop_speaking(self):
        return self.barge_in.stop_speaking

    @property
    def state(self):
        """docs/REFACTOR_PLAN.md Step 7: explicit VortexState (STANDBY/
        ACTIVE_SESSION/SPEAKING), computed fresh on every read from the same
        Events that already drive actual behavior - see
        core/state_manager.py for why this is a read-only view, not a new
        source of truth."""
        return current_state(is_speaking=self.barge_in.speaking.is_set,
                              is_in_active_session=self.session.in_active_session.is_set)

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

    def capture_command(self, timeout=8, allow_offline_on_unclear=True):
        return self.stt.capture_command(timeout=timeout, allow_offline_on_unclear=allow_offline_on_unclear)

    # ---------- reasoning (Ollama specifics live in llm/ollama_provider.py,
    # docs/REFACTOR_PLAN.md Step 4) ----------

    def ask_llm_stream(self, query):
        """Yield reply text as Ollama produces it, so speech can start early and
        generation stops the moment we are interrupted."""
        self.memory.add_turn('user', query)
        messages = [{'role': 'system', 'content': SYSTEM_PROMPT}] + self.memory.recent(HISTORY_TURNS)
        reply = ''
        try:
            stream = self.llm.chat_stream(messages)
        except Exception as e:
            self.log(f'LLM error: {e}')
            yield 'Sorry Boss, my reasoning engine is currently offline.'
            return
        try:
            for token in stream:
                reply += token
                yield token
        except Exception as e:
            self.log(f'LLM stream error: {e}')
            if not reply:
                yield 'Sorry Boss, my reasoning engine is currently offline.'
        finally:
            if reply:
                self.memory.add_turn('assistant', reply)

    # ---------- documents ----------

    def _stream_llm_answer(self, system_prompt, user_content):
        """Shared streaming/barge-in-friendly pattern: one system+user message
        in, tokens out, stoppable mid-generation exactly like ask_llm_stream."""
        messages = [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_content}]
        try:
            stream = self.llm.chat_stream(messages)
        except Exception as e:
            self.log(f'Document LLM error: {e}')
            yield 'Sorry Boss, my reasoning engine is currently offline.'
            return
        try:
            yield from stream
        except Exception as e:
            self.log(f'Document LLM stream error: {e}')

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
        """Delegates to tools/system/apps.py (docs/REFACTOR_PLAN.md Step 5) -
        same native-app-first, then web-app, then browser-search fallback."""
        self.speak(system_apps.open_target(target, self.native_apps, self.web_apps, self.browser))
        return True

    def close_named_app(self, target):
        self.speak(system_process.close_named_app(target, self.native_apps, self.audit))

    def close_all_apps(self):
        self.speak(system_process.close_all_apps(self.current_pid, self.protected, self.audit))

    def system_shutdown(self):
        self.speak('Shutting down the system. See you soon Boss.')
        self.audit.record('shutdown', 'system', 'executed')
        self.platform.shutdown()

    def system_restart(self):
        self.speak('Restarting the system now Boss.')
        self.audit.record('restart', 'system', 'executed')
        self.platform.restart()

    def lock_system(self):
        # No confirmation needed, unlike shutdown/restart/close-all: locking is
        # trivially reversible (just log back in) and loses no unsaved work.
        self.speak('Locking the system now Boss.')
        self.platform.lock()

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
        if is_affirmative(cmd):
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

    # docs/REFACTOR_PLAN.md Step 6: classification (core/intent_router.py)
    # and dispatch (core/capability_registry.py) now live in their own
    # modules, built once in __init__ as self._registry (a
    # CapabilityRegistry, not a raw list anymore).

    def execute(self, cmd):
        if not cmd:
            self.speak('I did not catch that Boss.')
            return
        if self.handle_confirmation(cmd):
            return
        intent = intent_router.route(cmd)
        if isinstance(intent, intent_router.Unhandled):
            # No capability matched - fall through to the LLM. Not a
            # registered capability itself: this is the default reasoning
            # path, not a capability with its own trigger phrase.
            self.speak_stream(self.ask_llm_stream(cmd))
            return
        self._registry.dispatch(intent)

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

        Offline STT (faster-whisper) IS eagerly warmed here; offline TTS
        (piper-tts) deliberately is NOT. These used to be symmetric (both
        eager, then both lazy - see CHANGELOG.md 2026-08-17) but stopped
        being equivalent once STT's offline fallback trigger was broadened
        the same day: it used to only engage on a real network failure
        (rare - hasn't happened once in extensive testing), but real evidence
        (a live-captured, genuinely-failed clip that Google's cloud STT
        couldn't parse but faster-whisper transcribed correctly, language
        probability 1.0) showed cloud STT is measurably less reliable than
        the "fallback" model for this project's AGC-boosted audio profile -
        so STT's offline model now also engages on sr.UnknownValueError,
        which is common, not rare. Leaving it lazy would mean the first
        capture failure in every session pays a 3.8-8s cold-load penalty on
        top of already having failed once - worse, not better, for the exact
        responsiveness this change is trying to protect. TTS's offline
        fallback trigger is unchanged (network failure only, still rare), so
        it stays lazy - no evidence yet that eagerly loading it is worth its
        share of the ~200MB combined memory cost measured on 2026-08-17."""
        self.stt.ensure_offline_ready()
        try:
            ollama.chat(model=MODEL, messages=[{'role': 'user', 'content': 'hi'}], keep_alive='30m')
        except Exception as e:
            self.log(f'Model warm-up failed (LLM): {e}')
        if self.rag is not None:
            try:
                ollama.embeddings(model=_cfg.embed_model, prompt='warm up', keep_alive='30m')
            except Exception as e:
                self.log(f'Model warm-up failed (embeddings): {e}')

    # ---------- process lifecycle (core/orchestrator.py owns the actual
    # logic, docs/REFACTOR_PLAN.md Step 7) ----------

    def request_stop_speaking(self, icon=None, item=None):
        self.orchestrator.request_stop_speaking(icon, item)

    def listen_now(self, icon=None, item=None):
        self.orchestrator.listen_now(icon, item)

    def stop(self, icon=None, item=None):
        self.orchestrator.stop(icon, item)

    def tray_exit(self, icon, item):
        self.orchestrator.tray_exit(icon, item)

    def shutdown(self):
        self.orchestrator.shutdown()

    def start(self):
        self.orchestrator.run()
