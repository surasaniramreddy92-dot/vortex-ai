"""Unit tests for src/vortex/audit.py's structured JSON-lines audit trail.

Every test uses a tmp_path log file, never the real logs/audit.jsonl.
"""
import json
import sys

import pytest

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.audit import AuditLog


@pytest.fixture
def log_path(tmp_path):
    return str(tmp_path / 'audit.jsonl')


def _read_lines(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def test_record_writes_one_json_line(log_path):
    audit = AuditLog(log_path)
    audit.record('delete_file', 'C:/Users/x/Desktop/a.txt', 'executed')
    entries = _read_lines(log_path)
    assert len(entries) == 1
    assert entries[0]['action'] == 'delete_file'
    assert entries[0]['target'] == 'C:/Users/x/Desktop/a.txt'
    assert entries[0]['outcome'] == 'executed'
    assert 'timestamp' in entries[0]


def test_record_appends_multiple_entries_in_order(log_path):
    audit = AuditLog(log_path)
    audit.record('close_all', 'all_non_protected_processes', 'prompted')
    audit.record('close_all', 'all_non_protected_processes', 'executed', count=5)
    entries = _read_lines(log_path)
    assert len(entries) == 2
    assert entries[0]['outcome'] == 'prompted'
    assert entries[1]['outcome'] == 'executed'
    assert entries[1]['details'] == {'count': 5}


def test_creates_parent_directory_if_missing(tmp_path):
    nested = tmp_path / 'nested' / 'dir' / 'audit.jsonl'
    audit = AuditLog(str(nested))
    audit.record('shutdown', 'system', 'executed')
    assert nested.exists()
    assert _read_lines(str(nested))[0]['action'] == 'shutdown'


def test_details_key_omitted_when_no_extra_kwargs(log_path):
    audit = AuditLog(log_path)
    audit.record('delete_file', 'a.txt', 'declined')
    entry = _read_lines(log_path)[0]
    assert 'details' not in entry


def test_each_entry_is_valid_standalone_json(log_path):
    """JSON-lines format: every line must parse independently, not just the
    file as a whole - proves no trailing commas / wrapping array."""
    audit = AuditLog(log_path)
    for i in range(5):
        audit.record('close_app', f'app{i}', 'executed')
    with open(log_path, encoding='utf-8') as f:
        lines = [l for l in f if l.strip()]
    assert len(lines) == 5
    for line in lines:
        json.loads(line)  # must not raise
