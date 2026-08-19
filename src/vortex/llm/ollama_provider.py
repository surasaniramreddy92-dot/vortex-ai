"""docs/REFACTOR_PLAN.md Step 4: the concrete Ollama implementation of
LLMProvider. Ports main.py's _poll_stream verbatim - the queue+background-
thread pattern exists because a plain `for part in stream` blocks on the
network read for the next token and only re-checks the cancellation signal
once one arrives, which measurably delayed barge-in on a cold-loading model
(see the original docstring, preserved below). Mirrors the same
producer-thread-plus-polled-queue pattern voice/tts.py's _speak_chunks uses
for TTS, so stop_speaking is checked at least every 0.1s regardless of how
slow Ollama is.
"""
import contextlib
import queue
import threading

import ollama

from .provider import LLMProvider

_STOP = object()


class OllamaProvider(LLMProvider):
    def __init__(self, model, max_tokens, barge_in, is_running, keep_alive='30m'):
        self.model = model
        self.max_tokens = max_tokens
        self.barge_in = barge_in
        self.is_running = is_running
        self.keep_alive = keep_alive

    def chat_with_tools(self, messages, tools):
        """Non-streaming - a tool call is one structured decision, not
        something to stream token-by-token. No keep_alive/num_predict
        options here on purpose: tool-calling responses are short structured
        decisions, not the long free-form replies num_predict was tuned
        against (see config.py's llm_max_tokens docstring - that tuning is
        specific to conversational answers, not this path)."""
        resp = ollama.chat(model=self.model, messages=messages, tools=tools)
        msg = resp['message']
        return {
            'content': msg.get('content') or '',
            'tool_calls': [
                {'name': tc['function']['name'], 'arguments': tc['function']['arguments']}
                for tc in (msg.get('tool_calls') or [])
            ],
        }

    def chat_stream(self, messages):
        # Deliberately not a generator function itself: ollama.chat(...) runs
        # synchronously here, so a connection failure raises immediately to
        # the caller's own try/except, exactly as it did inline in main.py -
        # not deferred to the first iteration of the returned generator.
        stream = ollama.chat(model=self.model, messages=messages, stream=True,
                              keep_alive=self.keep_alive, options={'num_predict': self.max_tokens})
        return self._wrap(stream)

    def _wrap(self, stream):
        try:
            yield from self._poll_stream(stream)
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    def _poll_stream(self, stream):
        """Consume an Ollama streaming response on a background thread and
        yield tokens through a polled queue, instead of a plain `for part in
        stream`.

        A plain for-loop blocks on the network read for the *next* token, and
        only re-checks stop_speaking once one arrives - fine when Ollama is
        warm, but a session's model can fail to warm at startup (Ollama not
        up yet when VORTEX tried), so the first real request pays a
        cold-load cost mid-generation. Live evidence: a barge-in was logged
        as "triggered" but the current answer kept playing for another ~20s
        before "yielding the floor" actually appeared - the generator was
        blocked waiting on Ollama's next token the whole time, deaf to
        stop_speaking. This queue+thread version checks stop_speaking at
        least every 0.1s regardless of how slow Ollama is."""
        token_q = queue.Queue(maxsize=8)

        def pump():
            try:
                for part in stream:
                    token_q.put(part['message']['content'])
            except Exception as e:
                token_q.put(e)
            finally:
                token_q.put(_STOP)

        threading.Thread(target=pump, daemon=True).start()
        while True:
            if self.barge_in.stop_speaking.is_set() or not self.is_running():
                return
            try:
                item = token_q.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is _STOP:
                return
            if isinstance(item, Exception):
                raise item
            yield item
