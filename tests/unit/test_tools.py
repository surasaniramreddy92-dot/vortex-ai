"""Unit tests for llm/tools.py, added alongside the Phase 4 tool-calling
infrastructure (2026-08-19). tool_call_to_intent() is the safety-critical
part: see its docstring and config.py's llm_tool_calling_enabled docstring
for the live evidence (llama3.2:1b hallucinating calls and mangling
arguments) that makes the defensive/fail-closed behavior here load-bearing,
not defensive-for-its-own-sake.
"""
import sys

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.core import intent_router
from vortex.llm.tools import TOOL_SCHEMAS, tool_call_to_intent


def test_schemas_cover_only_safe_non_destructive_tools():
    names = {schema['function']['name'] for schema in TOOL_SCHEMAS}
    assert names == {'open_app', 'web_search', 'get_time', 'get_date'}


def test_open_app_well_formed():
    intent = tool_call_to_intent('open_app', {'target': 'chrome'})
    assert intent == intent_router.OpenTarget('chrome')


def test_web_search_well_formed():
    intent = tool_call_to_intent('web_search', {'query': 'weather today'})
    assert intent == intent_router.WebSearch('weather today')


def test_get_time_well_formed_ignores_extra_args():
    intent = tool_call_to_intent('get_time', {})
    assert intent == intent_router.SpeakTime()


def test_get_date_well_formed():
    intent = tool_call_to_intent('get_date', {})
    assert intent == intent_router.SpeakDate()


def test_unknown_tool_name_returns_none():
    assert tool_call_to_intent('delete_everything', {'target': 'x'}) is None


def test_missing_required_argument_returns_none():
    assert tool_call_to_intent('open_app', {}) is None


def test_non_dict_arguments_returns_none():
    assert tool_call_to_intent('open_app', 'chrome') is None
    assert tool_call_to_intent('open_app', None) is None
    assert tool_call_to_intent('open_app', ['chrome']) is None


def test_non_string_argument_value_returns_none():
    assert tool_call_to_intent('open_app', {'target': 123}) is None
    assert tool_call_to_intent('open_app', {'target': None}) is None


def test_blank_string_argument_returns_none():
    assert tool_call_to_intent('open_app', {'target': '   '}) is None


def test_argument_value_is_stripped():
    intent = tool_call_to_intent('web_search', {'query': '  cats  '})
    assert intent == intent_router.WebSearch('cats')


def test_schema_echoed_back_instead_of_value_returns_none():
    """The exact malformed shape observed live from llama3.2:1b: instead of
    the extracted string value, the model echoed the parameter's own JSON
    schema dict back as the "value"."""
    echoed_schema = {'type': 'string', 'description': 'the app or site name'}
    assert tool_call_to_intent('open_app', {'target': echoed_schema}) is None
