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
"""
import hashlib
import os

import ollama
import psycopg2
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams, FieldCondition, Filter, MatchValue

POSTGRES_DSN = os.getenv('VORTEX_POSTGRES_DSN', 'dbname=vortex user=vortex password=vortex_local_dev host=localhost')
QDRANT_URL = os.getenv('VORTEX_QDRANT_URL', 'http://localhost:6333')
QDRANT_COLLECTION = 'vortex_chunks'
EMBED_MODEL = os.getenv('VORTEX_EMBED_MODEL', 'nomic-embed-text')
EMBED_DIM = 768  # nomic-embed-text's output size
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
TOP_K = 5


def _embed(text):
    res = ollama.embeddings(model=EMBED_MODEL, prompt=text)
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


class RagStore:
    def __init__(self):
        self._pg = psycopg2.connect(POSTGRES_DSN)
        self._pg.autocommit = True
        self._init_postgres_schema()
        self._qdrant = QdrantClient(url=QDRANT_URL)
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
        for idx, chunk in enumerate(_chunk_text(text)):
            with self._pg.cursor() as cur:
                cur.execute(
                    'INSERT INTO chunks (document_id, chunk_index, content) VALUES (%s, %s, %s) RETURNING id',
                    (doc_id, idx, chunk))
                chunk_id = cur.fetchone()[0]
            vector = _embed(chunk)
            points.append(PointStruct(
                id=chunk_id, vector=vector,
                payload={'document_id': doc_id, 'chunk_index': idx, 'content': chunk, 'filename': filename},
            ))
        if points:
            self._qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)
        return doc_id

    def retrieve(self, doc_id, question, top_k=TOP_K):
        """Top-k chunks (by cosine similarity to the question) from one document."""
        query_vector = _embed(question)
        hits = self._qdrant.query_points(
            collection_name=QDRANT_COLLECTION,
            query=query_vector,
            query_filter=Filter(must=[FieldCondition(key='document_id', match=MatchValue(value=doc_id))]),
            limit=top_k,
        ).points
        return [h.payload['content'] for h in hits]

    def close(self):
        self._pg.close()


def build_rag_prompt(chunks, question):
    context = '\n\n---\n\n'.join(chunks)
    return (
        f'Relevant excerpts from the document:\n{context}\n\n'
        f'Based only on these excerpts, {question}\n'
        'Answer concisely in a couple of spoken sentences. '
        "If the excerpts don't contain the answer, say so plainly."
    )
