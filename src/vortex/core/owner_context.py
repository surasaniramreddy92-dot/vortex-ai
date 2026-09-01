"""Owner-context foundation (2026-08-20) - Phase 4 of the Standby+
Activation+Personality/Owner-Context feature.

Deliberately thin: VORTEX is, and remains, a single-owner assistant (no
multi-user support exists anywhere in this codebase). This class holds only
the genuinely stable identity fields (owner_id, display_name,
preferred_address - the latter two sourced from the existing config.user_name,
not a new, second config knob for the same concept). session_state and
personality_mode are read-only *properties* that delegate live to the host
Vortex instance, not duplicated fields - copying them here would reintroduce
exactly the "snapshot that can go stale" problem core/state_manager.py's own
docstring already warns against for VortexState.

No voice enrollment, no speaker identification, no presence detection - the
spec explicitly rules these out for this phase. owner_id is a fixed
constant, not derived from any biometric or environmental signal; the clean
extension point for real, consent-based voice enrollment is `owner_id`
itself moving from a hardcoded default to a lookup, without changing this
class's shape.
"""


class OwnerContext:
    def __init__(self, *, owner_id, display_name, preferred_address, get_session_state, get_personality_mode):
        self.owner_id = owner_id
        self.display_name = display_name
        self.preferred_address = preferred_address
        self._get_session_state = get_session_state
        self._get_personality_mode = get_personality_mode

    @property
    def session_state(self):
        return self._get_session_state()

    @property
    def personality_mode(self):
        return self._get_personality_mode()
