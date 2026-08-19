"""Structured tool-calling (Phase 4 gap) - schema definitions plus a
defensive mapper from a raw Ollama tool call back to an existing
core/intent_router.py Intent, so a granted tool call reuses the exact same
dispatch path (core/capability_registry.py) every regex-matched command
already goes through, instead of a second, parallel execution path that
could drift from it.

See config.py's llm_tool_calling_enabled docstring for why this is real,
tested infrastructure that's nonetheless off by default: live-tested
2026-08-19, llama3.2:1b (the only locally-available model that accepts
Ollama's tools parameter at all - llama3:latest and phi:latest both reject
it outright) called the test tool for a plain unrelated general-knowledge
question, and mangled arguments on calls that were topically right (echoed
the JSON schema itself back instead of the extracted value). Deliberately
scoped to a handful of safe, non-destructive tools only - a hallucinated or
malformed *destructive* tool call (closing apps, deleting a file, shutting
down) would be a materially worse failure mode than a hallucinated
open_app/web_search call, so those stay out of this list regardless of
whether this feature is ever enabled.

tool_call_to_intent() is deliberately defensive about the arguments shape
for exactly this reason - a model that echoes the schema back instead of
the extracted value must fail closed (return None, dispatch nothing)
rather than construct an Intent from garbage and let capability_registry
act on it.
"""
from ..core import intent_router as intents

TOOL_SCHEMAS = [
    {
        'type': 'function',
        'function': {
            'name': 'open_app',
            'description': 'Open a named application or website.',
            'parameters': {
                'type': 'object',
                'properties': {'target': {'type': 'string', 'description': 'the app or site name'}},
                'required': ['target'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'web_search',
            'description': 'Search the web for a query.',
            'parameters': {
                'type': 'object',
                'properties': {'query': {'type': 'string', 'description': 'what to search for'}},
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_time',
            'description': 'Get the current time.',
            'parameters': {'type': 'object', 'properties': {}},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_date',
            'description': "Get today's date.",
            'parameters': {'type': 'object', 'properties': {}},
        },
    },
]

# name -> (Intent class, required string-argument names, in constructor order)
_TOOL_TO_INTENT = {
    'open_app': (intents.OpenTarget, ('target',)),
    'web_search': (intents.WebSearch, ('query',)),
    'get_time': (intents.SpeakTime, ()),
    'get_date': (intents.SpeakDate, ()),
}


def tool_call_to_intent(name, arguments):
    """Returns an Intent instance, or None if `name` isn't a known tool or
    `arguments` doesn't have the expected flat {str: str} shape a real,
    correctly-formed call would have - fails closed rather than guessing."""
    entry = _TOOL_TO_INTENT.get(name)
    if entry is None:
        return None
    intent_cls, required_args = entry
    if not isinstance(arguments, dict):
        return None
    values = []
    for arg_name in required_args:
        value = arguments.get(arg_name)
        if not isinstance(value, str) or not value.strip():
            return None
        values.append(value.strip())
    return intent_cls(*values)
