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
import time

import pygame
import speech_recognition as sr
from dotenv import load_dotenv
import ollama

from .memory import MemoryStore
from .documents import resolve_document, extract_text, build_document_prompt
from .browser import BrowserAgent
from .mail import MailAgent
from .rag import RagStore, build_rag_prompt, build_memory_prompt
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
from .llm.tools import TOOL_SCHEMAS, tool_call_to_intent
from .platform.windows.power import WindowsPlatformAdapter
from .platform.windows.apps import NATIVE_APPS, WEB_APPS
from .platform.windows.protected_processes import PROTECTED_PROCESSES
from .tools.system import apps as system_apps
from .tools.system import process as system_process
from .core import intent_router
from .core import personality
from .core import social_context
from .core import self_knowledge
from .core.owner_context import OwnerContext
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
# Standby/Activation/Personality foundation (2026-08-20).
ACTIVATION_RESPONSE = _cfg.activation_response
BARGE_IN_RESPONSE = _cfg.barge_in_response
PERSONALITY_MODE_DEFAULT = personality.PersonalityMode(_cfg.personality_mode)
DEMO_SEGMENT_PAUSE = _cfg.demo_segment_pause
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
GMAIL_CREDENTIALS_PATH = _cfg.gmail_credentials_path
GMAIL_TOKEN_PATH = _cfg.gmail_token_path
MAIL_MAX_RESULTS = _cfg.mail_max_results
LLM_TOOL_CALLING_ENABLED = _cfg.llm_tool_calling_enabled
logging.basicConfig(filename=os.path.join(LOG_DIR, 'vortex.log'), level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
try:
    pygame.mixer.init()
except pygame.error as e:
    # A machine with no real audio output device (a CI runner, a headless
    # test environment) raises here - degrade instead of making merely
    # *importing* this module impossible everywhere except a real desktop
    # with speakers. Nothing in the test suite drives real TTS playback
    # (every test mocks speak/speak_stream), so this only matters for
    # import-time collection, not for any currently-tested playback path.
    logging.warning(f'pygame.mixer.init() failed, TTS playback will be unavailable: {e}')


class Vortex:
    def __init__(self):
        self.running = True
        self.recognizer = sr.Recognizer()
        self.current_pid = os.getpid()
        self.awaiting_confirmation = None
        # Personality/Owner-Context foundation (2026-08-20): personality_mode
        # is mutable runtime state (switched by the "switch to X mode" voice
        # command, see core/capability_registry.py's _set_personality_mode) -
        # config.py's personality_mode only supplies the starting value, the
        # same relationship it has with e.g. awaiting_confirmation above.
        self.personality_mode = PERSONALITY_MODE_DEFAULT
        # Thin, single-owner identity - see core/owner_context.py's module
        # docstring for why session_state/personality_mode are live
        # properties here, not copied fields. owner_id is a fixed constant:
        # no voice enrollment/speaker ID exists or is attempted.
        self.owner = OwnerContext(
            owner_id='primary_owner', display_name=USER_NAME, preferred_address=USER_NAME,
            get_session_state=lambda: self.state, get_personality_mode=lambda: self.personality_mode)
        self._executing = threading.Event()
        self.audit = AuditLog(AUDIT_LOG_PATH)
        self.memory = MemoryStore(MEMORY_DB_PATH)
        self.browser = BrowserAgent()
        # Lazy exactly like BrowserAgent above - zero network/OAuth calls at
        # construction, so this stays as safe to construct as everything else
        # here (see mail.py's module docstring for why that matters).
        self.mail = MailAgent(credentials_path=GMAIL_CREDENTIALS_PATH, token_path=GMAIL_TOKEN_PATH,
                               max_results=MAIL_MAX_RESULTS)
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
            log=self.log, is_running=lambda: self.running,
            activation_response=ACTIVATION_RESPONSE, barge_in_response=BARGE_IN_RESPONSE)

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
        ACTIVE_SESSION/SPEAKING, plus EXECUTING added 2026-08-20), computed
        fresh on every read from the same Events that already drive actual
        behavior - see core/state_manager.py for why this is a read-only
        view, not a new source of truth."""
        return current_state(is_speaking=self.barge_in.speaking.is_set,
                              is_in_active_session=self.session.in_active_session.is_set,
                              is_executing=self._executing.is_set)

    @property
    def presentation_mode(self):
        """Presentation/Demo Mode foundation (2026-08-20) - derived, not an
        independently-settable second flag: presentation mode IS demo
        personality mode, switched the same way ("switch to demo mode"), so
        there's no second toggle that can drift out of sync with it for no
        real benefit. Extension point only for now - no current code path
        conditionally exposes internal diagnostics based on this (logs only
        ever go to vortex.log, never spoken), so this is honestly a real,
        queryable flag with exactly one live effect today (personality.py's
        DEMO directive) plus a documented seam for future diagnostics-
        surfacing code to check before speaking/showing anything internal."""
        return self.personality_mode == personality.PersonalityMode.DEMO

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
        user_turn_id = self.memory.add_turn('user', query)
        self._index_turn_async(user_turn_id, 'user', query)
        # Personality/Social-context foundation (2026-08-20): the one real
        # integration point for both - see core/personality.py's module
        # docstring for the full pipeline this implements (Conversation
        # Context -> Intent -> Personality Policy -> Response Generation).
        # social_context.classify() is intentionally simple/rule-based (see
        # its own docstring) - a future, genuinely intelligent classifier
        # can replace it without this call site changing.
        social_label = social_context.classify(query)
        system_prompt = personality.build_system_prompt(SYSTEM_PROMPT, self.personality_mode, social_label)
        messages = [{'role': 'system', 'content': system_prompt}] + self.memory.recent(HISTORY_TURNS)
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
                assistant_turn_id = self.memory.add_turn('assistant', reply)
                self._index_turn_async(assistant_turn_id, 'assistant', reply)

    def _index_turn_async(self, turn_id, role, content):
        """Fire-and-forget: embedding + a Qdrant upsert on every conversation
        turn would add real latency to the hot conversational path if done
        synchronously here (this runs inside ask_llm_stream, on the same
        thread TTS is already consuming from). A background thread means a
        slow or failed index never delays or breaks the actual spoken
        response - SQLite (memory.py) is already the durable, authoritative
        record by the time this is even called; Qdrant here is purely a
        similarity index on top of it, safe to be eventually-consistent or
        even to fail outright without losing anything real."""
        if self.rag is None:
            return

        def _run():
            try:
                self.rag.index_turn(turn_id, role, content)
            except Exception as e:
                self.log(f'Failed to index conversation turn {turn_id} for retrieval: {e}')
        threading.Thread(target=_run, daemon=True).start()

    def recall_memory(self, query):
        """Retrieval-backed answer over past conversation turns - the
        "Memory" half of Phase 5 that memory.py's own docstring flagged as
        deliberately not built yet ("still no retrieval over it, just
        chronological recall"). Degrades to a clear spoken explanation, not
        a crash or a silent no-op, if the retrieval service isn't running -
        same honest contract as document RAG's own degrade path."""
        if self.rag is None:
            self.speak("I can't search past conversations right now - that needs Postgres and Qdrant running.")
            return
        try:
            turns = self.rag.search_turns(query)
        except Exception as e:
            self.log(f'Memory search failed: {e}')
            self.speak("I couldn't search my memory right now.")
            return
        if not turns:
            self.speak("I don't have anything relevant in memory about that.")
            return
        prompt = build_memory_prompt(turns, query)
        self.speak_stream(self._stream_llm_answer(
            'Answer strictly using the provided conversation excerpts. If they do not contain '
            'the answer, say so plainly. Answer in short spoken sentences.', prompt))

    def demonstrate_self(self):
        """The actual content behind "VORTEX, demonstrate yourself" - not
        just switching to Demo personality mode (that alone only changes the
        *tone* of future answers). Spoken directly, not via the LLM - see
        core/self_knowledge.py's module docstring for the live-tested reason
        (llama3.2:1b could not reliably synthesize multiple real facts into
        one coherent answer; a deterministic, complete, hand-composed
        introduction beat an unreliable LLM-narrated one for a request that
        explicitly needs to cover several topics).

        Spoken as separate topic segments with a real pause between them,
        not one continuous utterance - see build_demo_segments()'s docstring
        for the barge-in problem this addresses. Stops between segments
        (not just mid-segment, which self.speak() already handles) the
        moment a barge-in has been registered, rather than plowing through
        every remaining topic regardless."""
        for segment in self_knowledge.build_demo_segments(self.memory.stats()):
            if self.stop_speaking.is_set():
                return
            self.speak(segment)
            time.sleep(DEMO_SEGMENT_PAUSE)

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

    # ---------- email (mail.py owns the Gmail API details) ----------

    def draft_email_reply(self, original_body, instruction):
        """Blocking, not streamed to speech - unlike every other
        _stream_llm_answer caller, the reply-drafting flow needs the
        complete text before speaking it (so the user hears the whole
        draft, not a truncated stream) and before it can be stored in
        awaiting_confirmation for the later, explicit send."""
        prompt = f"Original email:\n{original_body[:2000]}\n\nInstruction for the reply: {instruction}"
        return ''.join(self._stream_llm_answer(
            'You draft short, polite email replies. Output only the reply body text - '
            'no subject line, no salutation boilerplate beyond what reads naturally, '
            'no explanation of what you did.',
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

    def _do_send_email_reply(self, pending):
        try:
            self.mail.send_reply(pending['message_id'], pending['body'])
            self.audit.record('send_email_reply', pending['message_id'], 'executed', to=pending.get('to'))
            self.speak('Sent.')
        except Exception as e:
            self.audit.record('send_email_reply', pending['message_id'], 'failed', reason=str(e))
            self.speak("I couldn't send that reply.")

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
            elif action == 'send_email_reply': self._do_send_email_reply(pending)
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
        # Standby/Activation/Personality foundation (2026-08-20): EXECUTING
        # covers routing + dispatch as one atomic busy period - see
        # core/state_manager.py's EXECUTING docstring for why this isn't
        # split into a separate PROCESSING phase. try/finally so a raised
        # exception from any handler still clears this rather than leaving
        # state permanently stuck reporting EXECUTING.
        self._executing.set()
        try:
            if self.handle_confirmation(cmd):
                return
            intent = intent_router.route(cmd)
            if isinstance(intent, intent_router.Unhandled):
                if LLM_TOOL_CALLING_ENABLED:
                    tool_intent = self._try_tool_call(cmd)
                    if tool_intent is not None:
                        self._registry.dispatch(tool_intent)
                        return
                # No capability matched (regex or tool-call) - fall through to
                # the LLM. Not a registered capability itself: this is the
                # default reasoning path, not a capability with its own trigger
                # phrase.
                self.speak_stream(self.ask_llm_stream(cmd))
                return
            self._registry.dispatch(intent)
        finally:
            self._executing.clear()

    def _try_tool_call(self, cmd):
        """Only reached when config.py's llm_tool_calling_enabled is
        explicitly turned on - see its docstring for why that's off by
        default. Returns a mapped Intent if the model chose to call a known
        tool with well-formed arguments, else None (falls through to
        ask_llm_stream unchanged, same as if this whole block didn't run)."""
        try:
            resp = self.llm.chat_with_tools([{'role': 'user', 'content': cmd}], TOOL_SCHEMAS)
        except Exception as e:
            self.log(f'Tool-calling request failed: {e}')
            return None
        for call in resp['tool_calls']:
            mapped = tool_call_to_intent(call['name'], call['arguments'])
            if mapped is not None:
                return mapped
        return None

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
