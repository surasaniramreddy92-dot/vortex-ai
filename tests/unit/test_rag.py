"""Unit tests for the 2026-08-16 page/section provenance additions to
src/vortex/rag.py: page-aware chunking, the extract_pages-failure fallback,
and citation labeling in the assembled RAG prompt.

Deliberately does NOT test RagStore itself (its __init__ makes real Postgres/
Qdrant connections) - these test the pure, side-effect-free helper functions
it and main.py's answer_document_question() rely on, so they run with no
live services required. See tests/integration for anything that does need
Postgres/Qdrant.

Run with: pytest tests/unit/test_rag.py
"""
from unittest.mock import patch

import pytest

pytest.importorskip('psycopg2', reason='psycopg2 not installed')
pytest.importorskip('qdrant_client', reason='qdrant-client not installed')

from vortex import rag, documents


def test_chunk_pages_attaches_page_number_to_every_chunk():
    pages = [
        {'page': 1, 'section': None, 'text': 'A' * 900},
        {'page': 2, 'section': None, 'text': 'Short page two text.'},
    ]
    chunks = rag._chunk_pages(pages)
    assert all(c['page'] in (1, 2) for c in chunks)
    # page 1's 900 chars (> CHUNK_SIZE=800) should split into 2 chunks, both tagged page 1
    page1_chunks = [c for c in chunks if c['page'] == 1]
    assert len(page1_chunks) == 2
    page2_chunks = [c for c in chunks if c['page'] == 2]
    assert len(page2_chunks) == 1
    assert page2_chunks[0]['content'] == 'Short page two text.'


def test_chunk_pages_attaches_section_when_no_page():
    pages = [{'page': None, 'section': 'Budget', 'text': 'Q3 revenue was five million dollars.'}]
    chunks = rag._chunk_pages(pages)
    assert len(chunks) == 1
    assert chunks[0]['page'] is None
    assert chunks[0]['section'] == 'Budget'


def test_chunk_pages_empty_text_produces_no_chunks():
    pages = [{'page': 1, 'section': None, 'text': ''}]
    assert rag._chunk_pages(pages) == []


def test_chunk_with_provenance_falls_back_when_extract_pages_raises():
    """A documents.py bug (e.g. an unreadable file) must not break ingestion
    entirely - it should degrade to flat-text chunking with no provenance."""
    with patch.object(documents, 'extract_pages', side_effect=RuntimeError('corrupt file')):
        chunks = rag._chunk_with_provenance('/fake/path.pdf', 'Fallback flat text content here.')
    assert chunks == [{'content': 'Fallback flat text content here.', 'page': None, 'section': None}]


def test_chunk_with_provenance_falls_back_when_extract_pages_empty():
    with patch.object(documents, 'extract_pages', return_value=[]):
        chunks = rag._chunk_with_provenance('/fake/path.pdf', 'Another fallback text here.')
    assert chunks == [{'content': 'Another fallback text here.', 'page': None, 'section': None}]


def test_chunk_with_provenance_uses_page_data_when_available():
    fake_pages = [{'page': 5, 'section': None, 'text': 'Real page-tagged content here.'}]
    with patch.object(documents, 'extract_pages', return_value=fake_pages):
        chunks = rag._chunk_with_provenance('/fake/path.pdf', 'irrelevant flat text')
    assert chunks == [{'content': 'Real page-tagged content here.', 'page': 5, 'section': None}]


def test_citation_label_prefers_page_over_section():
    assert rag._citation_label(3, 'Budget') == '[Page 3] '


def test_citation_label_uses_section_when_no_page():
    assert rag._citation_label(None, 'Budget') == '[Budget] '


def test_citation_label_empty_when_neither_known():
    assert rag._citation_label(None, None) == ''


def test_build_rag_prompt_includes_page_citation():
    chunks = [{'content': 'The Q3 revenue was 5 million.', 'page': 3, 'section': None}]
    prompt = rag.build_rag_prompt(chunks, 'what was Q3 revenue?')
    assert '[Page 3]' in prompt
    assert 'The Q3 revenue was 5 million.' in prompt
    assert 'what was Q3 revenue?' in prompt


def test_build_rag_prompt_includes_section_citation_when_no_page():
    chunks = [{'content': 'No page info here.', 'page': None, 'section': 'Summary'}]
    prompt = rag.build_rag_prompt(chunks, 'what does it say?')
    assert '[Summary]' in prompt


def test_build_rag_prompt_no_citation_when_neither_known():
    """No per-chunk label should be prefixed onto the excerpt itself - the
    general citation *instruction* sentence always mentions "[Page N]" as a
    format example regardless, so this checks the excerpt line specifically,
    not prompt-wide absence of that substring."""
    chunks = [{'content': 'Neither page nor section known.', 'page': None, 'section': None}]
    prompt = rag.build_rag_prompt(chunks, 'q')
    assert '\nNeither page nor section known.' in prompt  # unprefixed, own line
    assert '] Neither page nor section known.' not in prompt


def test_build_rag_prompt_accepts_plain_strings_for_backward_compat():
    """retrieve() now returns dicts, but build_rag_prompt should still accept
    a plain list of strings without raising - defensive against any other
    caller/test that passes raw chunk text."""
    prompt = rag.build_rag_prompt(['plain chunk one', 'plain chunk two'], 'q')
    assert 'plain chunk one' in prompt
    assert 'plain chunk two' in prompt


def test_build_rag_prompt_omits_citations_when_page_numbers_disabled(monkeypatch):
    monkeypatch.setattr(rag, 'INCLUDE_PAGE_NUMBERS', False)
    chunks = [{'content': 'Some content.', 'page': 3, 'section': None}]
    prompt = rag.build_rag_prompt(chunks, 'q')
    assert '[Page 3]' not in prompt
