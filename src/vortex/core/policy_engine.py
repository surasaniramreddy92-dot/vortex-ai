"""docs/REFACTOR_PLAN.md Step 6: `is_affirmative` moved here unchanged from
main.py, where it landed in Step 5 as an early fix for the `'yes' in cmd`
substring bug flagged in docs/CURRENT_STATE.md §6 (a misheard "no, not yes I
don't want that" used to confirm instead of decline).

A single fully general "does this Intent require confirmation, and what do
I say" policy engine (the kind Phase 15 of the master roadmap describes) is
deliberately NOT built here - docs/CURRENT_STATE.md §6 judged the current
per-branch `awaiting_confirmation` pattern in main.py "fine for now" at this
scale (six destructive actions, each with a genuinely distinct spoken
prompt that a generic table wouldn't remove the need to hand-write anyway),
and nothing in this step's actual scope changes that judgment - it only
formalizes the one piece that had a real, already-fixed correctness bug."""
import re

_AFFIRMATIVE_WORDS = {'yes', 'yeah', 'yep', 'yup', 'sure', 'confirm', 'confirmed', 'proceed'}
_NEGATIVE_WORDS = {'no', 'nope', 'nah', "don't", 'dont', 'cancel', 'stop', 'negative'}


def is_affirmative(cmd):
    """Whole-word matching (not substring), and any negative word anywhere
    wins over an affirmative one, so "no", "not yes", and "don't" all
    correctly decline even though a plain 'yes' in cmd check would have
    matched them."""
    words = set(re.findall(r"[a-z']+", cmd))
    if words & _NEGATIVE_WORDS:
        return False
    return bool(words & _AFFIRMATIVE_WORDS) or 'go ahead' in cmd or 'do it' in cmd
