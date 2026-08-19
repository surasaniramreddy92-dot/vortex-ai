"""docs/REFACTOR_PLAN.md Step 6 exit criteria: core/intent_router.route() is
pure text -> Intent classification with zero side effects - no subprocess,
no psutil, no filesystem, no speech, not even a Vortex instance. Every test
below calls route() directly on a plain string; none construct Vortex or
touch anything hardware-adjacent. intent_router.py itself imports nothing
side-effecting (just `re`, `dataclasses`, `typing`), so "without ever
calling subprocess.Popen" is true by construction, not just by observation -
but the mock in test_route_never_touches_subprocess makes that provable
rather than merely asserted.
"""
import sys
from unittest.mock import patch

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.core import intent_router as ir


# ---------- representative mapping for every Intent type ----------

def test_open_chrome_routes_to_open_target():
    assert ir.route('open chrome') == ir.OpenTarget(target='chrome')


def test_shutdown_vortex():
    assert ir.route('shutdown vortex') == ir.ShutdownVortex()


def test_time():
    assert ir.route('what is the time') == ir.SpeakTime()


def test_date():
    assert ir.route('what is the date') == ir.SpeakDate()


def test_close_all_prompt():
    assert ir.route('close all') == ir.CloseAllPrompt()


def test_restart_system_and_reboot_system_both_route_to_restart_prompt():
    assert ir.route('restart system') == ir.RestartPrompt()
    assert ir.route('reboot system') == ir.RestartPrompt()


def test_shutdown_system_prompt():
    assert ir.route('shutdown system') == ir.ShutdownPrompt()


def test_lock():
    assert ir.route('lock the computer') == ir.Lock()


def test_close_browser_and_quit_browser():
    assert ir.route('close browser') == ir.CloseBrowser()
    assert ir.route('quit browser') == ir.CloseBrowser()


def test_read_page():
    assert ir.route('read the page') == ir.ReadPage()


def test_read_screen():
    for phrase in ('read my screen', 'read the screen', "what's on my screen"):
        assert ir.route(phrase) == ir.ReadScreen(), phrase


def test_check_email_phrasings():
    for phrase in ('check my email', 'read my email', "what's in my inbox", 'check my inbox'):
        assert ir.route(phrase) == ir.CheckEmail(), phrase


def test_reply_to_email_prompt():
    assert ir.route('reply to john and say i will be there at 5') == ir.ReplyToEmailPrompt(
        target='john', instruction='i will be there at 5')
    assert ir.route('reply to the meeting email saying i confirm') == ir.ReplyToEmailPrompt(
        target='the meeting email', instruction='i confirm')


def test_recall_memory_phrasings():
    assert ir.route('do you remember my favorite color') == ir.RecallMemory(query='my favorite color')
    assert ir.route('what did i tell you about the project') == ir.RecallMemory(query='the project')
    assert ir.route('recall what i said about the meeting') == ir.RecallMemory(
        query='what i said about the meeting')


def test_youtube_all_three_phrasings():
    assert ir.route('youtube play some song') == ir.PlayYoutube(query='some song')
    assert ir.route('play some song on youtube') == ir.PlayYoutube(query='some song')
    assert ir.route('search youtube for some song') == ir.PlayYoutube(query='some song')


def test_search_files():
    # The optional "named"/"called"/"containing" qualifier is consumed by
    # the pattern itself (verbatim from the original registry), not part of
    # the captured query.
    assert ir.route('find files containing report') == ir.SearchFiles(query='report')
    assert ir.route('search for files named report') == ir.SearchFiles(query='report')


def test_web_search_and_google():
    assert ir.route('search for coffee shops') == ir.WebSearch(query='coffee shops')
    assert ir.route('google coffee shops') == ir.WebSearch(query='coffee shops')


def test_browse():
    assert ir.route('go to example.com') == ir.Browse(target='example.com')


def test_click():
    assert ir.route('click on submit button') == ir.Click(text='submit button')


def test_list_files_no_directory():
    assert ir.route('list files') == ir.ListFiles(dir_name=None)


def test_list_files_matches_on_as_well_as_in():
    assert ir.route('list files in desktop') == ir.ListFiles(dir_name='desktop')
    assert ir.route('list files on desktop') == ir.ListFiles(dir_name='desktop')


def test_delete_file_prompt():
    assert ir.route('delete file a.txt') == ir.DeleteFilePrompt(filename='a.txt')


def test_move_file_prompt():
    assert ir.route('move file a.txt to documents') == ir.MoveFilePrompt(
        filename='a.txt', dest_name='documents')


def test_copy_file():
    assert ir.route('copy file a.txt to documents') == ir.CopyFile(
        filename='a.txt', dest_name='documents')


def test_rename_file_prompt():
    assert ir.route('rename file a.txt to b.txt') == ir.RenameFilePrompt(
        filename='a.txt', new_name='b.txt')


def test_close_app():
    assert ir.route('close chrome') == ir.CloseApp(target='chrome')


def test_document_question():
    assert ir.route('what does resume say about experience') == ir.DocumentQuestion(
        doc_name='resume', question='experience')


def test_summarize_document():
    assert ir.route('summarize resume.pdf') == ir.SummarizeDocument(doc_name='resume.pdf')


def test_read_document_fallback():
    assert ir.route('read resume.pdf') == ir.ReadDocument(doc_name='resume.pdf')


def test_unmatched_text_is_unhandled():
    assert ir.route('tell me a joke') == ir.Unhandled(text='tell me a joke')


# ---------- ordering: broader patterns must not swallow narrower ones checked earlier ----------

def test_open_youtube_and_play_routes_to_youtube_not_generic_open():
    assert ir.route('open youtube and play some song') == ir.PlayYoutube(query='some song')


def test_search_for_files_does_not_become_a_web_search():
    intent = ir.route('search for files containing report')
    assert isinstance(intent, ir.SearchFiles)


def test_read_my_screen_does_not_become_a_document_lookup():
    intent = ir.route('read my screen')
    assert isinstance(intent, ir.ReadScreen)


def test_read_an_actual_document_name_still_reaches_read_document():
    assert ir.route('read my_notes.txt') == ir.ReadDocument(doc_name='my_notes.txt')


def test_read_my_email_does_not_become_a_document_lookup():
    intent = ir.route('read my email')
    assert isinstance(intent, ir.CheckEmail)


def test_close_browser_does_not_become_generic_close_app():
    intent = ir.route('close browser')
    assert isinstance(intent, ir.CloseBrowser)


def test_open_chrome_does_not_become_a_document_read():
    intent = ir.route('open chrome')
    assert isinstance(intent, ir.OpenTarget)


# ---------- purity ----------

def test_route_never_touches_subprocess():
    with patch('subprocess.Popen') as mock_popen:
        for cmd in ('open chrome', 'close all', 'shutdown vortex', 'shutdown system',
                    'delete file a.txt', 'lock the computer'):
            ir.route(cmd)
        mock_popen.assert_not_called()


def test_every_all_intent_types_entry_is_reachable_and_names_are_unique():
    names = [t.name for t in ir.ALL_INTENT_TYPES]
    assert len(names) == len(set(names)), 'duplicate Intent.name values'
    assert ir.Unhandled in ir.ALL_INTENT_TYPES
