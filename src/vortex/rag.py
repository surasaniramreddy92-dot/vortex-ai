"""RAG: chunking, embedding, and retrieval for document question-answering
(Phase 5, full-stack version).

This replaces the "dump the whole document into the prompt, truncated" approach
in documents.py's plain summarize path with real retrieval for question-
answering specifically: a document is chunked once (cached in Postgres), each
chunk is embedded (via Ollama's local nomic-embed-text model) and indexed in
Qdrant, and a question retrieves only the chunks actually relevant to it -
so a 50-page document no longer silently loses everything past the truncation
cutoff for QA.

Postgres is the transactional source of truth (documents + their chunk text);
Qdrant is the vector index for similarity search. Each chunk's text is also
copied into its Qdrant payload so retrieval doesn't need a Postgres round trip
in the hot path - Postgres remains authoritative and re-ingestable if the
Qdrant collection is ever rebuilt.

Deliberately NOT built here: cross-document retrieval, or migrating
conversation history off SQLite (see memory.py - not attempted in this pass,
scoped out as too large to do safely in one sitting; see IMPLEMENTED.md).
This file is scoped to "answer a question about one document, well," which is
the concrete gap that justified adding this stack at all.

Page/section provenance (added 2026-08-16): `ensure_ingested(path, text)` kept
its original signature (a caller in main.py passes an already-extracted flat
`text` string, used here for hashing/change-detection exactly as before) but
now also calls documents.extract_pages(path) internally to get page/section-
tagged text, and chunks *that* instead of the flat string, so every stored
chunk carries where it actually came from. If extract_pages() fails or the
format has no page/section structure, this falls back to chunking the flat
`text` with page=section=None - retrieval and prompt assembly both already
handle that (no page/section to cite, so none is claimed).

Hybrid dense+sparse search and reranking (added 2026-08-16): `retrieve()` now
pulls a candidate pool from two independent retrieval methods - Qdrant's dense
cosine-similarity search (semantic/paraphrase-robust, but blurs exact tokens
into nearby vector-space points) and BM25 keyword search via `rank_bm25` over
the document's full chunk text (exact-term precision, no semantic
understanding at all) - fuses them with Reciprocal Rank Fusion, then reranks
the fused pool with a keyword-overlap heuristic before truncating to the
final top_k. See `_bm25_rank`, `_reciprocal_rank_fusion`, and `_rerank` below
for the reasoning behind each choice, including explicitly why this does NOT
use a cross-encoder reranker: `sentence-transformers` (the standard choice)
pulls in torch - verified via `pip install sentence-transformers --dry-run`
before writing this, which resolved a 122MB Windows wheel (torch 2.13.0,
cp311, win_amd64) plus transformers/tokenizers/safetensors/networkx/jinja2,
and a real cross-encoder reranker model would be a *further*, separate
download the first time it's actually used (typically tens to hundreds of MB
more). That's a real footprint mismatch for an otherwise fully local,
lightweight desktop app that already keeps numpy pinned to 1.x specifically
to avoid destabilizing the ONNX wake-word stack - not installed here. Both
new stages are individually off-switchable (`VORTEX_RAG_HYBRID_SEARCH`,
`VORTEX_RAG_RERANK`) so either can be ruled out or backed out without a code
change if it ever regresses answer quality or latency.
"""
import hashlib
import logging
import os
import re

import ollama
import psycopg2
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams, FieldCondition, Filter, MatchValue
from rank_bm25 import BM25Okapi

from . import documents

POSTGRES_DSN = os.getenv('VORTEX_POSTGRES_DSN', 'dbname=vortex user=vortex password=vortex_local_dev host=localhost')
QDRANT_URL = os.getenv('VORTEX_QDRANT_URL', 'http://localhost:6333')
QDRANT_COLLECTION = 'vortex_chunks'
# Conversation-memory retrieval (2026-08-19): a second, separate Qdrant
# collection for indexed conversation turns - reuses this same RagStore's
# existing Postgres+Qdrant connections rather than standing up a second
# pair (memory.py's module docstring explicitly scoped this out before as
# "a much bigger, later-stage build"; it's smaller than that framing
# suggested once document RAG's infrastructure already existed to build on
# top of - no chunking needed, a turn already is the retrieval unit, and no
# Postgres schema at all: SQLite in memory.py stays the sole source of
# truth for turns, Qdrant here is purely a similarity index over them).
QDRANT_TURNS_COLLECTION = 'vortex_conversation_turns'
EMBED_MODEL = os.getenv('VORTEX_EMBED_MODEL', 'nomic-embed-text')
EMBED_DIM = 768  # nomic-embed-text's output size
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
TOP_K = 5
# How many candidates each of dense/sparse search contributes going into
# fusion, and how many fused candidates survive to the reranking pass, before
# the final truncation to the caller's requested top_k. Deliberately wider
# than TOP_K: fusion and reranking can only reorder what they're handed, so
# the pool has to be generous enough that a chunk either single method ranked
# just outside top_k still gets a chance to be promoted back up by the other
# signal.
CANDIDATE_POOL = 20
RERANK_POOL = 10
# The constant from the original Reciprocal Rank Fusion paper (Cormack,
# Clarke & Buettcher, SIGIR 2009). Not sensitive to tuning here - see
# _reciprocal_rank_fusion's docstring for what it does and why RRF was chosen
# over a weighted-score blend.
RRF_K = 60
# Mirrors VortexConfig.document_page_numbers - see documents.py's OCR_ENABLED
# comment for why this stays a plain module constant rather than a config.py
# import (matching this file's own pre-existing pattern for POSTGRES_DSN etc.).
INCLUDE_PAGE_NUMBERS = os.getenv('VORTEX_DOCUMENT_PAGE_NUMBERS', 'true').strip().lower() not in (
    '0', 'false', 'no', 'off')
# Mirrors VortexConfig.rag_hybrid_search / rag_rerank (same non-import
# pattern as INCLUDE_PAGE_NUMBERS above). Both default on; each is
# independently switchable so either new stage can be ruled out - or turned
# off for latency reasons, see IMPLEMENTED.md for the measured per-query
# cost - without a code change.
HYBRID_SEARCH_ENABLED = os.getenv('VORTEX_RAG_HYBRID_SEARCH', 'true').strip().lower() not in (
    '0', 'false', 'no', 'off')
RERANK_ENABLED = os.getenv('VORTEX_RAG_RERANK', 'true').strip().lower() not in (
    '0', 'false', 'no', 'off')

_log = logging.getLogger('vortex.rag')

_TOKEN_RE = re.compile(r'[a-z0-9]+')


def _tokenize(text):
    """Lowercase alphanumeric tokens. Used by both BM25 and the keyword-overlap
    reranker so a query and a chunk are compared on the same terms - e.g.
    'SKU-48219-X' becomes ['sku', '48219', 'x'] consistently on both sides,
    punctuation-insensitive."""
    return _TOKEN_RE.findall(text.lower())


def _embed(text):
    # keep_alive: document ingestion can call this many times in a row (one per
    # chunk) - without it Ollama's default ~5min idle-unload can trigger mid-batch.
    res = ollama.embeddings(model=EMBED_MODEL, prompt=text, keep_alive='30m')
    return res['embedding']


def _chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Overlapping fixed-size windows, snapped to the nearest paragraph/sentence
    break where one exists nearby, so chunks don't split mid-sentence when avoidable."""
    chunks = []
    i = 0
    n = len(text)
    while i < n:
        end = min(i + size, n)
        if end < n:
            for sep in ('\n\n', '. ', '\n'):
                cut = text.rfind(sep, i, end)
                if cut > i + size // 2:
                    end = cut + len(sep)
                    break
        chunk = text[i:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        i = max(end - overlap, i + 1)
    return chunks


def _chunk_pages(pages, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Chunk each page/section entry independently (rather than flattening the
    whole document into one string first), so every resulting chunk can carry
    accurate page/section provenance - flattening first would lose the
    boundary the moment two pages' text got concatenated. Returns a list of
    {'content', 'page', 'section'} dicts, in document order."""
    result = []
    for entry in pages:
        for chunk in _chunk_text(entry.get('text', ''), size=size, overlap=overlap):
            result.append({'content': chunk, 'page': entry.get('page'), 'section': entry.get('section')})
    return result


def _chunk_with_provenance(path, text):
    """Best-effort page/section-aware chunking via documents.extract_pages(path);
    falls back to plain flat-text chunking (no page/section) if that raises or
    is empty, so a documents.py bug never breaks ingestion entirely."""
    try:
        pages = documents.extract_pages(path)
        if pages:
            chunks = _chunk_pages(pages)
            if chunks:
                return chunks
    except Exception as e:
        _log.warning(f'extract_pages failed for {path}, falling back to flat-text chunking: {e}')
    return [{'content': c, 'page': None, 'section': None} for c in _chunk_text(text)]


def _bm25_rank(corpus_texts, corpus_ids, query, top_k):
    """Sparse (BM25) retrieval over one document's full chunk corpus. Returns
    chunk ids ordered best-first.

    BM25 scores a chunk higher when it contains the query's exact terms,
    weighted by how rare those terms are across the corpus and normalized for
    chunk length - unlike dense embedding similarity, it doesn't blur an
    exact token (a product code, a specific number, an uncommon proper noun)
    into a nearby point in vector space; it either literally contains the
    token or it doesn't. That's the concrete, well-understood gap this
    closes: dense retrieval is strong on paraphrase/semantic similarity and
    comparatively weak on exact-keyword precision; BM25 is the reverse.
    `rank_bm25` (BM25Okapi) is a small, pure-Python implementation of the
    standard algorithm - its only dependency is numpy, which VORTEX already
    pins for the ONNX wake-word stack, so this adds no meaningful footprint.
    """
    if not corpus_texts:
        return []
    tokenized_corpus = [_tokenize(t) for t in corpus_texts]
    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(_tokenize(query))
    ranked = sorted(zip(corpus_ids, scores), key=lambda pair: pair[1], reverse=True)
    # Exclude zero-score chunks: a chunk sharing literally no term with the
    # query isn't a sparse "hit," just an artifact of BM25 scoring every
    # corpus document. Including it would let fusion treat "no keyword
    # overlap at all" as a weak positive signal instead of no signal.
    return [cid for cid, score in ranked[:top_k] if score > 0]


def _reciprocal_rank_fusion(ranked_id_lists, k=RRF_K):
    """Combines multiple ranked-id lists (e.g. dense hits, sparse/BM25 hits)
    into one fused ranking via Reciprocal Rank Fusion: each list contributes
    1/(k + rank) to every id it contains (rank is the 0-indexed position in
    that list), contributions are summed per id across all lists, and the
    result is sorted descending. Returns a list of (id, fused_score) tuples.

    RRF is the standard, simple choice for combining dense+sparse search
    (it's what Qdrant's, Weaviate's, and Elasticsearch's own hybrid-search
    features use internally) specifically because it only needs *rank order*,
    not raw scores - cosine similarity (bounded 0..1) and BM25 scores
    (unbounded, corpus- and query-length dependent) live on incomparable
    scales, so a weighted-score blend would need ad-hoc normalization to keep
    one method from dominating just because its numbers happen to run
    larger. RRF sidesteps that entirely by only ever looking at position.
    k=60 is deliberately not tuned per-corpus (see RRF_K above).
    """
    scores = {}
    for id_list in ranked_id_lists:
        for rank, cid in enumerate(id_list):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def _keyword_overlap_score(query, text):
    """Lightweight reranking signal: the fraction of the query's tokens that
    literally appear in the chunk, plus a fixed bonus if the entire query
    appears as a substring (case-insensitive) of the chunk - the strongest
    possible signal for an exact-match query like "what is SKU-48219-X".

    This is deliberately NOT a cross-encoder (see the module docstring for
    why) - it's a cheap, fully explainable second opinion that specifically
    rewards exact lexical match, correcting cases where RRF's rank-only
    fusion still leaves a semantically-close-but-keyword-empty chunk ahead of
    the chunk that actually contains the literal term being asked about.
    """
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return 0.0
    t_tokens = set(_tokenize(text))
    overlap = len(q_tokens & t_tokens) / len(q_tokens)
    substring_bonus = 0.5 if query.strip().lower() in text.lower() else 0.0
    return overlap + substring_bonus


def _rerank(candidates, question, top_k):
    """Reorders `candidates` (dicts with at least 'content' and a numeric
    'fused_score' from RRF) by a composite of the normalized fused rank and
    the keyword-overlap score above, then returns the top_k.

    The fused RRF score is normalized to a 0..1 range across just this
    candidate set first, so it's on a comparable scale to the keyword-overlap
    score (0..1.5) before combining. Both signals matter and neither should
    fully override the other: the fused rank already reflects both dense and
    sparse evidence working together, while keyword overlap adds a sharper,
    focused exact-match check on top of that - equal 0.5/0.5 weighting means
    a strong exact-keyword match can promote a candidate, but a candidate
    with excellent fused rank and merely average keyword overlap isn't
    discarded just because one chunk happens to repeat the query's words.
    """
    if not candidates:
        return []
    max_fused = max(c['fused_score'] for c in candidates) or 1.0
    scored = []
    for c in candidates:
        fused_norm = c['fused_score'] / max_fused
        kw = _keyword_overlap_score(question, c['content'])
        composite = 0.5 * fused_norm + 0.5 * kw
        scored.append((composite, c))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [c for _, c in scored[:top_k]]


class RagStore:
    def __init__(self):
        # Explicit short timeouts on both connections: Postgres/Qdrant are separate
        # local services that can be down or still starting. Without a bound here,
        # a hung connection attempt blocks VORTEX's entire startup (constructed
        # synchronously in Vortex.__init__, before the wake stream or anything else
        # comes up) instead of the caller's try/except catching a fast, clean failure.
        self._pg = psycopg2.connect(POSTGRES_DSN, connect_timeout=5)
        self._pg.autocommit = True
        self._init_postgres_schema()
        self._qdrant = QdrantClient(url=QDRANT_URL, timeout=5)
        self._init_qdrant_collection()
        self._init_qdrant_turns_collection()
        # doc_id -> list of {'id','content','page','section'} - this document's
        # full chunk corpus, needed for BM25 (which has to score against every
        # chunk to know term rarity, not just a top-k). Cached because a single
        # document-QA session issues many retrieve() calls against an unchanged
        # corpus; invalidated in ensure_ingested() whenever that document's
        # chunks are rewritten (re-ingested with changed content).
        self._corpus_cache = {}

    def _init_postgres_schema(self):
        with self._pg.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS documents (
                    id SERIAL PRIMARY KEY,
                    path TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    ingested_at TIMESTAMP DEFAULT now()
                )
            ''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS chunks (
                    id SERIAL PRIMARY KEY,
                    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL
                )
            ''')
            # Added 2026-08-16 for page/section provenance. ADD COLUMN IF NOT
            # EXISTS (not a fresh CREATE TABLE) so this is a safe no-op
            # migration against a chunks table that already has rows from
            # before this change - existing chunks simply have page=section=
            # NULL (same as "no provenance known", which retrieval/prompt
            # assembly already treat as the no-citation case).
            cur.execute('ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page INTEGER')
            cur.execute('ALTER TABLE chunks ADD COLUMN IF NOT EXISTS section TEXT')

    def _init_qdrant_collection(self):
        existing = [c.name for c in self._qdrant.get_collections().collections]
        if QDRANT_COLLECTION not in existing:
            self._qdrant.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
            )

    def _init_qdrant_turns_collection(self):
        existing = [c.name for c in self._qdrant.get_collections().collections]
        if QDRANT_TURNS_COLLECTION not in existing:
            self._qdrant.create_collection(
                collection_name=QDRANT_TURNS_COLLECTION,
                vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
            )

    def _get_document(self, path):
        with self._pg.cursor() as cur:
            cur.execute('SELECT id, content_hash FROM documents WHERE path = %s', (path,))
            return cur.fetchone()

    def ensure_ingested(self, path, text):
        """Chunk + embed + index a document, unless it's already indexed and unchanged."""
        content_hash = hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest()
        existing = self._get_document(path)
        if existing and existing[1] == content_hash:
            return existing[0]

        filename = os.path.basename(path)
        with self._pg.cursor() as cur:
            if existing:
                doc_id = existing[0]
                cur.execute('DELETE FROM chunks WHERE document_id = %s', (doc_id,))
                cur.execute('UPDATE documents SET content_hash = %s, ingested_at = now() WHERE id = %s',
                           (content_hash, doc_id))
                # Pre-existing gap noticed while testing hybrid search (2026-08-16),
                # fixed here: Postgres chunk rows for this doc_id were just deleted
                # above, but nothing previously told Qdrant to drop the matching
                # points - a re-ingested document's *old* chunk vectors stayed
                # indexed forever under new SERIAL chunk ids never got assigned
                # to, silently polluting every future dense search for this
                # document with stale, no-longer-true content. Best-effort: a
                # failed delete here shouldn't block re-ingestion (the new points
                # about to be upserted below are what matters most), just leaves
                # the old pollution in place for next time.
                try:
                    self._qdrant.delete(
                        collection_name=QDRANT_COLLECTION,
                        points_selector=Filter(must=[FieldCondition(key='document_id', match=MatchValue(value=doc_id))]),
                    )
                except Exception as e:
                    _log.warning(f'Failed to clear stale Qdrant points for doc {doc_id} before re-ingesting: {e}')
            else:
                cur.execute(
                    'INSERT INTO documents (path, filename, content_hash) VALUES (%s, %s, %s) RETURNING id',
                    (path, filename, content_hash))
                doc_id = cur.fetchone()[0]

        # Stale corpus (old chunk ids/text) would otherwise get served to
        # BM25 for this doc_id until process restart - invalidate now that
        # chunks are about to be rewritten. No-op for a brand new doc_id
        # (nothing cached yet).
        self._corpus_cache.pop(doc_id, None)

        points = []
        for idx, chunk in enumerate(_chunk_with_provenance(path, text)):
            with self._pg.cursor() as cur:
                cur.execute(
                    'INSERT INTO chunks (document_id, chunk_index, content, page, section) '
                    'VALUES (%s, %s, %s, %s, %s) RETURNING id',
                    (doc_id, idx, chunk['content'], chunk['page'], chunk['section']))
                chunk_id = cur.fetchone()[0]
            vector = _embed(chunk['content'])
            points.append(PointStruct(
                id=chunk_id, vector=vector,
                payload={
                    'document_id': doc_id, 'chunk_index': idx, 'content': chunk['content'],
                    'filename': filename, 'page': chunk['page'], 'section': chunk['section'],
                },
            ))
        if points:
            self._qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)
        return doc_id

    def _dense_search(self, doc_id, question, limit):
        """Dense (embedding cosine-similarity) search - the sole retrieval
        path before hybrid search was added. Returns (ids, payloads): ids is
        the ranked chunk-id list (best first), payloads maps id -> the
        {'content','page','section'} dict retrieve() eventually returns, so
        a dense hit's content doesn't need a second round trip to resolve."""
        query_vector = _embed(question)
        hits = self._qdrant.query_points(
            collection_name=QDRANT_COLLECTION,
            query=query_vector,
            query_filter=Filter(must=[FieldCondition(key='document_id', match=MatchValue(value=doc_id))]),
            limit=limit,
        ).points
        ids = [h.id for h in hits]
        payloads = {
            h.id: {'content': h.payload['content'], 'page': h.payload.get('page'), 'section': h.payload.get('section')}
            for h in hits
        }
        return ids, payloads

    def _get_chunk_corpus(self, doc_id):
        """This document's full chunk corpus (id/content/page/section) from
        Postgres, for BM25 - which needs the whole corpus, not just a top-k,
        to score term rarity correctly. Cached per doc_id (see
        self._corpus_cache's comment in __init__); on a Postgres error, logs
        and returns an empty corpus rather than raising, so a transient
        Postgres hiccup degrades retrieve() to dense-only instead of failing
        the whole question."""
        if doc_id in self._corpus_cache:
            return self._corpus_cache[doc_id]
        try:
            with self._pg.cursor() as cur:
                cur.execute(
                    'SELECT id, content, page, section FROM chunks WHERE document_id = %s ORDER BY chunk_index',
                    (doc_id,))
                rows = cur.fetchall()
            corpus = [{'id': r[0], 'content': r[1], 'page': r[2], 'section': r[3]} for r in rows]
        except Exception as e:
            _log.warning(f'BM25 corpus fetch failed for doc {doc_id}, falling back to dense-only: {e}')
            corpus = []
        self._corpus_cache[doc_id] = corpus
        return corpus

    def retrieve(self, doc_id, question, top_k=TOP_K):
        """Top-k chunks for one document, most relevant first. Returns a list
        of {'content', 'page', 'section'} dicts - callers that only need the
        text can still do `c['content']`; build_rag_prompt below is the
        intended consumer and uses 'page'/'section' for citations. Signature
        and return shape are unchanged from the dense-only version, so
        main.py's call site needs no edits.

        When HYBRID_SEARCH_ENABLED (default on): fuses dense (Qdrant) and
        sparse (BM25) candidate pools via Reciprocal Rank Fusion, then - when
        RERANK_ENABLED (default on) - reranks the fused pool with a
        keyword-overlap heuristic before truncating to top_k. When either
        flag is off, falls back toward the simpler/cheaper path (dense-only
        search, or fused-but-unreranked) - see module docstring for why each
        stage exists and its cost.
        """
        if not HYBRID_SEARCH_ENABLED:
            dense_ids, dense_payloads = self._dense_search(doc_id, question, limit=top_k)
            return [dense_payloads[cid] for cid in dense_ids]

        pool = max(CANDIDATE_POOL, top_k)
        dense_ids, dense_payloads = self._dense_search(doc_id, question, limit=pool)

        corpus = self._get_chunk_corpus(doc_id)
        corpus_by_id = {c['id']: c for c in corpus}
        sparse_ids = _bm25_rank(
            [c['content'] for c in corpus], [c['id'] for c in corpus], question, top_k=pool
        ) if corpus else []

        fused = _reciprocal_rank_fusion([dense_ids, sparse_ids])
        rerank_pool = max(RERANK_POOL, top_k)
        candidates = []
        for cid, score in fused[:rerank_pool]:
            payload = dense_payloads.get(cid) or corpus_by_id.get(cid)
            if payload is None:
                continue  # defensive: id came from fusion but neither source could resolve it
            candidates.append({
                'id': cid, 'content': payload['content'],
                'page': payload.get('page'), 'section': payload.get('section'),
                'fused_score': score,
            })

        chunks = _rerank(candidates, question, top_k) if RERANK_ENABLED else candidates[:top_k]
        return [{'content': c['content'], 'page': c.get('page'), 'section': c.get('section')} for c in chunks]

    # ---------- conversation-memory retrieval ----------
    # No chunking, no Postgres schema - a conversation turn already is the
    # retrieval unit, and memory.py's SQLite table stays the sole source of
    # truth for turns (this is purely a similarity index over them, safe to
    # rebuild from scratch by re-indexing if the Qdrant collection is ever
    # dropped - unlike document chunks, which don't exist anywhere else).

    def index_turn(self, turn_id, role, content):
        """Embeds and indexes one conversation turn. Called fire-and-forget
        from a background thread (see app.py's _index_turn_async) so a slow
        or failed embed/upsert never blocks the live conversation - any
        exception here is the caller's problem to catch and log, not this
        method's to swallow, so a real bug doesn't look like silent success."""
        vector = _embed(content)
        self._qdrant.upsert(collection_name=QDRANT_TURNS_COLLECTION, points=[
            PointStruct(id=turn_id, vector=vector, payload={'role': role, 'content': content}),
        ])

    def search_turns(self, query, top_k=5):
        """Returns up to top_k past conversation turns most similar to
        `query`, most relevant first - [{'role', 'content'}, ...]. Dense
        (embedding) search only, deliberately not the full hybrid+rerank
        pipeline retrieve() uses for documents: conversation turns are short,
        conversational text, not dense reference material where an exact
        keyword (a SKU, a specific number) is likely to matter as much as
        semantic similarity - see the module docstring's BM25 reasoning for
        why that trade-off exists for documents but doesn't obviously apply
        here. Can be revisited if real usage shows otherwise."""
        query_vector = _embed(query)
        hits = self._qdrant.query_points(
            collection_name=QDRANT_TURNS_COLLECTION, query=query_vector, limit=top_k).points
        return [{'role': h.payload['role'], 'content': h.payload['content']} for h in hits]

    def close(self):
        self._pg.close()


def _citation_label(page, section):
    if page is not None:
        return f'[Page {page}] '
    if section:
        return f'[{section}] '
    return ''


def build_rag_prompt(chunks, question):
    """`chunks` is normally the list of dicts retrieve() returns, but plain
    strings are also accepted (defensive - so a caller/test passing raw text
    still works) since a dict-only assumption would raise for a `chunks`
    value shaped differently than `retrieve()` produces."""
    parts = []
    for c in chunks:
        if isinstance(c, dict):
            label = _citation_label(c.get('page'), c.get('section')) if INCLUDE_PAGE_NUMBERS else ''
            parts.append(f"{label}{c['content']}")
        else:
            parts.append(c)
    context = '\n\n---\n\n'.join(parts)
    cite_instruction = (
        ' Excerpts tagged with a page number or section above are labeled '
        '[Page N] or [section name]; mention it in your answer (e.g. "on page 3") '
        'when it helps the user find the source.' if INCLUDE_PAGE_NUMBERS else ''
    )
    return (
        f'Relevant excerpts from the document:\n{context}\n\n'
        f'Based only on these excerpts, {question}\n'
        f'Answer concisely in a couple of spoken sentences.{cite_instruction} '
        "If the excerpts don't contain the answer, say so plainly."
    )


def build_memory_prompt(turns, query):
    """`turns` is search_turns()'s return shape - [{'role', 'content'}, ...].
    Mirrors build_rag_prompt's structure for the same reason: one place that
    turns retrieved context into a prompt, so the "answer only from what was
    actually retrieved, say so plainly if it's not there" instruction stays
    consistent between documents and conversation memory."""
    context = '\n'.join(f"{t['role']}: {t['content']}" for t in turns)
    return (
        f'Relevant excerpts from earlier conversation:\n{context}\n\n'
        f'Based only on these excerpts, {query}\n'
        'Answer concisely in a couple of spoken sentences. '
        "If the excerpts don't contain the answer, say so plainly - don't guess."
    )
