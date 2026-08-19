"""Persistent conversation memory (Phase 5, scoped v1).

A plain SQLite-backed store for conversation turns, so history survives a
restart instead of living only in a Python list. This is deliberately not
the full Phase 5 RAG/embeddings/PostgreSQL+Qdrant scope from the blueprint -
that's a much bigger, later-stage build. This is the concrete, immediate gap
it closes: "conversation history resets every time the process restarts."
"""
import sqlite3
import threading


class MemoryStore:
    def __init__(self, db_path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute('''
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self._conn.commit()

    def add_turn(self, role, content):
        """Returns the new turn's row id - used by app.py to index it for
        retrieval (rag.py's index_turn) without a second query to find out
        which row it just wrote."""
        with self._lock:
            cur = self._conn.execute('INSERT INTO turns (role, content) VALUES (?, ?)', (role, content))
            self._conn.commit()
            return cur.lastrowid

    def recent(self, limit=10):
        """Oldest-first, most-recent `limit` turns - ready to feed straight to an LLM's messages list."""
        with self._lock:
            rows = self._conn.execute(
                'SELECT role, content FROM turns ORDER BY id DESC LIMIT ?', (limit,)
            ).fetchall()
        return [{'role': role, 'content': content} for role, content in reversed(rows)]

    def close(self):
        with self._lock:
            self._conn.close()
