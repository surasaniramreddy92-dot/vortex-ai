"""docs/REFACTOR_PLAN.md Step 7: explicit, named states instead of having to
read three different Event/bool flags scattered across main.py and
voice/barge_in.py to answer "what is VORTEX doing right now" - useful today
for logging/diagnostics, and for any future status surface (a tray icon
color, a spoken/visual "still listening" indicator) without re-deriving the
same reasoning again.

Deliberately NOT a replacement for the underlying concurrency primitives
(BargeIn's speaking/stop_speaking Events, the capturing Event, running) -
those were extracted and live-debugged in Step 3 against real barge-in
timing bugs (see voice/session.py's module docstring and CHANGELOG.md
2026-08-16), and forcing them through a single enum as the actual source of
truth would risk exactly the class of latency regression that took multiple
live sessions to fix, for a purely cosmetic win. current_state() is a
read-only, computed-on-demand *view* over those same Events - it never gets
out of sync because it has no state of its own to desync."""
from enum import Enum, auto


class VortexState(Enum):
    STANDBY = auto()
    ACTIVE_SESSION = auto()
    SPEAKING = auto()
    # Standby/Activation/Personality foundation (2026-08-20): added alongside
    # the existing three, not a rename of them - see this module's own
    # earlier reasoning about not desyncing from the real Events driving
    # behavior. Deliberately ONE new state, not the feature spec's literal
    # "PROCESSING" + "EXECUTING" pair: in this codebase's synchronous,
    # single-worker-thread design, intent classification
    # (core/intent_router.py's route()) is a sub-millisecond regex match,
    # not an observably separate phase from actually running the matched
    # capability or the LLM fallback - a second state with no real signal
    # behind it would be exactly the kind of fake, unearned distinction this
    # project's own conventions (and the feature spec itself) argue against.
    # Revisit this split if/when routing itself becomes multi-step or async
    # (e.g. a future multi-model router that takes measurable time to pick a
    # model) and a genuine second phase exists to name.
    EXECUTING = auto()


def current_state(*, is_speaking, is_in_active_session, is_executing=lambda: False):
    """is_speaking/is_in_active_session/is_executing: zero-arg callables
    (typically barge_in.speaking.is_set / session.in_active_session.is_set /
    a Vortex-owned Event set around execute()) - callables, not booleans, so
    the answer is always computed fresh against current Event state, not a
    snapshot that can go stale between when it's read and when it's used.
    is_executing defaults to a constant False so every existing caller
    (state_manager.current_state(is_speaking=..., is_in_active_session=...))
    keeps working unchanged - this is a purely additive parameter.

    Priority, highest first: SPEAKING, then EXECUTING, then ACTIVE_SESSION,
    then STANDBY. SPEAKING stays the most specific answer for "what is
    VORTEX doing right now" even while technically also inside an active
    session or (for a dispatched capability that itself calls speak() before
    finishing, e.g. a confirmation prompt) still executing."""
    if is_speaking():
        return VortexState.SPEAKING
    if is_executing():
        return VortexState.EXECUTING
    if is_in_active_session():
        return VortexState.ACTIVE_SESSION
    return VortexState.STANDBY
