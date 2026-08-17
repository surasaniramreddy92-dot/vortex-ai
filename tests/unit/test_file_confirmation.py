"""End-to-end confirmation-gating tests for the new file-op voice commands
(delete/move/rename) - proves they don't touch the filesystem until the
existing awaiting-confirmation flow (self.awaiting_confirmation, shared with
shutdown/restart/close-all) gets an explicit "yes", and that a decline
leaves the file untouched. Also proves delete never reaches a real
send2trash call, or os.remove, until confirmed.

Uses a real Vortex() (see test_registry.py's fixture for why that's safe -
no hardware is touched at construction), with files.SEARCH_DIRS
monkeypatched to temp directories so nothing under the user's real
Desktop/Documents/Downloads is ever touched.
"""
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.main import Vortex
from vortex import files as fileops


@pytest.fixture
def v(monkeypatch, tmp_path):
    desktop = tmp_path / 'Desktop'
    documents = tmp_path / 'Documents'
    downloads = tmp_path / 'Downloads'
    for d in (desktop, documents, downloads):
        d.mkdir()
    # main.py does `from . import files as fileops` - same module object,
    # so patching vortex.files.SEARCH_DIRS here is visible to main.py's
    # handlers too, not just to files.py's own functions.
    monkeypatch.setattr(fileops, 'SEARCH_DIRS', [desktop, documents, downloads])

    inst = Vortex()
    inst.spoken = []
    inst.speak = lambda text: inst.spoken.append(text)

    yield inst, {'desktop': desktop, 'documents': documents, 'downloads': downloads}

    inst.memory.close()
    if inst.rag is not None:
        inst.rag.close()


def test_delete_file_requires_confirmation_before_deleting(v):
    inst, dirs = v
    target = dirs['desktop'] / 'a.txt'
    target.write_text('x', encoding='utf-8')

    with patch('send2trash.send2trash') as mock_trash:
        inst.execute('delete file a.txt')
        assert inst.awaiting_confirmation is not None
        assert inst.awaiting_confirmation['action'] == 'delete_file'
        mock_trash.assert_not_called()
        assert target.exists()

        inst.execute('yes')
        mock_trash.assert_called_once_with(str(target))
    assert inst.awaiting_confirmation is None


def test_delete_file_declined_leaves_file_untouched(v):
    inst, dirs = v
    target = dirs['desktop'] / 'a.txt'
    target.write_text('x', encoding='utf-8')

    with patch('send2trash.send2trash') as mock_trash:
        inst.execute('delete file a.txt')
        inst.execute('no thanks')
        mock_trash.assert_not_called()
    assert target.exists()
    assert inst.awaiting_confirmation is None


def test_delete_file_never_uses_permanent_os_remove(v):
    inst, dirs = v
    target = dirs['desktop'] / 'a.txt'
    target.write_text('x', encoding='utf-8')

    with patch('send2trash.send2trash') as mock_trash, patch('os.remove') as mock_remove:
        inst.execute('delete file a.txt')
        inst.execute('yes')
    mock_trash.assert_called_once()
    mock_remove.assert_not_called()


def test_move_file_requires_confirmation_before_moving(v):
    inst, dirs = v
    target = dirs['desktop'] / 'a.txt'
    target.write_text('x', encoding='utf-8')

    inst.execute('move file a.txt to documents')
    assert inst.awaiting_confirmation is not None
    assert target.exists()
    assert not (dirs['documents'] / 'a.txt').exists()

    inst.execute('yes')
    assert not target.exists()
    assert (dirs['documents'] / 'a.txt').exists()


def test_move_file_declined_leaves_file_in_place(v):
    inst, dirs = v
    target = dirs['desktop'] / 'a.txt'
    target.write_text('x', encoding='utf-8')

    inst.execute('move file a.txt to documents')
    inst.execute('nope')
    assert target.exists()
    assert not (dirs['documents'] / 'a.txt').exists()
    assert inst.awaiting_confirmation is None


def test_rename_file_requires_confirmation(v):
    inst, dirs = v
    target = dirs['desktop'] / 'a.txt'
    target.write_text('x', encoding='utf-8')

    inst.execute('rename file a.txt to b.txt')
    assert inst.awaiting_confirmation is not None
    assert target.exists()
    assert not (dirs['desktop'] / 'b.txt').exists()

    inst.execute('yes')
    assert not target.exists()
    assert (dirs['desktop'] / 'b.txt').exists()


def test_copy_file_does_not_require_confirmation(v):
    """Deliberate design choice (see files.py's module docstring / main.py's
    file-operations comment): copy can't destroy or rename anything that
    already existed, and files.copy_file structurally refuses to overwrite
    an existing destination - so it runs immediately."""
    inst, dirs = v
    target = dirs['desktop'] / 'a.txt'
    target.write_text('x', encoding='utf-8')

    inst.execute('copy file a.txt to documents')
    assert inst.awaiting_confirmation is None
    assert target.exists()
    assert (dirs['documents'] / 'a.txt').exists()


def test_delete_file_not_found_does_not_prompt(v):
    inst, dirs = v
    inst.execute('delete file nonexistent.txt')
    assert inst.awaiting_confirmation is None
    assert any("couldn't find" in s for s in inst.spoken)


def test_close_all_confirmation_gate_unaffected_by_dict_shape_change(v):
    """Regression check: the pre-existing close-all confirmation gate (not a
    file op) must still behave the same now that awaiting_confirmation holds
    a dict instead of a bare string."""
    inst, dirs = v
    inst.close_all_apps = lambda: inst.spoken.append('CLOSED_ALL')
    inst.execute('close all')
    assert inst.awaiting_confirmation == {'action': 'close_all'}
    assert 'CLOSED_ALL' not in inst.spoken
    inst.execute('yes')
    assert 'CLOSED_ALL' in inst.spoken
    assert inst.awaiting_confirmation is None
