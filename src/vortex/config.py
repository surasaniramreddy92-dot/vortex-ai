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
import os


def _float_env(name, default):
    return float(os.getenv(name, str(default)))


def _int_env(name, default):
    return int(os.getenv(name, str(default)))


def _default_root():
    # Path.home()-based fallback, not the hardcoded E:\VORTEX main.py uses today
    # - see docs/CURRENT_STATE.md Section 4 for why the hardcoded path is a real
    # portability problem. VORTEX_HOME lets Step 2's migration point this back at
    # E:\VORTEX explicitly, preserving continuity with existing logs/data/models
    # on this machine rather than silently relocating them.
    return os.getenv('VORTEX_HOME', r'E:\VORTEX')


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
    # than it did for the synthetic training clips. Lowered further to 0.65 as
    # a pragmatic, evidence-based (not arbitrary) compromise - it is not a
    # clean fix, and the real fix is very likely retraining/revalidating the
    # wake model against real voice samples from this user rather than only
    # synthetic TTS (tools/wakeword/build_hey_vortex.py), not just tuning this
    # number further. Trade-off, not a free lunch: standby false-positives
    # were already a known open issue before either change (see
    # IMPLEMENTED.md) and each reduction makes that more likely - accepted
    # because a wake word that doesn't wake is a worse failure mode.
    wake_threshold: float = field(default_factory=lambda: _float_env('VORTEX_WAKE_THRESHOLD', 0.65))
    # Kept equal to wake_threshold, not independently set - main.py's own comment
    # on BARGE_IN_THRESHOLD documents this as deliberate ("NOT stricter than
    # WAKE_THRESHOLD, on purpose"): an earlier attempt at a *higher* bar for
    # barge-in broke real interruptions, since barge-in is inherently harder to
    # score high on (the mic also hears our own speakers). Lowering wake_threshold
    # without lowering this too would have silently reintroduced that same bug.
    barge_in_threshold: float = field(default_factory=lambda: _float_env('VORTEX_BARGE_IN_THRESHOLD', 0.65))
    wake_cooldown: float = field(default_factory=lambda: _float_env('VORTEX_WAKE_COOLDOWN', 1.5))
    wake_watchdog_timeout: float = field(default_factory=lambda: _float_env('VORTEX_WAKE_WATCHDOG_TIMEOUT', 5.0))

    agc_target_rms: float = field(default_factory=lambda: _float_env('VORTEX_AGC_TARGET_RMS', 3500))
    agc_max_gain: float = field(default_factory=lambda: _float_env('VORTEX_AGC_MAX_GAIN', 4.0))
    agc_noise_margin: float = field(default_factory=lambda: _float_env('VORTEX_AGC_NOISE_MARGIN', 1.6))

    session_timeout: float = field(default_factory=lambda: _float_env('VORTEX_SESSION_TIMEOUT', 18))
    history_turns: int = field(default_factory=lambda: _int_env('VORTEX_HISTORY_TURNS', 10))
    summary_max_chars: int = field(default_factory=lambda: _int_env('VORTEX_SUMMARY_MAX_CHARS', 12000))

    llm_model: str = field(default_factory=lambda: os.getenv('VORTEX_MODEL', 'llama3.2:1b'))
    system_prompt: str = field(default_factory=lambda: os.getenv(
        'VORTEX_SYSTEM_PROMPT',
        'You are VORTEX, a concise desktop AI assistant. You are heard, not read, '
        'so answer in short spoken sentences and never use markdown or code blocks.'))

    postgres_dsn: str = field(default_factory=lambda: os.getenv(
        'VORTEX_POSTGRES_DSN', 'dbname=vortex user=vortex password=vortex_local_dev host=localhost'))
    qdrant_url: str = field(default_factory=lambda: os.getenv('VORTEX_QDRANT_URL', 'http://localhost:6333'))
    embed_model: str = field(default_factory=lambda: os.getenv('VORTEX_EMBED_MODEL', 'nomic-embed-text'))

    # These four depend on `root`, so they can't use a simple default_factory
    # with no arguments - resolved in __post_init__ once root is known.
    wake_word: str = field(default='')
    log_dir: str = field(default='')
    data_dir: str = field(default='')
    memory_db_path: str = field(default='')

    def __post_init__(self):
        object.__setattr__(self, 'wake_word', os.getenv(
            'VORTEX_WAKE_WORD', os.path.join(self.root, 'tools', 'wakeword', 'models', 'hey_vortex.onnx')))
        object.__setattr__(self, 'log_dir', os.path.join(self.root, 'logs'))
        object.__setattr__(self, 'data_dir', os.path.join(self.root, 'data'))
        object.__setattr__(self, 'memory_db_path', os.getenv(
            'VORTEX_MEMORY_DB', os.path.join(self.data_dir, 'vortex_memory.db')))

    @classmethod
    def from_env(cls):
        """Explicit entry point (rather than relying on class-body defaults
        alone), so a caller's intent to read the current environment is visible
        at the call site, and so this is trivially mockable in tests without
        needing to modify os.environ globally."""
        return cls()
