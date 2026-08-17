"""Structured (JSON-lines) audit trail for consequential actions.

Separate from - not a replacement for - self.log()/logging.info() in
main.py, which stays exactly as it is (plain, unstructured chatter lines
into logs/vortex.log covering everything VORTEX does or says). This module
exists for one narrower purpose: a reviewable, machine-parseable record of
anything destructive or requiring confirmation - file delete/move, app
close, shutdown/restart, and every confirmation prompt together with its
eventual outcome - so that record doesn't have to be picked out of general
chatter by hand.

One JSON object per line (JSON Lines / .jsonl), no schema migrations, no new
heavy dependency - just `json` (stdlib) plus a plain append-mode file, the
same durability model logging.FileHandler already relies on for vortex.log.
"""
import json
import os
import threading
import time

# The vocabulary this module expects callers to use for `outcome`, kept as a
# constant for reference/tests rather than enforced at runtime - a caller
# passing something outside this set shouldn't make the audit trail itself
# raise (recording *something* about a consequential action, even with an
# unexpected outcome value, beats losing the record entirely).
OUTCOMES = ('prompted', 'executed', 'declined', 'failed')


class AuditLog:
    """Appends one JSON object per call to `record()`. Thread-safe via a
    simple lock - main.py's worker thread and any future callers can share
    one instance without interleaving partial lines."""

    def __init__(self, path):
        self.path = path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, action, target, outcome, **details):
        """action: short verb-ish string, e.g. 'delete_file', 'close_app',
        'shutdown'. target: the thing acted on (a path, an app name,
        'system'). outcome: 'prompted' | 'executed' | 'declined' | 'failed'
        (see OUTCOMES above). Any extra keyword arguments are nested under
        'details' (e.g. dest=..., reason=..., count=...)."""
        entry = {
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'action': action,
            'target': target,
            'outcome': outcome,
        }
        if details:
            entry['details'] = details
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
