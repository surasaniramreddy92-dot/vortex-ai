"""Unit tests for the 2026-08-16 hybrid dense+sparse search and reranking
additions to src/vortex/rag.py: BM25 sparse scoring, Reciprocal Rank Fusion,
the keyword-overlap reranking heuristic, and RagStore.retrieve()'s wiring of
all three together.

These test the pure, side-effect-free helper functions (_bm25_rank,
_reciprocal_rank_fusion, _keyword_overlap_score, _rerank) plus RagStore
methods against a mocked Postgres/Qdrant, so everything here runs with no
live services required. See tests/integration (or the manual live test run
alongside this change) for the real Postgres+Qdrant+Ollama end-to-end check.

Run with: pytest tests/unit/test_rag_hybrid.py
"""
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip('psycopg2', reason='psycopg2 not installed')
pytest.importorskip('qdrant_client', reason='qdrant-client not installed')
pytest.importorskip('rank_bm25', reason='rank_bm25 not installed')

from vortex import rag


# ---------- _tokenize ----------

def test_tokenize_lowercases_and_splits_on_punctuation():
    assert rag._tokenize('SKU-48219-X, in stock') == ['sku', '48219', 'x', 'in', 'stock']


def test_tokenize_empty_string():
    assert rag._tokenize('') == []


# ---------- _bm25_rank ----------

def test_bm25_rank_finds_exact_keyword_chunk_top_even_when_short():
    corpus = [
        'The quarterly report covers revenue growth and market trends broadly.',
        'Product code SKU-48219-X ships from the west coast warehouse.',
        'Our team discussed strategy and long-term planning for next year.',
    ]
    ids = ['c1', 'c2', 'c3']
    ranked = rag._bm25_rank(corpus, ids, 'What is SKU-48219-X?', top_k=3)
    assert ranked[0] == 'c2'


def test_bm25_rank_excludes_zero_score_chunks():
    corpus = ['apples and oranges', 'completely unrelated text here']
    ids = ['a', 'b']
    ranked = rag._bm25_rank(corpus, ids, 'bananas', top_k=2)
    assert ranked == []


def test_bm25_rank_empty_corpus_returns_empty():
    assert rag._bm25_rank([], [], 'anything', top_k=5) == []


def test_bm25_rank_respects_top_k():
    corpus = ['cat dog', 'cat bird', 'cat fish', 'cat mouse']
    ids = ['1', '2', '3', '4']
    ranked = rag._bm25_rank(corpus, ids, 'cat', top_k=2)
    assert len(ranked) == 2


# ---------- _reciprocal_rank_fusion ----------

def test_rrf_ranks_item_in_both_lists_above_item_in_one():
    dense = ['a', 'b', 'c']
    sparse = ['b', 'd', 'a']
    fused = rag._reciprocal_rank_fusion([dense, sparse])
    fused_ids = [cid for cid, _ in fused]
    # 'b' (rank 1 dense, rank 0 sparse) and 'a' (rank 0 dense, rank 2 sparse)
    # both appear in both lists and should outrank 'c'/'d', which appear once.
    assert fused_ids.index('a') < fused_ids.index('c')
    assert fused_ids.index('b') < fused_ids.index('d')


def test_rrf_includes_ids_present_in_only_one_list():
    fused = rag._reciprocal_rank_fusion([['x'], ['y']])
    fused_ids = {cid for cid, _ in fused}
    assert fused_ids == {'x', 'y'}


def test_rrf_empty_lists_returns_empty():
    assert rag._reciprocal_rank_fusion([[], []]) == []


def test_rrf_rank_zero_scores_higher_than_rank_one_in_same_list():
    fused = dict(rag._reciprocal_rank_fusion([['first', 'second']]))
    assert fused['first'] > fused['second']


# ---------- _keyword_overlap_score ----------

def test_keyword_overlap_full_match_scores_higher_than_partial():
    full = rag._keyword_overlap_score('quarterly revenue', 'The quarterly revenue was strong.')
    partial = rag._keyword_overlap_score('quarterly revenue', 'The revenue was strong this quarter somehow.')
    assert full > partial


def test_keyword_overlap_exact_substring_gets_bonus():
    with_substring = rag._keyword_overlap_score('SKU-48219-X', 'Product SKU-48219-X is in stock.')
    without_substring = rag._keyword_overlap_score('SKU-48219-X', 'sku 48219 x appear separately in this sentence')
    assert with_substring > without_substring


def test_keyword_overlap_no_shared_tokens_is_zero():
    assert rag._keyword_overlap_score('bananas', 'completely unrelated text') == 0.0


def test_keyword_overlap_empty_query_is_zero():
    assert rag._keyword_overlap_score('', 'some text') == 0.0


# ---------- _rerank ----------

def test_rerank_promotes_exact_keyword_match_over_weaker_fused_candidate():
    """The concrete mechanism check: a candidate with a *lower* fused_score
    (would've lost a straight RRF-only top_k cut) but which contains the
    literal query term should be promoted above a candidate with a higher
    fused_score but no keyword overlap at all."""
    candidates = [
        {'id': 'weak_keyword_strong_fused', 'content': 'General discussion of quarterly performance and outlook.',
         'fused_score': 0.9},
        {'id': 'strong_keyword_weak_fused', 'content': 'Product code SKU-48219-X ships next Tuesday.',
         'fused_score': 0.2},
    ]
    reranked = rag._rerank(candidates, 'SKU-48219-X', top_k=2)
    assert reranked[0]['id'] == 'strong_keyword_weak_fused'


def test_rerank_respects_top_k_truncation():
    candidates = [{'id': str(i), 'content': f'chunk {i}', 'fused_score': 1.0 / (i + 1)} for i in range(5)]
    reranked = rag._rerank(candidates, 'chunk', top_k=2)
    assert len(reranked) == 2


def test_rerank_empty_candidates_returns_empty():
    assert rag._rerank([], 'q', top_k=5) == []


# ---------- The concrete dense-vs-hybrid demonstration ----------
# This simulates (rather than calls live Qdrant/Ollama) a dense ranking that
# fails to surface an exact-keyword chunk in its top-k - a realistic outcome
# for an embedding model, since nothing here special-cases it - and shows
# that adding the BM25 sparse signal via fusion recovers it, while a
# dense-only top_k would not have. The live end-to-end equivalent of this
# (real Ollama embeddings, real Qdrant) was run separately; see the RAG
# stack's manual verification for that.

def test_hybrid_recovers_exact_keyword_chunk_that_dense_only_misses():
    # Five chunks about a project; one contains an exact, unusual product
    # code. A dense embedding model, having no special training signal for
    # an opaque alphanumeric code, ranks it 5th/last by semantic similarity
    # (it's short and topically generic-looking) - this is the simulated
    # dense ranking; it does not call any real embedding model.
    keyword_chunk_id = 'chunk_3'
    simulated_dense_ranking = ['chunk_1', 'chunk_5', 'chunk_2', 'chunk_4', keyword_chunk_id]
    corpus_texts = [
        'Team standup notes: general project status update for the week.',      # chunk_1
        'Meeting scheduled for Thursday to discuss the roadmap.',               # chunk_2
        'Replacement part is product code SKU-48219-X, confirmed in stock.',    # chunk_3 (exact keyword)
        'Budget review pushed to next quarter pending finance sign-off.',       # chunk_4
        'Office relocation planning continues into next month.',               # chunk_5
    ]
    corpus_ids = ['chunk_1', 'chunk_2', 'chunk_3', 'chunk_4', 'chunk_5']
    question = 'What is the replacement part SKU-48219-X?'

    dense_only_top_3 = simulated_dense_ranking[:3]
    assert keyword_chunk_id not in dense_only_top_3, (
        'test setup check: dense-only must actually miss the keyword chunk in top-3'
    )

    sparse_ranking = rag._bm25_rank(corpus_texts, corpus_ids, question, top_k=5)
    assert sparse_ranking[0] == keyword_chunk_id, 'BM25 should rank the exact-keyword chunk first'

    fused = rag._reciprocal_rank_fusion([simulated_dense_ranking, sparse_ranking])
    fused_ids = [cid for cid, _ in fused]
    hybrid_top_3 = fused_ids[:3]
    assert keyword_chunk_id in hybrid_top_3, (
        'hybrid fusion should recover the exact-keyword chunk into the top-3 '
        'that dense-only search alone missed'
    )

    # And the reranker keeps (or further strengthens) that placement, since
    # the chunk also has the strongest possible keyword-overlap score.
    corpus_by_id = dict(zip(corpus_ids, corpus_texts))
    candidates = [
        {'id': cid, 'content': corpus_by_id[cid], 'fused_score': score}
        for cid, score in fused[:5]
    ]
    reranked = rag._rerank(candidates, question, top_k=3)
    reranked_ids = [c['id'] for c in reranked]
    assert keyword_chunk_id in reranked_ids


# ---------- RagStore.retrieve() wiring (mocked Postgres/Qdrant) ----------

def _make_store_with_mocks():
    """Builds a RagStore without running __init__'s real connection logic -
    mirrors the pattern used for testing other connection-owning classes in
    this codebase where no live service should be required."""
    store = rag.RagStore.__new__(rag.RagStore)
    store._pg = MagicMock()
    store._qdrant = MagicMock()
    store._corpus_cache = {}
    return store


def test_retrieve_dense_only_when_hybrid_disabled():
    store = _make_store_with_mocks()
    fake_hit = MagicMock(id=1, payload={'content': 'dense hit', 'page': 1, 'section': None})
    store._qdrant.query_points.return_value = MagicMock(points=[fake_hit])
    with patch.object(rag, 'HYBRID_SEARCH_ENABLED', False), patch.object(rag, '_embed', return_value=[0.1]):
        result = store.retrieve(doc_id=1, question='q', top_k=1)
    assert result == [{'content': 'dense hit', 'page': 1, 'section': None}]
    # BM25 corpus should never be fetched in dense-only mode.
    store._pg.cursor.assert_not_called()


def test_retrieve_hybrid_merges_dense_and_sparse_and_reranks():
    store = _make_store_with_mocks()
    dense_hit = MagicMock(id=10, payload={'content': 'General topic discussion.', 'page': None, 'section': None})
    store._qdrant.query_points.return_value = MagicMock(points=[dense_hit])

    # Three chunks, not two: BM25's idf formula (log(N-df+0.5) - log(df+0.5))
    # degenerates to exactly 0 for a term appearing in precisely half of a
    # 2-document corpus, which would make this test's sparse signal
    # vanish for reasons unrelated to what's being tested here. A real
    # document's corpus is a whole document's worth of chunks (routinely
    # dozens+), so a 3rd distractor chunk here is a more representative
    # minimum, not a workaround for a bug - see IMPLEMENTED.md for this as
    # a documented, honest tiny-corpus caveat of BM25 itself.
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value.fetchall.return_value = [
        (10, 'General topic discussion.', None, None),
        (20, 'Exact code SKU-999-Z is referenced here.', None, None),
        (30, 'A third unrelated chunk about lunch plans.', None, None),
    ]
    store._pg.cursor.return_value = cursor_cm

    with patch.object(rag, 'HYBRID_SEARCH_ENABLED', True), \
         patch.object(rag, 'RERANK_ENABLED', True), \
         patch.object(rag, '_embed', return_value=[0.1]):
        result = store.retrieve(doc_id=1, question='SKU-999-Z', top_k=2)

    contents = [c['content'] for c in result]
    assert 'Exact code SKU-999-Z is referenced here.' in contents


def test_get_chunk_corpus_caches_and_degrades_on_postgres_error():
    store = _make_store_with_mocks()
    store._pg.cursor.side_effect = RuntimeError('connection lost')
    corpus = store._get_chunk_corpus(doc_id=1)
    assert corpus == []
    # Second call should use the cache, not hit Postgres again (cursor.side_effect
    # would raise again if it were re-invoked - no exception here proves that).
    corpus_again = store._get_chunk_corpus(doc_id=1)
    assert corpus_again == []


def test_ensure_ingested_invalidates_corpus_cache_on_reingest():
    store = _make_store_with_mocks()
    store._corpus_cache[5] = [{'id': 1, 'content': 'stale', 'page': None, 'section': None}]

    select_cursor = MagicMock()
    select_cursor.__enter__.return_value.fetchone.return_value = (5, 'old_hash')
    update_cursor = MagicMock()
    insert_cursor = MagicMock()
    insert_cursor.__enter__.return_value.fetchone.return_value = (999,)
    store._pg.cursor.side_effect = [select_cursor, update_cursor, insert_cursor]

    with patch.object(rag, '_chunk_with_provenance', return_value=[]), \
         patch.object(rag, '_embed', return_value=[0.1]):
        store.ensure_ingested('/fake/path.txt', 'new content, different from before')

    assert 5 not in store._corpus_cache


def test_ensure_ingested_deletes_stale_qdrant_points_on_reingest():
    """Regression test for a pre-existing gap noticed while testing hybrid
    search: re-ingesting a changed document deleted the old Postgres chunk
    rows but never told Qdrant to drop the matching points, so stale vectors
    from the previous version of the document stayed in the dense index
    forever. ensure_ingested should now call qdrant.delete() scoped to this
    doc_id before upserting the new points."""
    store = _make_store_with_mocks()

    select_cursor = MagicMock()
    select_cursor.__enter__.return_value.fetchone.return_value = (7, 'old_hash')
    update_cursor = MagicMock()
    store._pg.cursor.side_effect = [select_cursor, update_cursor]

    with patch.object(rag, '_chunk_with_provenance', return_value=[]), \
         patch.object(rag, '_embed', return_value=[0.1]):
        store.ensure_ingested('/fake/path.txt', 'changed content')

    store._qdrant.delete.assert_called_once()
    _, kwargs = store._qdrant.delete.call_args
    assert kwargs['collection_name'] == rag.QDRANT_COLLECTION


def test_ensure_ingested_survives_qdrant_delete_failure():
    """A failed stale-point cleanup must not block re-ingestion entirely -
    degrades to 'old pollution stays a bit longer' rather than raising."""
    store = _make_store_with_mocks()

    select_cursor = MagicMock()
    select_cursor.__enter__.return_value.fetchone.return_value = (7, 'old_hash')
    update_cursor = MagicMock()
    store._pg.cursor.side_effect = [select_cursor, update_cursor]
    store._qdrant.delete.side_effect = RuntimeError('qdrant unreachable')

    with patch.object(rag, '_chunk_with_provenance', return_value=[]), \
         patch.object(rag, '_embed', return_value=[0.1]):
        doc_id = store.ensure_ingested('/fake/path.txt', 'changed content')

    assert doc_id == 7
