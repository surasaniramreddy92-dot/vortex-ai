"""docs/REFACTOR_PLAN.md Step 6: pure text -> Intent classification, split
out of main.py's old `_build_registry`/`execute` fused "classify + run"
chain. `route(cmd)` has zero side effects - no subprocess, no psutil, no
speech, no filesystem access - so it's testable with a plain string in, an
Intent out (see tests/unit/test_intent_routing.py). Actually invoking the
capability that handles an Intent is core/capability_registry.py's job, not
this module's.

Every pattern, and the *order* patterns are checked in, is a direct,
unchanged port of the original if/elif chain (later, the registry list) -
the ordering comments below are preserved from there, not new. Order still
matters for the same reasons it always did: several matchers are
intentionally checked before broader ones that would otherwise swallow
them (documented inline at each such case)."""
import re
from dataclasses import dataclass
from typing import ClassVar, Optional


@dataclass(frozen=True)
class ShutdownVortex:
    name: ClassVar[str] = 'shutdown_vortex'
    destructive: ClassVar[bool] = True
    description: ClassVar[str] = 'Stop the VORTEX process itself.'


@dataclass(frozen=True)
class SpeakTime:
    name: ClassVar[str] = 'time'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Speak the current time.'


@dataclass(frozen=True)
class SpeakDate:
    name: ClassVar[str] = 'date'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = "Speak today's date."


@dataclass(frozen=True)
class CloseAllPrompt:
    name: ClassVar[str] = 'close_all_prompt'
    destructive: ClassVar[bool] = True
    description: ClassVar[str] = 'Prompt to close every non-protected running app.'


@dataclass(frozen=True)
class RestartPrompt:
    name: ClassVar[str] = 'restart_prompt'
    destructive: ClassVar[bool] = True
    description: ClassVar[str] = 'Prompt to restart the system.'


@dataclass(frozen=True)
class ShutdownPrompt:
    name: ClassVar[str] = 'shutdown_prompt'
    destructive: ClassVar[bool] = True
    description: ClassVar[str] = 'Prompt to shut down the system.'


@dataclass(frozen=True)
class Lock:
    name: ClassVar[str] = 'lock'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Lock the workstation (no confirmation - trivially reversible).'


@dataclass(frozen=True)
class CloseBrowser:
    name: ClassVar[str] = 'close_browser'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Close the automated browser session.'


@dataclass(frozen=True)
class ReadPage:
    name: ClassVar[str] = 'read_page'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Read back the current browser page.'


@dataclass(frozen=True)
class ReadScreen:
    name: ClassVar[str] = 'read_screen'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'OCR and read back the current screen.'


@dataclass(frozen=True)
class PlayYoutube:
    query: str
    name: ClassVar[str] = 'youtube'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Search and play a YouTube video.'


@dataclass(frozen=True)
class SearchFiles:
    query: str
    name: ClassVar[str] = 'search_files'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Find files by name in Desktop/Documents/Downloads.'


@dataclass(frozen=True)
class WebSearch:
    query: str
    name: ClassVar[str] = 'web_search'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Search the web.'


@dataclass(frozen=True)
class Browse:
    target: str
    name: ClassVar[str] = 'browse'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Navigate the browser to a URL or site name.'


@dataclass(frozen=True)
class Click:
    text: str
    name: ClassVar[str] = 'click'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Click matching text on the current page.'


@dataclass(frozen=True)
class ListFiles:
    dir_name: Optional[str]
    name: ClassVar[str] = 'list_files'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'List files in Desktop/Documents/Downloads.'


@dataclass(frozen=True)
class DeleteFilePrompt:
    filename: str
    name: ClassVar[str] = 'delete_file_prompt'
    destructive: ClassVar[bool] = True
    description: ClassVar[str] = 'Prompt to delete a file (Recycle Bin, not permanent).'


@dataclass(frozen=True)
class MoveFilePrompt:
    filename: str
    dest_name: str
    name: ClassVar[str] = 'move_file_prompt'
    destructive: ClassVar[bool] = True
    description: ClassVar[str] = 'Prompt to move a file between the allowed directories.'


@dataclass(frozen=True)
class CopyFile:
    filename: str
    dest_name: str
    name: ClassVar[str] = 'copy_file'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Copy a file between the allowed directories (no overwrite).'


@dataclass(frozen=True)
class RenameFilePrompt:
    filename: str
    new_name: str
    name: ClassVar[str] = 'rename_file_prompt'
    destructive: ClassVar[bool] = True
    description: ClassVar[str] = 'Prompt to rename a file in place.'


@dataclass(frozen=True)
class CloseApp:
    target: str
    name: ClassVar[str] = 'close_app'
    destructive: ClassVar[bool] = True
    description: ClassVar[str] = 'Close one named running app.'


@dataclass(frozen=True)
class OpenTarget:
    target: str
    name: ClassVar[str] = 'open'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Open a named app, web app, or web search.'


@dataclass(frozen=True)
class DocumentQuestion:
    doc_name: str
    question: str
    name: ClassVar[str] = 'document_question'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Answer a question about a document.'


@dataclass(frozen=True)
class SummarizeDocument:
    doc_name: str
    name: ClassVar[str] = 'summarize'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Summarize a document.'


@dataclass(frozen=True)
class ReadDocument:
    doc_name: str
    name: ClassVar[str] = 'read_document'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = 'Read (summarize) a document.'


@dataclass(frozen=True)
class Unhandled:
    text: str
    name: ClassVar[str] = 'unhandled'
    destructive: ClassVar[bool] = False
    description: ClassVar[str] = "Fell through every pattern - goes to the LLM fallback."


# Every Intent type route() can return, in the exact order they're checked
# below - used by tests/unit/test_intent_routing.py to verify completeness
# (every type reachable, every name unique) without hand-maintaining a
# second, separate list that could drift from the real routing order.
ALL_INTENT_TYPES = (
    ShutdownVortex, SpeakTime, SpeakDate, CloseAllPrompt, RestartPrompt, ShutdownPrompt,
    Lock, CloseBrowser, ReadPage, ReadScreen, PlayYoutube, SearchFiles, WebSearch, Browse,
    Click, ListFiles, DeleteFilePrompt, MoveFilePrompt, CopyFile, RenameFilePrompt,
    CloseApp, OpenTarget, DocumentQuestion, SummarizeDocument, ReadDocument, Unhandled,
)

_YOUTUBE_PATTERNS = [
    re.compile(r'(?:open |go to )?youtube(?: and)? (?:play|search for|find|watch) (.+)'),
    re.compile(r'play (.+) on youtube'),
    re.compile(r'search youtube for (.+)'),
]

_TIME = re.compile(r'\btime\b')
_DATE = re.compile(r'\bdate\b')
_LOCK = re.compile(r'\block\b.*\b(?:system|computer|screen|pc)\b')
_READ_PAGE = re.compile(r"(?:read|what'?s on) (?:the |this )?page")
_READ_SCREEN = re.compile(r"(?:read|what'?s on) (?:my |the |this )?screen")
_SEARCH_FILES = re.compile(r'(?:find|search for) files?(?: named| called| containing)? (.+)')
_WEB_SEARCH = re.compile(r'(?:search(?: the web)? for|google) (.+)')
_BROWSE = re.compile(r'(?:go to|browse to|browse) (.+)')
_CLICK = re.compile(r'click (?:on )?(.+)')
_LIST_FILES = re.compile(r'list files?(?: (?:in|on) (.+))?$')
_DELETE_FILE = re.compile(r'delete file (.+)')
_MOVE_FILE = re.compile(r'move file (.+) to (.+)')
_COPY_FILE = re.compile(r'copy file (.+) to (.+)')
_RENAME_FILE = re.compile(r'rename file (.+) to (.+)')
_CLOSE_APP = re.compile(r'close (.+)')
_OPEN = re.compile(r'open (.+)')
_DOCUMENT_QUESTION = re.compile(r'what does (.+?) say about (.+)')
_SUMMARIZE = re.compile(r'(?:summarize|summarise) (.+)')
_READ_DOCUMENT = re.compile(r'read (?:me )?(.+)')


def route(cmd):
    """cmd must already be lowercased/stripped (voice/stt.py does this
    before anything reaches here, same as before this extraction)."""
    if 'shutdown vortex' in cmd:
        return ShutdownVortex()
    if _TIME.search(cmd):
        return SpeakTime()
    if _DATE.search(cmd):
        return SpeakDate()
    if 'close all' in cmd:
        return CloseAllPrompt()
    if 'restart system' in cmd or 'reboot system' in cmd:
        return RestartPrompt()
    if 'shutdown system' in cmd:
        return ShutdownPrompt()
    if _LOCK.search(cmd):
        return Lock()
    # Browser commands are checked before the generic close/open/read
    # patterns below so "close browser" / "read the page" don't get misrouted.
    if 'close browser' in cmd or 'quit browser' in cmd:
        return CloseBrowser()
    if _READ_PAGE.search(cmd):
        return ReadPage()
    # Checked before read_document's broad "read (?:me )?(.+)" catch-all
    # below, for the same reason read_page is - otherwise "read my screen"
    # would be interpreted as "find a document named my screen".
    if _READ_SCREEN.search(cmd):
        return ReadScreen()
    # YouTube search-and-play is checked before the generic "open (.+)"
    # pattern below, which used to swallow phrases like "open youtube and
    # play X" whole and fall through to a dumb literal-phrase web search.
    for pattern in _YOUTUBE_PATTERNS:
        m = pattern.match(cmd)
        if m:
            return PlayYoutube(query=m.group(1).strip())
    # File search is checked before the generic web-search pattern below -
    # otherwise "search for files containing report" would run a web search
    # for "files containing report" instead of a local filename search.
    m = _SEARCH_FILES.match(cmd)
    if m:
        return SearchFiles(query=m.group(1).strip())
    m = _WEB_SEARCH.match(cmd)
    if m:
        return WebSearch(query=m.group(1).strip())
    m = _BROWSE.match(cmd)
    if m:
        return Browse(target=m.group(1).strip())
    m = _CLICK.match(cmd)
    if m:
        return Click(text=m.group(1).strip())
    m = _LIST_FILES.match(cmd)
    if m:
        return ListFiles(dir_name=m.group(1).strip() if m.group(1) else None)
    m = _DELETE_FILE.match(cmd)
    if m:
        return DeleteFilePrompt(filename=m.group(1).strip())
    m = _MOVE_FILE.match(cmd)
    if m:
        return MoveFilePrompt(filename=m.group(1).strip(), dest_name=m.group(2).strip())
    m = _COPY_FILE.match(cmd)
    if m:
        return CopyFile(filename=m.group(1).strip(), dest_name=m.group(2).strip())
    m = _RENAME_FILE.match(cmd)
    if m:
        return RenameFilePrompt(filename=m.group(1).strip(), new_name=m.group(2).strip())
    m = _CLOSE_APP.match(cmd)
    if m:
        return CloseApp(target=m.group(1).strip())
    m = _OPEN.match(cmd)
    if m:
        return OpenTarget(target=m.group(1).strip())
    # Document commands, checked after the app-launching "open (.+)" pattern
    # so "open chrome" still launches an app rather than looking for a file.
    m = _DOCUMENT_QUESTION.match(cmd)
    if m:
        return DocumentQuestion(doc_name=m.group(1).strip(), question=m.group(2).strip())
    m = _SUMMARIZE.match(cmd)
    if m:
        return SummarizeDocument(doc_name=m.group(1).strip())
    m = _READ_DOCUMENT.match(cmd)
    if m:
        return ReadDocument(doc_name=m.group(1).strip())
    return Unhandled(text=cmd)
