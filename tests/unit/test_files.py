"""Unit tests for src/vortex/files.py (Phase 2 file operations, 2026-08-16).

All directories are tempfile-created (pytest's tmp_path) for every test -
documents.SEARCH_DIRS (imported into files.py as files.SEARCH_DIRS) is
monkeypatched to point at temp subdirectories named like the real
Desktop/Documents/Downloads it would otherwise resolve to, so these tests
never read, write, move, copy, rename, or delete anything under the user's
actual Desktop/Documents/Downloads.

send2trash.send2trash itself is mocked (not exercised for real) in the
delete tests - the goal is proving VORTEX calls send2trash rather than
os.remove, not exercising the Windows Recycle Bin shell API from a unit
test.
"""
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex import files


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    desktop = tmp_path / 'Desktop'
    documents = tmp_path / 'Documents'
    downloads = tmp_path / 'Downloads'
    for d in (desktop, documents, downloads):
        d.mkdir()
    monkeypatch.setattr(files, 'SEARCH_DIRS', [desktop, documents, downloads])
    return {'desktop': desktop, 'documents': documents, 'downloads': downloads}


def _touch(path, content='hello'):
    path.write_text(content, encoding='utf-8')
    return path


# ---------- allowed-directory scoping ----------

def test_resolve_file_finds_file_in_allowed_dir(sandbox):
    _touch(sandbox['documents'] / 'report.pdf')
    assert files.resolve_file('report.pdf') == sandbox['documents'] / 'report.pdf'


def test_resolve_file_returns_none_when_not_found(sandbox):
    assert files.resolve_file('nonexistent.pdf') is None


def test_resolve_dir_maps_spoken_name(sandbox):
    assert files.resolve_dir('desktop') == sandbox['desktop']
    assert files.resolve_dir('Documents') == sandbox['documents']


def test_resolve_dir_returns_none_for_unknown_folder(sandbox):
    assert files.resolve_dir('windows') is None


def test_delete_refuses_path_outside_allowed_dirs(sandbox, tmp_path):
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    target = _touch(outside / 'secret.txt')
    with patch('send2trash.send2trash') as mock_trash:
        with pytest.raises(files.PathNotAllowedError):
            files.delete_file(target)
        mock_trash.assert_not_called()
    assert target.exists()


def test_move_refuses_source_outside_allowed_dirs(sandbox, tmp_path):
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    target = _touch(outside / 'secret.txt')
    with pytest.raises(files.PathNotAllowedError):
        files.move_file(target, sandbox['desktop'])
    assert target.exists()


def test_move_refuses_dest_outside_allowed_dirs(sandbox, tmp_path):
    target = _touch(sandbox['documents'] / 'a.txt')
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    with pytest.raises(files.PathNotAllowedError):
        files.move_file(target, outside)
    assert target.exists()


def test_path_traversal_via_dotdot_is_refused(sandbox):
    """A resolved-but-outside path (standing in for a '..'-crafted path)
    must be refused even though the raw string starts inside SEARCH_DIRS -
    proves the check is on the resolved path, not a naive string prefix."""
    traversal = sandbox['documents'] / '..' / '..' / 'somewhere_else.txt'
    with pytest.raises(files.PathNotAllowedError):
        files.delete_file(traversal)


def test_resolve_file_never_returns_path_outside_search_dirs(sandbox, tmp_path):
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    _touch(outside / 'a.txt')
    assert files.resolve_file('a.txt') is None


# ---------- delete goes through send2trash, never a permanent os.remove ----------

def test_delete_calls_send2trash_not_os_remove(sandbox):
    target = _touch(sandbox['desktop'] / 'todelete.txt')
    with patch('send2trash.send2trash') as mock_trash, patch('os.remove') as mock_remove:
        files.delete_file(target)
    mock_trash.assert_called_once_with(str(target))
    mock_remove.assert_not_called()
    assert target.exists()  # untouched: send2trash itself was mocked out


# ---------- move / copy / rename ----------

def test_move_file_moves_and_returns_dest(sandbox):
    target = _touch(sandbox['desktop'] / 'a.txt', 'content')
    dest = files.move_file(target, sandbox['documents'])
    assert dest == sandbox['documents'] / 'a.txt'
    assert dest.exists()
    assert not target.exists()


def test_move_file_refuses_to_overwrite_existing(sandbox):
    target = _touch(sandbox['desktop'] / 'a.txt', 'source')
    _touch(sandbox['documents'] / 'a.txt', 'existing')
    with pytest.raises(FileExistsError):
        files.move_file(target, sandbox['documents'])
    assert target.exists()
    assert (sandbox['documents'] / 'a.txt').read_text(encoding='utf-8') == 'existing'


def test_copy_file_copies_and_keeps_source(sandbox):
    target = _touch(sandbox['desktop'] / 'a.txt', 'content')
    dest = files.copy_file(target, sandbox['documents'])
    assert dest.read_text(encoding='utf-8') == 'content'
    assert target.exists()


def test_copy_file_refuses_to_overwrite_existing(sandbox):
    target = _touch(sandbox['desktop'] / 'a.txt', 'source')
    _touch(sandbox['documents'] / 'a.txt', 'existing')
    with pytest.raises(FileExistsError):
        files.copy_file(target, sandbox['documents'])
    assert (sandbox['documents'] / 'a.txt').read_text(encoding='utf-8') == 'existing'


def test_rename_file_renames_in_place(sandbox):
    target = _touch(sandbox['desktop'] / 'a.txt', 'content')
    dest = files.rename_file(target, 'b.txt')
    assert dest == sandbox['desktop'] / 'b.txt'
    assert dest.exists()
    assert not target.exists()


def test_rename_strips_path_components_from_new_name(sandbox):
    """A spoken/injected new name containing path separators must not be
    able to relocate the file - only the bare filename portion is used."""
    target = _touch(sandbox['desktop'] / 'a.txt', 'content')
    dest = files.rename_file(target, '../../elsewhere/b.txt')
    assert dest == sandbox['desktop'] / 'b.txt'
    assert dest.exists()


def test_rename_refuses_to_overwrite_existing(sandbox):
    target = _touch(sandbox['desktop'] / 'a.txt', 'source')
    _touch(sandbox['desktop'] / 'b.txt', 'existing')
    with pytest.raises(FileExistsError):
        files.rename_file(target, 'b.txt')
    assert target.exists()


# ---------- list / search ----------

def test_list_files_all_dirs(sandbox):
    """Combining multiple folders must carry per-file location - a bare
    filename with no folder context isn't actionable when several folders
    are involved (see files.list_files's docstring)."""
    _touch(sandbox['desktop'] / 'a.txt')
    _touch(sandbox['documents'] / 'b.txt')
    entries, err = files.list_files()
    assert err is None
    assert {(e['name'], e['location']) for e in entries} == {
        ('a.txt', 'Desktop'), ('b.txt', 'Documents')}


def test_list_files_one_dir(sandbox):
    _touch(sandbox['desktop'] / 'a.txt')
    _touch(sandbox['documents'] / 'b.txt')
    entries, err = files.list_files('documents')
    assert err is None
    assert entries == [{'name': 'b.txt', 'location': 'Documents'}]


def test_list_files_unknown_dir_returns_error(sandbox):
    entries, err = files.list_files('nonexistent_folder')
    assert entries == []
    assert err is not None


def test_search_files_substring_match(sandbox):
    _touch(sandbox['desktop'] / 'quarterly_report.pdf')
    _touch(sandbox['documents'] / 'unrelated.txt')
    matches = files.search_files('report')
    assert [p.name for p in matches] == ['quarterly_report.pdf']


def test_search_files_no_match(sandbox):
    _touch(sandbox['desktop'] / 'a.txt')
    assert files.search_files('nonexistent') == []


def test_search_files_glob_wildcard(sandbox):
    _touch(sandbox['desktop'] / 'invoice_2024.pdf')
    _touch(sandbox['desktop'] / 'invoice_2025.pdf')
    _touch(sandbox['desktop'] / 'notes.txt')
    matches = files.search_files('invoice_*.pdf')
    assert {p.name for p in matches} == {'invoice_2024.pdf', 'invoice_2025.pdf'}


def test_search_files_only_searches_allowed_dirs(sandbox, tmp_path):
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    _touch(outside / 'report_secret.txt')
    assert files.search_files('report') == []
