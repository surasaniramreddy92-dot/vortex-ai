"""Unit tests for src/vortex/popup.py (file-listing popup, added 2026-08-17).

Doesn't open a real Tk window - _format_entry (the pure formatting logic)
is tested directly, and show_file_popup is tested only for "does it return
immediately without raising," matching its non-blocking, best-effort
contract. Actually driving a real GUI window from a headless test run isn't
attempted here - see popup.py's own degrade-gracefully try/except for what
happens when tkinter genuinely can't open one.
"""
import sys
from pathlib import Path

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex import popup


def test_format_entry_dict_with_location():
    entry = {'name': 'report.pdf', 'location': 'Desktop'}
    assert popup._format_entry(entry) == 'report.pdf  (PDF) - Desktop'


def test_format_entry_dict_without_location():
    entry = {'name': 'report.pdf', 'location': None}
    assert popup._format_entry(entry) == 'report.pdf  (PDF)'


def test_format_entry_path_object():
    p = Path('C:/Users/x/Documents/notes.docx')
    assert popup._format_entry(p) == 'notes.docx  (DOCX) - Documents'


def test_format_entry_no_extension():
    entry = {'name': 'README', 'location': 'Desktop'}
    assert popup._format_entry(entry) == 'README  (file) - Desktop'


def test_show_file_popup_does_not_raise_and_returns_immediately():
    """Non-blocking contract: calling this must return right away (it starts
    a background thread), not hang waiting for a window to be closed."""
    import time
    start = time.monotonic()
    popup.show_file_popup([{'name': 'a.txt', 'location': 'Desktop'}])
    elapsed = time.monotonic() - start
    assert elapsed < 1.0
    popup._close_existing()


def test_run_popup_degrades_gracefully_when_tkinter_unavailable(monkeypatch):
    """If tkinter itself can't be imported/used (no display, headless CI,
    etc.), the popup thread must log and stop, never raise into the daemon
    thread or block the caller."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == 'tkinter':
            raise RuntimeError('no display available')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', fake_import)
    # Must not raise - the try/except inside _run_popup is what's under test.
    popup._run_popup('Files', ['a.txt  (TXT)'])
