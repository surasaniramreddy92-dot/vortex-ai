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
