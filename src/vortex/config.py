"""Typed configuration for VORTEX.

Wired into main.py as of Step 2 of docs/REFACTOR_PLAN.md (main.py's module-
level constants are populated from one VortexConfig instance instead of their
own scattered os.getenv(...) calls).

Every field mirrors an existing constant in main.py, name-for-name and
default-for-default, verified directly against main.py's source at the time
this file was written - not reconstructed from memory. Where main.py hardcodes
a value with no env var today (e.g. MODEL, SYSTEM_PROMPT), the field is
included here too (typed configuration's whole purpose is to be the one place
all of this eventually lives) but the default is unchanged, so wiring this in
later is not expected to change current behavior for anyone who hasn't set an
env var for it.

Every field uses `field(default_factory=...)`, not a plain `= os.getenv(...)`
class-body default, on purpose: a plain default expression is evaluated once
at class-definition time and then frozen for every future instance, which
would make VortexConfig() silently ignore any env var set after the module
was first imported - verified empirically (not assumed) before writing this,
since that's exactly the kind of subtle bug that defeats the point of a
*typed, testable* config object. default_factory re-reads the environment on
every construction instead.
"""
from dataclasses import dataclass, field
from pathlib import Path
import os


def _float_env(name, default):
    return float(os.getenv(name, str(default)))


def _int_env(name, default):
    return int(os.getenv(name, str(default)))


def _bool_env(name, default):
    # Explicit string set, not a truthy cast of the raw string - os.getenv
    # always returns a non-empty string when the var is set at all, so
    # bool("false") would evaluate True and silently invert the intent of
    # e.g. VORTEX_OCR_ENABLED=false.
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() not in ('0', 'false', 'no', 'off')


def _default_root():
    # Path.home()-based fallback - see docs/CURRENT_STATE.md Section 4 for why
    # a hardcoded drive-letter path is a real portability problem (it doesn't
    # just break on Linux/macOS - it breaks on any Windows machine, including
    # a CI runner, that isn't this exact E: drive). This comment used to
    # describe this exact fallback while the code beneath it hardcoded
    # r'E:\VORTEX' as the literal default anyway - caught by
    # docs/REFACTOR_PLAN.md Step 10's feature-parity check actually running
    # CI for real on a fresh machine, not by local testing (which always ran
    # on this exact E: drive and could never have caught it). VORTEX_HOME is
    # set explicitly in this machine's own .env (gitignored, not this
    # default) to E:\VORTEX, preserving continuity with existing logs/data/
    # models here - the code default only matters for a machine that hasn't
    # set it, which was never true for local runs, only for CI.
    return os.getenv('VORTEX_HOME', str(Path.home() / '.vortex'))


# The trained wake model is a repo-committed asset (tools/wakeword/models/
# hey_vortex.onnx, ~394KB, checked into git per docs/CURRENT_STATE.md §1) -
# code shipped alongside src/vortex/, not user-generated runtime data like
# logs/memory/audit. It must resolve relative to *this file's own location*
# (the repo/package checkout), never relative to VORTEX_HOME/root - those
# two happened to be the same directory on this machine (E:\VORTEX is both
# the git checkout and where VORTEX_HOME pointed), which is exactly why
# `root`-relative resolution looked correct here but broke the instant CI
# ran with VORTEX_HOME defaulting to a real user-data directory
# (~/.vortex) that obviously doesn't contain the repo's tools/ folder.
_DEFAULT_WAKE_WORD_PATH = (
    Path(__file__).resolve().parents[2] / 'tools' / 'wakeword' / 'models' / 'hey_vortex.onnx')


@dataclass(frozen=True)
class VortexConfig:
    root: str = field(default_factory=_default_root)
    voice: str = field(default_factory=lambda: os.getenv('VORTEX_VOICE', 'en-US-AvaMultilingualNeural'))
    user_name: str = field(default_factory=lambda: os.getenv('USER_NAME', 'Boss'))
    # On laptops the mic and speakers sit close together, so VORTEX's own voice
    # dominates whatever the mic hears while it's talking - a real barge-in
    # failure traced to this: real diagnostic logs showed zero wake-model score
    # above 0.3 (not "just under threshold") while the user tried to interrupt
    # during a long response. Gain control can't fix this - it scales the whole
    # mixed signal, it can't separate one voice from another. Turning VORTEX's
    # own output down is the practical mitigation available without building
    # full acoustic echo cancellation. 1.0 = unchanged/full volume.
    tts_volume: float = field(default_factory=lambda: _float_env('VORTEX_TTS_VOLUME', 1.0))

    # 0.8 was calibrated against synthetic TTS clips only (tools/wakeword/validate_hey_vortex.py),
    # never against real human voice at typical laptop-mic distance. First
    # lowered to 0.75 on 2026-08-16 after 74 real attempts landed in the
    # 0.65-0.84 band; that helped (two confirmed real triggers at 0.98+) but
    # real attempts kept landing at 0.70-0.74 and still missing. A full-session
    # histogram (397 scored frames) showed no clean valley between "background"
    # and "genuine speech" - real attempts appear spread across roughly
    # 0.5-0.95, not clustered tightly above some obvious cutoff, meaning this
    # wake model's confidence for this user's actual voice runs lower overall
    # than it did for the synthetic training clips. Lowered further to 0.65 on
    # 2026-08-16 as a pragmatic, evidence-based compromise. Lowered again to
    # 0.60 on 2026-08-17 after direct evidence the 0.65 bar was still too
    # tight even under good conditions: running the exact same wake model +
    # AGC pipeline in isolation (bypassing the live process entirely) against
    # a real captured "Hey Vortex" utterance scored a max of 0.6499 - a
    # genuine attempt landing a hair's-width under 0.65, not background noise.
    # The live process scored the same kind of attempt even lower still
    # (likely real resource contention - Ollama/Qdrant/memory/tray all
    # running concurrently - degrading an already-marginal score further).
    # Not a clean fix, and the real fix is very likely retraining/
    # revalidating the wake model against real voice samples from this user
    # rather than only synthetic TTS (tools/wakeword/build_hey_vortex.py),
    # not just tuning this number further. Trade-off, not a free lunch:
    # standby false-positives were already a known open issue before any of
    # these changes (see IMPLEMENTED.md) and each reduction makes that more
    # likely - accepted because a wake word that doesn't wake is a worse
    # failure mode than an occasional unwanted activation.
    wake_threshold: float = field(default_factory=lambda: _float_env('VORTEX_WAKE_THRESHOLD', 0.60))
    # Kept equal to wake_threshold, not independently set - app.py's own comment
    # on BARGE_IN_THRESHOLD documents this as deliberate ("NOT stricter than
    # WAKE_THRESHOLD, on purpose"): an earlier attempt at a *higher* bar for
    # barge-in broke real interruptions, since barge-in is inherently harder to
    # score high on (the mic also hears our own speakers). Lowering wake_threshold
    # without lowering this too would have silently reintroduced that same bug.
    barge_in_threshold: float = field(default_factory=lambda: _float_env('VORTEX_BARGE_IN_THRESHOLD', 0.60))
    wake_cooldown: float = field(default_factory=lambda: _float_env('VORTEX_WAKE_COOLDOWN', 1.5))
    wake_watchdog_timeout: float = field(default_factory=lambda: _float_env('VORTEX_WAKE_WATCHDOG_TIMEOUT', 5.0))

    agc_target_rms: float = field(default_factory=lambda: _float_env('VORTEX_AGC_TARGET_RMS', 3500))
    agc_max_gain: float = field(default_factory=lambda: _float_env('VORTEX_AGC_MAX_GAIN', 4.0))
    agc_noise_margin: float = field(default_factory=lambda: _float_env('VORTEX_AGC_NOISE_MARGIN', 1.6))

    session_timeout: float = field(default_factory=lambda: _float_env('VORTEX_SESSION_TIMEOUT', 18))
    history_turns: int = field(default_factory=lambda: _int_env('VORTEX_HISTORY_TURNS', 10))
    summary_max_chars: int = field(default_factory=lambda: _int_env('VORTEX_SUMMARY_MAX_CHARS', 12000))

    llm_model: str = field(default_factory=lambda: os.getenv('VORTEX_MODEL', 'llama3.2:1b'))
    # Backstop cap on response length (Ollama's num_predict) - works together
    # with system_prompt below, not instead of it. First tried num_predict
    # alone as the only control (60, then 40): both still let real responses
    # run 14-17s, because a raw token cut lands mid-sentence rather than at a
    # natural stop, and doesn't reliably produce a *shorter* sentence in the
    # first place. The model also doesn't follow the system prompt's word
    # limit deterministically - same question, same prompt, ranged 9 to 24+
    # words across repeated tries. 50 was tried as a generous backstop and
    # still let one real answer ramble to a mid-sentence cutoff live. 32
    # verified as a firm backstop across 5 repeated tries of the same
    # question: every single one completed with a proper sentence ending
    # (7-19 words), none cut off mid-thought.
    llm_max_tokens: int = field(default_factory=lambda: _int_env('VORTEX_LLM_MAX_TOKENS', 32))
    # "Short spoken sentences" (plural, no explicit limit) was not reliably
    # followed by this model - live evidence 2026-08-16: single-sentence
    # answers running 230+ characters, 14-17s to speak. Longer speech directly
    # means a longer window where VORTEX's own voice can mask a real
    # "Hey Vortex" barge-in attempt (near-field self-noise - see tts_volume
    # above); live tests during exactly this kind of long response repeatedly
    # showed zero wake-model scores for the entire duration, not a late
    # trigger. A concrete constraint ("ONE sentence, no more than 20 words")
    # was verified to actually work: 6-20 words across several real
    # questions, every one a complete sentence.
    system_prompt: str = field(default_factory=lambda: os.getenv(
        'VORTEX_SYSTEM_PROMPT',
        'You are VORTEX, a concise desktop AI assistant. You are heard, not read. '
        'Answer in ONE short sentence, no more than 20 words. '
        'Never use markdown or code blocks.'))

    postgres_dsn: str = field(default_factory=lambda: os.getenv(
        'VORTEX_POSTGRES_DSN', 'dbname=vortex user=vortex password=vortex_local_dev host=localhost'))
    qdrant_url: str = field(default_factory=lambda: os.getenv('VORTEX_QDRANT_URL', 'http://localhost:6333'))
    embed_model: str = field(default_factory=lambda: os.getenv('VORTEX_EMBED_MODEL', 'nomic-embed-text'))

    # Phase 7 (Document Intelligence) OCR fallback, added 2026-08-16. Scanned/
    # image-only PDF pages return empty (or near-empty) text from PyMuPDF's
    # native extraction, since there's no text layer to read - OCR (pytesseract
    # + the Tesseract binary) is the fallback for those pages specifically, not
    # a replacement for native extraction (native text is faster and more
    # accurate whenever it's actually present). Default on, but genuinely a
    # no-op wherever the Tesseract binary isn't installed - see documents.py's
    # _ocr_available(), which checks for it explicitly and logs rather than
    # assuming, and degrades to "whatever native text WAS extracted" instead
    # of failing. On this dev machine as of this writing, Tesseract itself is
    # NOT installed (`where tesseract` finds nothing) - only the pytesseract
    # Python wrapper is; this flag exists to be able to turn the *attempt*
    # off entirely (e.g. on a machine where probing for the binary once per
    # process is itself unwanted) rather than to promise OCR is functional
    # here today.
    ocr_enabled: bool = field(default_factory=lambda: _bool_env('VORTEX_OCR_ENABLED', True))
    # Tesseract language code (its own flag, not a locale string) - 'eng' is
    # the default language pack Tesseract installs, so this is the one choice
    # that doesn't additionally require the user to `tesseract --list-langs`
    # and install a language pack first.
    ocr_language: str = field(default_factory=lambda: os.getenv('VORTEX_OCR_LANGUAGE', 'eng'))
    # Whether retrieved-chunk provenance (page number, or section/sheet name
    # where a document has no page concept) gets surfaced in the prompt
    # assembled for document Q&A - see rag.py's build_rag_prompt(). Default on
    # since it costs nothing when a chunk has no page/section (falls through
    # to the unlabeled excerpt, same as before this existed) and is one of the
    # two explicit Phase 7 gaps this change closes (see IMPLEMENTED.md).
    document_page_numbers: bool = field(
        default_factory=lambda: _bool_env('VORTEX_DOCUMENT_PAGE_NUMBERS', True))

    # Hybrid dense+sparse retrieval and reranking, added 2026-08-16 (see
    # rag.py's module docstring for the full mechanism and reasoning).
    # Default on: hybrid search fixes a real, demonstrated gap (dense-only
    # retrieval can miss a chunk purely because it contains an exact keyword
    # - a product code, a number, a proper noun - the embedding model didn't
    # weight heavily; see IMPLEMENTED.md for the concrete before/after
    # ranking comparison). Off-switchable because it costs one extra
    # Postgres query per uncached document (for the BM25 corpus) and more
    # CPU per question than dense-only search - a real, if small, latency
    # trade-off on top of the existing embedding + Ollama round trip.
    rag_hybrid_search: bool = field(
        default_factory=lambda: _bool_env('VORTEX_RAG_HYBRID_SEARCH', True))
    # Keyword-overlap reranking of the fused hybrid candidate pool - NOT a
    # cross-encoder (sentence-transformers pulls in torch, a ~122MB+ Windows
    # wheel plus a separately-downloaded model at first use; see rag.py for
    # the measured footprint). Default on: it's a cheap, local, explainable
    # pass over an already-small candidate pool (RERANK_POOL=10 chunks), not
    # a second network/model round trip. Off-switchable for the same reason
    # as rag_hybrid_search above - it's additional CPU work per question.
    rag_rerank: bool = field(
        default_factory=lambda: _bool_env('VORTEX_RAG_RERANK', True))

    # Phase 1 offline STT/TTS fallback, added 2026-08-16. Cloud STT (Google Web
    # Speech) and cloud TTS (edge-tts) both silence VORTEX outright on a network
    # outage even though wake detection is fully local - see IMPLEMENTED.md's
    # Phase 1 row. This is a *fallback*, not a replacement: cloud is tried
    # first every time (better quality, matches existing behavior exactly when
    # online). TTS's offline engine only engages on a genuine network-
    # reachability failure - tts.py's _synth
    # (aiohttp.ClientConnectionError/asyncio.TimeoutError, not edge-tts's own
    # EdgeTTSException family). STT's trigger is broader as of 2026-08-17:
    # stt.py's capture_command falls back on sr.RequestError (network) AND
    # sr.UnknownValueError (Google reached fine, couldn't parse the audio) -
    # widened from network-only after real evidence a live UnknownValueError
    # capture, which Google failed to transcribe, transcribed correctly via
    # faster-whisper (language probability 1.0); cloud STT is measurably less
    # reliable than the "fallback" for this project's AGC-boosted audio. One
    # kill switch covers both directions, matching the ocr_enabled pattern
    # just above: default on, but a genuine no-op wherever faster-whisper/
    # piper-tts aren't installed or their model files aren't cached yet -
    # both stt.py and tts.py probe for this explicitly and log rather than
    # assuming, same as documents.py's _ocr_available().
    offline_fallback_enabled: bool = field(
        default_factory=lambda: _bool_env('VORTEX_OFFLINE_FALLBACK_ENABLED', True))
    # faster-whisper model size. Measured on this dev machine (CPU-only, no
    # dedicated GPU - see IMPLEMENTED.md): warm inference on a short spoken
    # command was tiny.en ~0.5-1.5s vs base.en ~0.9-1.4s, i.e. the two were not
    # meaningfully different in latency once the model is loaded and cached
    # (75MB vs 141MB on disk). Given that, base.en's real, well-documented
    # accuracy advantage over tiny.en is worth taking - this is a fallback
    # path where correctness matters more than shaving another ~0.3s off an
    # already-sub-2s transcription. Revisit with tiny.en if this ever needs to
    # run on meaningfully weaker CPU hardware than this dev machine.
    offline_stt_model: str = field(
        default_factory=lambda: os.getenv('VORTEX_OFFLINE_STT_MODEL', 'base.en'))
    # Piper TTS voice. piper-tts (the OHF-voice/piper1-gpl package on PyPI, GPL-
    # 3.0-or-later - noted here since that's a different license than the rest
    # of this project's dependencies) ships prebuilt Windows wheels with no
    # separate build toolchain required, confirmed by installing it directly
    # in this dev environment - the plan's original Piper mention turned out to
    # be realistically installable on Windows via pip after all, no substitute
    # engine was needed. en_US-lessac-medium synthesized a 13-word sentence in
    # ~2.2s (one-time voice load ~4s, cached after) and produced a real,
    # playable ~4.6s WAV file - see IMPLEMENTED.md for the full test.
    offline_tts_voice: str = field(
        default_factory=lambda: os.getenv('VORTEX_OFFLINE_TTS_VOICE', 'en_US-lessac-medium'))

    # These seven depend on `root`/`data_dir`/`log_dir`, so they can't use a
    # simple default_factory with no arguments - resolved in __post_init__
    # once root is known.
    wake_word: str = field(default='')
    log_dir: str = field(default='')
    data_dir: str = field(default='')
    memory_db_path: str = field(default='')
    offline_stt_model_dir: str = field(default='')
    offline_tts_model_dir: str = field(default='')
    # Phase 2 (2026-08-16): structured JSON-lines audit trail for consequential
    # actions (file delete/move, app close, shutdown/restart, confirmation
    # prompts and their outcomes) - see audit.py's module docstring for why
    # this is a separate file from log_dir's plain vortex.log rather than a
    # replacement for it. Lives under log_dir alongside vortex.log since both
    # are append-only operational records, just different formats/purposes.
    audit_log_path: str = field(default='')

    def __post_init__(self):
        object.__setattr__(self, 'wake_word', os.getenv('VORTEX_WAKE_WORD', str(_DEFAULT_WAKE_WORD_PATH)))
        object.__setattr__(self, 'log_dir', os.path.join(self.root, 'logs'))
        object.__setattr__(self, 'data_dir', os.path.join(self.root, 'data'))
        object.__setattr__(self, 'memory_db_path', os.getenv(
            'VORTEX_MEMORY_DB', os.path.join(self.data_dir, 'vortex_memory.db')))
        # Both faster-whisper and piper-tts cache whatever they download under
        # these directories on their own (huggingface_hub's snapshot cache /
        # piper's voice-file layout respectively) - pointing download_root/
        # download_dir at a folder under data_dir just keeps that cache inside
        # VORTEX_HOME instead of scattered into each library's own default
        # (~/.cache/huggingface, cwd), consistent with memory_db_path above.
        # Neither library re-downloads once the target file already exists.
        object.__setattr__(self, 'offline_stt_model_dir', os.getenv(
            'VORTEX_OFFLINE_STT_MODEL_DIR', os.path.join(self.data_dir, 'offline_stt_models')))
        object.__setattr__(self, 'offline_tts_model_dir', os.getenv(
            'VORTEX_OFFLINE_TTS_MODEL_DIR', os.path.join(self.data_dir, 'offline_tts_models')))
        object.__setattr__(self, 'audit_log_path', os.getenv(
            'VORTEX_AUDIT_LOG', os.path.join(self.log_dir, 'audit.jsonl')))

    @classmethod
    def from_env(cls):
        """Explicit entry point (rather than relying on class-body defaults
        alone), so a caller's intent to read the current environment is visible
        at the call site, and so this is trivially mockable in tests without
        needing to modify os.environ globally."""
        return cls()
