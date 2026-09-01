"""Unit tests for src/vortex/memory.py's MemoryStore.stats() - added
2026-09-01 for core/self_knowledge.py's "how long have we been talking"
grounding fact. add_turn/recent were previously only exercised indirectly
through Vortex integration tests; stats() gets direct coverage here since
it's new and its correctness matters for demonstrate_self() not inventing
numbers.
"""
import sys

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.memory import MemoryStore


def make_store(tmp_path):
    return MemoryStore(str(tmp_path / 'test_memory.db'))


def test_stats_on_empty_store(tmp_path):
    store = make_store(tmp_path)
    stats = store.stats()
    assert stats == {'turn_count': 0, 'first_turn_at': None}
    store.close()


def test_stats_counts_every_turn(tmp_path):
    store = make_store(tmp_path)
    store.add_turn('user', 'hello')
    store.add_turn('assistant', 'hi there')
    store.add_turn('user', 'how are you')
    stats = store.stats()
    assert stats['turn_count'] == 3
    store.close()


def test_stats_first_turn_at_is_the_earliest_not_the_latest(tmp_path):
    store = make_store(tmp_path)
    store.add_turn('user', 'first message')
    store.add_turn('user', 'second message')
    stats = store.stats()
    assert stats['first_turn_at'] is not None
    # first_turn_at must come from the FIRST row (id=1), not the last one -
    # verified by checking it matches a fresh single-row query for id 1.
    with store._lock:
        expected = store._conn.execute(
            'SELECT created_at FROM turns WHERE id = 1').fetchone()[0]
    assert stats['first_turn_at'] == expected
    store.close()
