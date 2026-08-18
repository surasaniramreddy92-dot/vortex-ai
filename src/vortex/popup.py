"""Visual popup showing file listings alongside the spoken answer. Added
2026-08-17, direct user request: "open a popup and write the files while
calling out the title and type of file simultaneously."

Runs tkinter in its own dedicated thread with its own, fully independent
Tk() root - never sharing widgets or state with any other thread. Tkinter is
not safe to drive from multiple threads sharing one Tk instance, but a
thread that owns its own Tk() root end-to-end (create it, populate it, run
its mainloop, all in that one thread) is a standard, working pattern on
Windows. This keeps the popup non-blocking: VORTEX's worker thread starts
it and moves straight on to speaking, instead of freezing until the window
is closed.
"""
import logging
import threading

_log = logging.getLogger('vortex.popup')
_current_popup = {'root': None}


def show_file_popup(entries, title='Files'):
    """Opens a small window listing entries (dicts with 'name' and
    'location', matching files.list_files()'s shape, or Path objects from
    search_files() - handled below) - one line per file, filename and type
    together, so what's read aloud has something to look at at the same
    time. Closes any previous popup from this process first, so repeated
    "list files" calls don't pile up windows. Returns immediately; the
    window itself runs on its own thread until the user closes it."""
    _close_existing()
    lines = [_format_entry(e) for e in entries]
    t = threading.Thread(target=_run_popup, args=(title, lines), daemon=True)
    t.start()


def _format_entry(entry):
    if isinstance(entry, dict):
        name, location = entry['name'], entry.get('location')
    else:
        # A search_files() Path object - same "name (type)" formatting,
        # location from the parent folder name instead of a dict key.
        name, location = entry.name, entry.parent.name
    ext = name.rsplit('.', 1)[-1].upper() if '.' in name else 'file'
    suffix = f' - {location}' if location else ''
    return f'{name}  ({ext}){suffix}'


def _close_existing():
    root = _current_popup['root']
    if root is not None:
        try:
            root.after(0, root.destroy)
        except Exception:
            pass
        _current_popup['root'] = None


def _run_popup(title, lines):
    # Best-effort, like every other optional/environment-dependent feature in
    # this project (OCR, offline voice models): a popup failing to open
    # (no display, tkinter unavailable, etc.) must never take down the
    # voice loop that's already moved on to speaking the same information -
    # log it and stop, don't raise into this daemon thread's root.
    try:
        import tkinter as tk
        root = tk.Tk()
        _current_popup['root'] = root
        root.title(title)
        root.attributes('-topmost', True)
        frame = tk.Frame(root, padx=12, pady=10)
        frame.pack(fill='both', expand=True)
        tk.Label(frame, text=f'{len(lines)} file{"s" if len(lines) != 1 else ""}',
                  font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(0, 6))
        listbox = tk.Listbox(frame, width=50, height=min(15, max(3, len(lines))),
                              font=('Segoe UI', 10))
        for line in lines:
            listbox.insert('end', line)
        listbox.pack(fill='both', expand=True)
        tk.Button(frame, text='Close', command=root.destroy).pack(pady=(8, 0))
        root.protocol('WM_DELETE_WINDOW', root.destroy)
        root.mainloop()
        if _current_popup['root'] is root:
            _current_popup['root'] = None
    except Exception as e:
        _log.warning(f'File popup failed to open: {type(e).__name__}: {e}')
        _current_popup['root'] = None
