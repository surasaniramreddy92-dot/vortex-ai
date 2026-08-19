"""Unit tests for the 2026-08-19 conversation-memory retrieval addition to
src/vortex/rag.py: RagStore.index_turn/search_turns and build_memory_prompt.

Live-verified separately (not just here): a real conversation turn about
"my favorite programming language is Rust" was indexed via a real
background thread, then a real "do you remember my favorite programming
language" recall correctly retrieved it and answered from it via a real
Ollama call - see this feature's commit message for the transcript. These
tests exercise RagStore's own logic against a mocked Postgres/Qdrant,
matching test_rag_hybrid.py's established pattern, so the suite runs with
no live services required.
"""
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

pytest.importorskip('psycopg2', reason='psycopg2 not installed')
pytest.importorskip('qdrant_client', reason='qdrant-client not installed')

from vortex import rag


def _make_store_with_mocks():
    store = rag.RagStore.__new__(rag.RagStore)
    store._pg = MagicMock()
    store._qdrant = MagicMock()
    store._corpus_cache = {}
    return store


# ---------- index_turn ----------

def test_index_turn_embeds_and_upserts_with_the_turn_id_as_point_id(monkeypatch):
    store = _make_store_with_mocks()
    monkeypatch.setattr(rag, '_embed', lambda text: [0.1, 0.2, 0.3])

    store.index_turn(42, 'user', 'my favorite language is rust')

    store._qdrant.upsert.assert_called_once()
    _, kwargs = store._qdrant.upsert.call_args
    assert kwargs['collection_name'] == rag.QDRANT_TURNS_COLLECTION
    point = kwargs['points'][0]
    assert point.id == 42
    assert point.vector == [0.1, 0.2, 0.3]
    assert point.payload == {'role': 'user', 'content': 'my favorite language is rust'}


def test_index_turn_propagates_embed_failures():
    """Deliberately does NOT swallow exceptions - app.py's
    _index_turn_async is what catches/logs, so a real bug here shows up as
    a real, loud failure in that caller's own test coverage, not silently
    here."""
    store = _make_store_with_mocks()

    def boom(text):
        raise RuntimeError('ollama unreachable')
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rag, '_embed', boom)
        with pytest.raises(RuntimeError):
            store.index_turn(1, 'user', 'hi')


# ---------- search_turns ----------

def test_search_turns_returns_role_and_content_most_relevant_first(monkeypatch):
    store = _make_store_with_mocks()
    monkeypatch.setattr(rag, '_embed', lambda text: [0.5])
    hit1 = MagicMock(payload={'role': 'user', 'content': 'I like Rust'})
    hit2 = MagicMock(payload={'role': 'assistant', 'content': 'Rust has great safety guarantees'})
    store._qdrant.query_points.return_value = MagicMock(points=[hit1, hit2])

    result = store.search_turns('what language do I like', top_k=5)

    assert result == [
        {'role': 'user', 'content': 'I like Rust'},
        {'role': 'assistant', 'content': 'Rust has great safety guarantees'},
    ]
    _, kwargs = store._qdrant.query_points.call_args
    assert kwargs['collection_name'] == rag.QDRANT_TURNS_COLLECTION
    assert kwargs['limit'] == 5


def test_search_turns_no_hits_returns_empty_list(monkeypatch):
    store = _make_store_with_mocks()
    monkeypatch.setattr(rag, '_embed', lambda text: [0.5])
    store._qdrant.query_points.return_value = MagicMock(points=[])
    assert store.search_turns('anything') == []


# ---------- build_memory_prompt ----------

def test_build_memory_prompt_includes_role_and_content():
    turns = [{'role': 'user', 'content': 'I like Rust'}, {'role': 'assistant', 'content': 'Good choice'}]
    prompt = rag.build_memory_prompt(turns, 'what language do I like')
    assert 'user: I like Rust' in prompt
    assert 'assistant: Good choice' in prompt
    assert 'what language do I like' in prompt


def test_build_memory_prompt_instructs_not_to_guess():
    prompt = rag.build_memory_prompt([{'role': 'user', 'content': 'x'}], 'q')
    assert "don't guess" in prompt or 'say so plainly' in prompt
