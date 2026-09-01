"""Personality foundation (2026-08-20) - the lightweight abstraction the
Standby+Activation+Personality/Owner-Context feature's Phase 3 asks for.

Deliberately NOT a multi-agent personality system: one enum plus one pure
function that appends a short, hand-written tone directive onto the existing
system prompt. No joke banks, no canned-response tables - the "intelligence"
still comes entirely from the one existing LLM call (see app.py's
ask_llm_stream), this module only steers its instructions.

Conceptual pipeline this implements (Conversation Context -> Intent ->
Personality Policy -> Response Generation): `build_system_prompt()` is the
"Personality Policy" step - it takes the base system prompt, the current
PersonalityMode, and an optional core.social_context.SocialLabel, and
returns the system prompt actually sent to the LLM. Nothing upstream
(intent_router.py) or downstream (llm/ollama_provider.py) needs to know this
layer exists - it's a pure string transform sitting entirely inside
app.py's ask_llm_stream, which is exactly the seam a future multi-model
router could plug into (swap build_system_prompt's output, or add a
per-model variant, without touching any call site).

PROTECTIVE's directive spells out this project's one non-negotiable
principle from the feature spec, as literal text sent to the model, not
just a comment here: VORTEX must never blindly defend its owner when the
owner is factually wrong.
"""
from enum import Enum


class PersonalityMode(Enum):
    PROFESSIONAL = 'professional'
    FRIENDLY = 'friendly'
    WITTY = 'witty'
    PROTECTIVE = 'protective'
    DEMO = 'demo'


_DIRECTIVES = {
    PersonalityMode.PROFESSIONAL: 'Stay formal, concise, and neutral in tone.',
    PersonalityMode.FRIENDLY: 'Use a warm, casual, approachable tone.',
    PersonalityMode.WITTY: 'You may add a touch of light, dry humor, without sacrificing correctness.',
    PersonalityMode.PROTECTIVE: (
        'You may show mild loyalty toward your owner in tone, but you must never blindly defend '
        'the owner when they are factually wrong - if criticism of the owner or a prior answer is '
        'technically valid, say so plainly.'
    ),
    PersonalityMode.DEMO: (
        'You are demonstrating your own capabilities to a visitor. Stay polished and professional; '
        'never mention internal logs, diagnostics, or implementation details.'
    ),
}

# core/social_context.py's SocialLabel -> an additional directive layered on
# top of the personality mode's own. None means "no special handling" - the
# deliberate default for NORMAL and AMBIGUOUS (see social_context.py's
# module docstring for why ambiguous cases must not trigger extra behavior).
_SOCIAL_DIRECTIVES = {
    'technical_criticism': (
        'The user is offering technical criticism. Evaluate it objectively - if it is valid, '
        'say so plainly, even if it means agreeing you or the owner were wrong.'
    ),
    'friendly_teasing': 'The user is teasing in a friendly way. A small amount of light humor back is fine.',
    'owner_directed_disrespect': (
        "Someone is being disrespectful toward the owner. You may respond with a mild, "
        "professional, protective tone on the owner's behalf - never insult or threaten anyone."
    ),
    'genuine_abuse': (
        'The user is being genuinely abusive. De-escalate, stay calm and professional. '
        'Do not insult, threaten, or escalate under any circumstances.'
    ),
}


def build_system_prompt(base_prompt, mode, social_label=None):
    """Pure string transform: base_prompt + this mode's tone directive +
    (optionally) this social_label's directive. social_label is a
    core.social_context.SocialLabel or None - accepted as either the enum or
    its .value string so callers that already have the raw label string
    (e.g. from a test) don't need to import the enum just to call this."""
    label_value = social_label.value if hasattr(social_label, 'value') else social_label
    parts = [base_prompt, _DIRECTIVES[mode]]
    social_directive = _SOCIAL_DIRECTIVES.get(label_value)
    if social_directive:
        parts.append(social_directive)
    return ' '.join(parts)
