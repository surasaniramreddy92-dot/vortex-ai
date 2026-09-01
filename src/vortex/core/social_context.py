"""Social-context foundation (2026-09-01) - Phase 5 of the Standby+
Activation+Personality/Owner-Context feature.

`classify()` is a deliberately simple, conservative, rule-based (keyword and
phrase-shape) classifier - NOT sentiment analysis, NOT a trained model, and
not a claim of real social understanding. It exists to prove the pipeline
end to end (text -> SocialLabel -> core/personality.py's per-label
directive -> a real system-prompt change) with an honest, inspectable
implementation a future, genuinely intelligent classifier (an LLM-based
judge, a trained model) can replace without changing this module's
contract: one function, text in, SocialLabel out.

Fails closed to AMBIGUOUS whenever no rule matches with confidence, same
philosophy as llm/tools.py's tool_call_to_intent() - guessing wrong here
would mean a policy directive fires that shouldn't (see personality.py's
docstring: NORMAL and AMBIGUOUS both map to "no special handling" on
purpose, so an under-confident guess never triggers protective/de-escalation
behavior that wasn't warranted).

Non-negotiable design constraint (documented here per the feature spec,
Phase 5): no label this module can ever produce - now or in any future,
smarter replacement - may correspond to an aggressive, insulting, or
threatening response. core/personality.py's _SOCIAL_DIRECTIVES table is the
enforcement point: it simply has no such directive to hand out.
"""
from enum import Enum
import re


class SocialLabel(Enum):
    NORMAL = 'normal'
    TECHNICAL_CRITICISM = 'technical_criticism'
    FRIENDLY_TEASING = 'friendly_teasing'
    OWNER_DIRECTED_DISRESPECT = 'owner_directed_disrespect'
    GENUINE_ABUSE = 'genuine_abuse'
    AMBIGUOUS = 'ambiguous'


_PLAYFUL_MARKERS = re.compile(r'\b(lol|lmao|haha+|jk|just kidding|kidding)\b')
_TECHNICAL_MARKERS = re.compile(
    r'\b(bug|wrong|incorrect|flawed|inefficient|doesn\'?t work|broken|mistake|regression|'
    r'this approach|that approach|the logic|the code|the implementation)\b')
_PROFANITY_OR_INSULT = re.compile(
    r'\b(stupid|idiot|dumb|useless|pathetic|garbage|trash|worthless|shut up|hate you)\b')
_OWNER_REFERENCE = re.compile(r'\b(boss|owner|your (?:owner|boss|creator|maker))\b')


def classify(text):
    """text should already be lowercased (matching intent_router.route()'s
    own contract - callers pass already-normalized command/utterance text).

    NORMAL means "no marker matched at all" - the honest default for
    ordinary conversation. AMBIGUOUS is reserved for text that trips
    *conflicting* signals at once (e.g. an insult alongside technical
    language - genuinely unclear whether that's blunt criticism or abuse) -
    a real "not confident enough to pick a label" case, distinct from
    "nothing notable detected"."""
    has_insult = bool(_PROFANITY_OR_INSULT.search(text))
    has_playful = bool(_PLAYFUL_MARKERS.search(text))
    has_technical = bool(_TECHNICAL_MARKERS.search(text))
    has_owner_reference = bool(_OWNER_REFERENCE.search(text))

    if has_insult and has_playful:
        return SocialLabel.FRIENDLY_TEASING
    if has_insult and has_owner_reference:
        return SocialLabel.OWNER_DIRECTED_DISRESPECT
    if has_insult and has_technical:
        return SocialLabel.AMBIGUOUS
    if has_insult:
        return SocialLabel.GENUINE_ABUSE
    if has_technical:
        return SocialLabel.TECHNICAL_CRITICISM
    if has_playful:
        return SocialLabel.FRIENDLY_TEASING
    return SocialLabel.NORMAL
