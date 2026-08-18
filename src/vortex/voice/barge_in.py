"""Shared cancellation-token object for barge-in.

Extracted verbatim from Vortex.__init__'s two threading.Event attributes
(docs/REFACTOR_PLAN.md Step 3) - same two Events, same semantics, just
grouped into one small object that wake.py, tts.py, stt.py, session.py,
llm/ollama_provider.py's token-polling, and app.py's ask_llm_stream can all
share a reference to, instead of each reaching into Vortex's own attributes
directly.

No behavior lives here on purpose - every call site still does
`barge_in.speaking.is_set()` / `.set()` / `.clear()` exactly as it did when
these were `self.speaking` / `self.stop_speaking` on Vortex, so this is a
pure "where do these two Events live" change, not a logic change.
"""
import threading


class BargeIn:
    """speaking: TTS is on air. stop_speaking: cut it off now."""

    def __init__(self):
        self.speaking = threading.Event()
        self.stop_speaking = threading.Event()
