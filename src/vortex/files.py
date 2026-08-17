"""Voice-triggered file operations (Phase 2: Desktop & OS Automation) - list,
search, move, copy, rename, delete.

Scoped ONLY to the same three directories documents.py already resolves
files against - Desktop, Documents, Downloads (see documents.SEARCH_DIRS,
imported here rather than re-declared, so there is exactly one path policy
for "which folders can VORTEX touch by voice", not two that could quietly
drift apart).

Every function that turns voice input into a filesystem path verifies the
resolved, real path actually lives under one of SEARCH_DIRS before touching
it (`_is_within_allowed_dirs`) - so a misheard or adversarial phrase cannot
reach `..`, an absolute path elsewhere, or a symlink pointing outside those
three folders. This module raises PathNotAllowedError rather than silently
clamping or ignoring an out-of-scope path, so a caller can tell the user why
the request was refused instead of appearing to silently do nothing.

Delete goes through send2trash (the Windows Recycle Bin), never a permanent
os.remove - a voice command is transcribed speech, not a typed, reviewed
command, so a misheard "delete" should be recoverable.

Move/copy/rename never silently overwrite an existing file at the
destination - they raise FileExistsError instead (a builtin, so callers that
already handle OSError catch it for free; FileExistsError is a subclass).
This is deliberately a structural guard, not just a confirmation prompt: the
voice layer (main.py) additionally gates delete/move/rename behind
awaiting_confirmation, but copy is immediate since it can't destroy or
rename anything that already existed - the only way it could surprise
someone is by overwriting, and that path is closed off here instead.
"""
import fnmatch
import shutil
from pathlib import Path

from .documents import SEARCH_DIRS


class PathNotAllowedError(Exception):
    """Raised when a resolved path falls outside SEARCH_DIRS."""


def _is_within_allowed_dirs(path):
    """True if `path` resolves (symlinks/`..` included) to SEARCH_DIRS itself
    or somewhere underneath it. References the module-level SEARCH_DIRS name
    at call time (not a value captured once at import), so tests can
    monkeypatch `files.SEARCH_DIRS` to temp directories the same way
    documents.py's own tests would."""
    try:
        resolved = Path(path).resolve(strict=False)
    except OSError:
        return False
    for base in SEARCH_DIRS:
        try:
            base_resolved = Path(base).resolve(strict=False)
        except OSError:
            continue
        if resolved == base_resolved or base_resolved in resolved.parents:
            return True
    return False


def resolve_file(name):
    """Find a file matching `name` (exact name, exact stem, or substring) in
    the allowed directories. Returns a Path or None. Unlike
    documents.resolve_document, this is not limited to SUPPORTED_EXTENSIONS -
    generic file management, not text extraction, so any file type is a
    valid target."""
    name = name.strip().strip('"\'').lower()
    candidates = []
    for d in SEARCH_DIRS:
        d = Path(d)
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.is_file():
                continue
            stem, full = f.stem.lower(), f.name.lower()
            if name == full or name == stem or name in stem or name in full:
                candidates.append(f)
    if not candidates:
        return None
    exact = [c for c in candidates if c.stem.lower() == name or c.name.lower() == name]
    pool = exact or candidates
    return max(pool, key=lambda p: p.stat().st_mtime)


def resolve_dir(name):
    """Resolve a spoken folder name ('desktop'/'documents'/'downloads', or
    whatever SEARCH_DIRS' actual basenames are in a test) to one of the
    allowed directories. Returns None for anything else - intentionally NOT
    a general path resolver, so "move X to C:\\Windows" can never resolve."""
    name = name.strip().strip('"\'').lower()
    for d in SEARCH_DIRS:
        if Path(d).name.lower() == name:
            return Path(d)
    return None


def list_files(dir_name=None, limit=20):
    """Returns (filenames, error_message). Lists one allowed directory by
    spoken name, or all three combined if none is given."""
    if dir_name:
        d = resolve_dir(dir_name)
        if not d:
            return [], f"I only know Desktop, Documents, and Downloads - not {dir_name}."
        dirs = [d]
    else:
        dirs = [Path(d) for d in SEARCH_DIRS]
    names = []
    for d in dirs:
        if d.is_dir():
            names.extend(sorted(f.name for f in d.iterdir() if f.is_file()))
    return names[:limit], None


def search_files(query, limit=20):
    """Filename search (case-insensitive substring, or '*'/'?' glob if the
    query itself contains wildcards) across all three allowed directories.
    Matches by filename only, not file content - content search over these
    same documents already exists via Phase 5's RAG stack (rag.py) and is a
    different, heavier problem than "find this file by name"."""
    query = query.strip().strip('"\'').lower()
    pattern = query if any(ch in query for ch in '*?') else f'*{query}*'
    matches = []
    for d in SEARCH_DIRS:
        d = Path(d)
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file() and fnmatch.fnmatchcase(f.name.lower(), pattern):
                matches.append(f)
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[:limit]


def delete_file(path):
    """Moves `path` to the Recycle Bin via send2trash. Raises
    PathNotAllowedError if it resolves outside the allowed directories."""
    path = Path(path)
    if not _is_within_allowed_dirs(path):
        raise PathNotAllowedError(str(path))
    import send2trash
    send2trash.send2trash(str(path))


def move_file(src, dest_dir):
    """Moves `src` into `dest_dir` (keeping its filename). Both the source
    and the resolved destination must be within the allowed directories;
    refuses to overwrite an existing file at the destination."""
    src = Path(src)
    dest_dir = Path(dest_dir)
    if not _is_within_allowed_dirs(src) or not _is_within_allowed_dirs(dest_dir):
        raise PathNotAllowedError(f'{src} -> {dest_dir}')
    dest = dest_dir / src.name
    if not _is_within_allowed_dirs(dest):
        raise PathNotAllowedError(str(dest))
    if dest.exists():
        raise FileExistsError(str(dest))
    shutil.move(str(src), str(dest))
    return dest


def copy_file(src, dest_dir):
    """Copies `src` into `dest_dir` (keeping its filename), preserving
    metadata via shutil.copy2. Same allowed-directory and no-overwrite rules
    as move_file."""
    src = Path(src)
    dest_dir = Path(dest_dir)
    if not _is_within_allowed_dirs(src) or not _is_within_allowed_dirs(dest_dir):
        raise PathNotAllowedError(f'{src} -> {dest_dir}')
    dest = dest_dir / src.name
    if not _is_within_allowed_dirs(dest):
        raise PathNotAllowedError(str(dest))
    if dest.exists():
        raise FileExistsError(str(dest))
    shutil.copy2(str(src), str(dest))
    return dest


def rename_file(src, new_name):
    """Renames `src` in place (same directory). `new_name` is treated as a
    bare filename - any path components spoken/injected in it are stripped
    via Path(new_name).name, so this can never be used to relocate a file
    outside its current directory."""
    src = Path(src)
    if not _is_within_allowed_dirs(src):
        raise PathNotAllowedError(str(src))
    new_name = Path(new_name.strip()).name
    dest = src.parent / new_name
    if not _is_within_allowed_dirs(dest):
        raise PathNotAllowedError(str(dest))
    if dest.exists():
        raise FileExistsError(str(dest))
    src.rename(dest)
    return dest
