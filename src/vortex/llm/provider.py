"""docs/REFACTOR_PLAN.md Step 4: isolates Ollama specifics behind this
interface so a cloud adapter can be added later without touching call sites
in main.py (ask_llm_stream, _stream_llm_answer)."""
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def chat_stream(self, messages):
        """Start a chat completion for `messages` (Ollama-style role/content
        dicts) and return an iterator of reply-text tokens.

        Must raise immediately (before returning) if the connection/request
        itself fails, so callers can tell "never started" apart from "started
        then failed mid-stream" - main.py's ask_llm_stream/_stream_llm_answer
        log and recover from those two cases differently. Once iteration
        starts, must stop yielding promptly when the caller's cancellation
        signal (barge-in / shutdown) fires, not just when the underlying
        stream naturally ends."""
        raise NotImplementedError

    @abstractmethod
    def chat_with_tools(self, messages, tools):
        """Non-streaming: a tool call is a single structured decision, not
        something meaningful to stream token-by-token. Returns a dict with
        at least 'content' (str, possibly empty) and 'tool_calls' (a list,
        possibly empty, of {'name': str, 'arguments': dict}).

        See config.py's llm_tool_calling_enabled docstring before assuming
        this is reliable enough to act on unconditionally - it is real,
        working infrastructure, not a placeholder, but is off by default for
        a concrete, tested reason."""
        raise NotImplementedError
