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

Deliberately NOT built here: reranking, sparse/hybrid search, cross-document
retrieval, or migrating conversation history off SQLite. This is scoped to
"answer a question about one document, well," which is the concrete gap that
justified adding this stack at all (see IMPLEMENTED.md).

Page/section provenance (added 2026-08-16): `ensure_ingested(path, text)` kept
its original signature (a caller in main.py passes an already-extracted flat
`text` string, used here for hashing/change-detection exactly as before) but
now also calls documents.extract_pages(path) internally to get page/section-
tagged text, and chunks *that* instead of the flat string, so every stored
chunk carries where it actually came from. If extract_pages() fails or the
format has no page/section structure, this falls back to chunking the flat
`text` with page=section=None - retrieval and prompt assembly both already
handle that (no page/section to cite, so none is claimed).
"""
import hashlib
import logging
import os

import ollama
import psycopg2
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams, FieldCondition, Filter, MatchValue

from . import documents

POSTGRES_DSN = os.getenv('VORTEX_POSTGRES_DSN', 'dbname=vortex user=vortex password=vortex_local_dev host=localhost')
QDRANT_URL = os.getenv('VORTEX_QDRANT_URL', 'http://localhost:6333')
QDRANT_COLLECTION = 'vortex_chunks'
EMBED_MODEL = os.getenv('VORTEX_EMBED_MODEL', 'nomic-embed-text')
EMBED_DIM = 768  # nomic-embed-text's output size
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
TOP_K = 5
# Mirrors VortexConfig.document_page_numbers - see documents.py's OCR_ENABLED
# comment for why this stays a plain module constant rather than a config.py
# import (matching this file's own pre-existing pattern for POSTGRES_DSN etc.).
INCLUDE_PAGE_NUMBERS = os.getenv('VORTEX_DOCUMENT_PAGE_NUMBERS', 'true').strip().lower() not in (
    '0', 'false', 'no', 'off')

_log = logging.getLogger('vortex.rag')


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
            else:
                cur.execute(
                    'INSERT INTO documents (path, filename, content_hash) VALUES (%s, %s, %s) RETURNING id',
                    (path, filename, content_hash))
                doc_id = cur.fetchone()[0]

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

    def retrieve(self, doc_id, question, top_k=TOP_K):
        """Top-k chunks (by cosine similarity to the question) from one document.
        Returns a list of {'content', 'page', 'section'} dicts - callers that
        only need the text can still do `c['content']`; build_rag_prompt below
        is the intended consumer and uses 'page'/'section' for citations."""
        query_vector = _embed(question)
        hits = self._qdrant.query_points(
            collection_name=QDRANT_COLLECTION,
            query=query_vector,
            query_filter=Filter(must=[FieldCondition(key='document_id', match=MatchValue(value=doc_id))]),
            limit=top_k,
        ).points
        return [
            {'content': h.payload['content'], 'page': h.payload.get('page'), 'section': h.payload.get('section')}
            for h in hits
        ]

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
