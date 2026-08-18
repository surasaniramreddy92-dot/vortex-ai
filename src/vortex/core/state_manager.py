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


def current_state(*, is_speaking, is_in_active_session):
    """is_speaking/is_in_active_session: zero-arg callables (typically
    barge_in.speaking.is_set / session.in_active_session.is_set) -
    callables, not booleans, so the answer is always computed fresh against
    current Event state, not a snapshot that can go stale between when it's
    read and when it's used.

    SPEAKING takes priority over ACTIVE_SESSION: mid-response, VORTEX is
    both "speaking" and technically inside an active session's follow-up
    window, but SPEAKING is the more specific, more useful answer to "what
    is VORTEX doing right now"."""
    if is_speaking():
        return VortexState.SPEAKING
    if is_in_active_session():
        return VortexState.ACTIVE_SESSION
    return VortexState.STANDBY
