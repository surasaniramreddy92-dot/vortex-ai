"""Unit tests for command dispatch (src/vortex/main.py's execute(), which as
of docs/REFACTOR_PLAN.md Step 6 routes through core/intent_router.route()
for classification and a core/capability_registry.CapabilityRegistry for
dispatch) added 2026-08-16 (Phase 2), still exercising execute() end-to-end.
Every existing voice command from before this refactor is asserted here to
still route to the exact same action, proving each successive rewrite
(if/elif chain -> flat registry list -> intent router + capability
registry) changed only HOW dispatch happens, not WHAT any command does. New
file-ops/search commands are asserted alongside them since they register
through the exact same mechanism, not as separate, disconnected work.

Constructs a real Vortex() (no hardware touched at construction - see
test_barge_in.py's vortex_instance fixture for why: WakeDetector loads the
ONNX model but never opens a mic stream until .start(), and
BrowserAgent/RagStore both degrade gracefully with no real browser/DB
running), then monkeypatches every side-effecting method (subprocess-
launching, psutil-touching, browser-touching, LLM-calling) so these tests
exercise real regex/dispatch logic without actually opening apps, shutting
down Windows, or hitting the network.
"""
import sys

import pytest

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.app import Vortex
from vortex.core import intent_router


@pytest.fixture
def v():
    inst = Vortex()
    inst.spoken = []
    inst.speak = lambda text: inst.spoken.append(text)
    inst.calls = []

    def recorder(name):
        def _fn(*a, **k):
            inst.calls.append((name, a, k))
        return _fn

    for name in ('open_target', 'close_named_app', 'close_all_apps', 'system_shutdown',
                 'system_restart', 'lock_system', 'stop'):
        setattr(inst, name, recorder(name))

    class FakeBrowser:
        def close(self):
            inst.calls.append(('browser.close', (), {}))

        def read_page(self):
            return 'page text'

        def play_youtube(self, q):
            inst.calls.append(('browser.play_youtube', (q,), {}))
            return 'playing'

        def search(self, q):
            inst.calls.append(('browser.search', (q,), {}))
            return 'searched'

        def open(self, q):
            inst.calls.append(('browser.open', (q,), {}))
            return 'opened'

        def click_text(self, q):
            inst.calls.append(('browser.click_text', (q,), {}))
            return 'clicked'

    inst.browser = FakeBrowser()
    inst.ask_llm_stream = lambda cmd: iter([f'LLM:{cmd}'])
    inst.speak_stream = lambda gen: inst.calls.append(('speak_stream', (list(gen),), {}))
    inst.summarize_document = recorder('summarize_document')
    inst.answer_document_question = recorder('answer_document_question')
    inst.recall_memory = recorder('recall_memory')

    class FakeMail:
        def __init__(self):
            self.unread = []
            self.sent = []

        def list_unread(self):
            return self.unread

        def get_email_body(self, message_id):
            return next(e['body'] for e in self.unread if e['id'] == message_id)

        def send_reply(self, message_id, body):
            self.sent.append((message_id, body))

    inst.mail = FakeMail()
    inst.draft_email_reply = lambda original_body, instruction: f'DRAFT[{instruction}]'

    yield inst
    inst.memory.close()
    if inst.rag is not None:
        inst.rag.close()


def last_call(v, name):
    matches = [c for c in v.calls if c[0] == name]
    assert matches, f'{name} was never called; calls={v.calls}'
    return matches[-1]


# ---------- pre-existing commands: must dispatch identically to before the registry refactor ----------

def test_shutdown_vortex(v):
    v.execute('shutdown vortex')
    last_call(v, 'stop')


def test_time(v):
    v.execute('what is the time')
    assert any('current time' in s for s in v.spoken)


def test_date(v):
    v.execute('what is the date')
    assert any("Today's date" in s for s in v.spoken)


def test_close_all_prompts_for_confirmation_not_immediate(v):
    v.execute('close all')
    assert v.awaiting_confirmation == {'action': 'close_all'}
    assert not any(c[0] == 'close_all_apps' for c in v.calls)


def test_restart_system_prompts(v):
    v.execute('restart system')
    assert v.awaiting_confirmation == {'action': 'restart'}


def test_reboot_system_prompts(v):
    v.execute('reboot system')
    assert v.awaiting_confirmation == {'action': 'restart'}


def test_shutdown_system_prompts(v):
    v.execute('shutdown system')
    assert v.awaiting_confirmation == {'action': 'shutdown'}


def test_lock_system(v):
    v.execute('lock the computer')
    last_call(v, 'lock_system')


def test_close_browser(v):
    v.execute('close browser')
    last_call(v, 'browser.close')


def test_quit_browser(v):
    v.execute('quit browser')
    last_call(v, 'browser.close')


def test_read_page(v):
    v.execute('read the page')
    assert 'page text' in v.spoken


def test_youtube_play(v):
    v.execute('youtube play some song')
    _, args, _ = last_call(v, 'browser.play_youtube')
    assert args == ('some song',)


def test_play_on_youtube(v):
    v.execute('play some song on youtube')
    _, args, _ = last_call(v, 'browser.play_youtube')
    assert args == ('some song',)


def test_search_youtube_for(v):
    v.execute('search youtube for some song')
    _, args, _ = last_call(v, 'browser.play_youtube')
    assert args == ('some song',)


def test_web_search(v):
    v.execute('search for coffee shops')
    _, args, _ = last_call(v, 'browser.search')
    assert args == ('coffee shops',)


def test_google(v):
    v.execute('google coffee shops')
    _, args, _ = last_call(v, 'browser.search')
    assert args == ('coffee shops',)


def test_browse_to(v):
    v.execute('go to example.com')
    _, args, _ = last_call(v, 'browser.open')
    assert args == ('example.com',)


def test_click(v):
    v.execute('click on submit button')
    _, args, _ = last_call(v, 'browser.click_text')
    assert args == ('submit button',)


def test_close_named_app(v):
    v.execute('close chrome')
    _, args, _ = last_call(v, 'close_named_app')
    assert args == ('chrome',)


def test_open_target(v):
    v.execute('open chrome')
    _, args, _ = last_call(v, 'open_target')
    assert args == ('chrome',)


def test_document_question(v):
    v.execute('what does resume say about experience')
    _, args, _ = last_call(v, 'answer_document_question')
    assert args == ('resume', 'experience')


def test_summarize_document(v):
    v.execute('summarize resume.pdf')
    _, args, _ = last_call(v, 'summarize_document')
    assert args == ('resume.pdf',)


def test_read_document_fallback(v):
    v.execute('read resume.pdf')
    _, args, _ = last_call(v, 'summarize_document')
    assert args == ('resume.pdf',)


def test_unmatched_falls_through_to_llm(v):
    v.execute('tell me a joke')
    _, args, _ = last_call(v, 'speak_stream')
    assert args[0] == ['LLM:tell me a joke']


def test_empty_command_does_not_crash(v):
    v.execute('')
    assert 'I did not catch that Boss.' in v.spoken
    assert v.calls == []


# ---------- registry structure itself ----------
# docs/REFACTOR_PLAN.md Step 6: classification (intent_router.ALL_INTENT_TYPES,
# a tuple of Intent dataclasses, each self-describing via its
# name/destructive/description ClassVars) and dispatch (v._registry, a
# CapabilityRegistry) replaced the old flat list-of-dicts `_registry` these
# tests used to inspect directly - same structural guarantees (required
# shape, unique names, full dispatch coverage), asserted against the new
# shape instead.

def test_intent_types_have_required_metadata():
    for t in intent_router.ALL_INTENT_TYPES:
        assert isinstance(t.name, str) and t.name
        assert isinstance(t.destructive, bool)
        assert isinstance(t.description, str) and t.description


def test_intent_type_names_are_unique():
    names = [t.name for t in intent_router.ALL_INTENT_TYPES]
    assert len(names) == len(set(names))


def test_every_routable_intent_type_has_a_capability_registry_handler(v):
    # Unhandled never reaches the registry - execute() routes it straight to
    # the LLM fallback (see main.py's execute()) - so it's the one type
    # deliberately excluded here, same as CapabilityRegistry.__init__'s own
    # coverage assertion.
    routable = set(intent_router.ALL_INTENT_TYPES) - {intent_router.Unhandled}
    assert routable <= set(v._registry._handlers)


def test_new_file_commands_are_registered():
    names = {t.name for t in intent_router.ALL_INTENT_TYPES}
    assert {'list_files', 'search_files', 'delete_file_prompt', 'move_file_prompt',
            'copy_file', 'rename_file_prompt'} <= names


def test_read_screen_is_registered_and_dispatches(v, monkeypatch):
    """"read my screen" / "what's on my screen" (added 2026-08-17, direct
    user request) must reach screen.read_screen_text(), not fall through to
    read_document's broad "read (?:me )?(.+)" catch-all (which would try to
    find a document literally named "my screen")."""
    from vortex import screen as screen_reader
    monkeypatch.setattr(screen_reader, 'read_screen_text',
                         lambda: ('some screen text', None))

    for phrase in ('read my screen', 'read the screen', "what's on my screen"):
        v.spoken.clear()
        v.execute(phrase)
        assert v.spoken == ['some screen text'], f'{phrase!r} did not reach h_read_screen'


def test_read_screen_speaks_error_when_unavailable(v, monkeypatch):
    from vortex import screen as screen_reader
    monkeypatch.setattr(screen_reader, 'read_screen_text',
                         lambda: (None, "I can't read the screen right now."))
    v.execute('read my screen')
    assert v.spoken == ["I can't read the screen right now."]


def test_read_my_document_still_reaches_read_document_not_screen(v):
    """Sanity check the ordering didn't break the pre-existing catch-all:
    "read <actual document name>" must still route to h_read_document, not
    get accidentally swallowed by the new read_screen entry."""
    v.execute('read my_notes.txt')
    last_call(v, 'summarize_document')


def test_list_files_matches_on_as_well_as_in(v, monkeypatch, tmp_path):
    """Regression test: a live acoustic test on 2026-08-17 found "list files
    on desktop" (natural phrasing a real user actually said) fell through to
    the generic LLM fallback instead of the list_files handler, because the
    matcher only accepted "list files in X" - the model then hallucinated a
    plausible-looking but fake file listing instead of failing clearly. The
    fix broadens the matcher to accept "on" too; this proves both phrasings
    now reach the real handler instead of the LLM fallback."""
    from vortex import files as fileops
    desktop = tmp_path / 'Desktop'
    desktop.mkdir()
    (desktop / 'report.txt').write_text('x', encoding='utf-8')
    monkeypatch.setattr(fileops, 'SEARCH_DIRS', [desktop])

    for phrase in ('list files in desktop', 'list files on desktop'):
        v.spoken.clear()
        v.calls.clear()
        v.execute(phrase)
        assert not any(c[0] == 'speak_stream' for c in v.calls), (
            f'{phrase!r} fell through to the LLM fallback instead of list_files')
        assert any('report.txt' in s for s in v.spoken), f'{phrase!r} did not list the real file'


# ---------- email ----------

def test_check_email_speaks_a_summary(v):
    v.mail.unread = [
        {'id': 'm1', 'sender': 'Jane Doe <jane@x.com>', 'subject': 'Lunch?', 'snippet': ''},
        {'id': 'm2', 'sender': 'bob@x.com', 'subject': 'Re: Project', 'snippet': ''},
    ]
    v.execute('check my email')
    assert any('2 unread emails' in s and 'Jane Doe' in s and 'Lunch?' in s and 'bob@x.com' in s
               for s in v.spoken)


def test_check_email_with_no_unread_says_so(v):
    v.mail.unread = []
    v.execute('check my email')
    assert v.spoken == ['No unread emails.']


def test_check_email_when_not_configured_degrades_gracefully(v):
    def boom():
        raise FileNotFoundError('no credentials.json')
    v.mail.list_unread = boom
    v.execute('check my email')
    assert any("isn't set up yet" in s for s in v.spoken)


def test_reply_to_email_drafts_and_prompts_for_confirmation_not_immediate_send(v):
    v.mail.unread = [{'id': 'm1', 'sender': 'John Doe <john@x.com>', 'subject': 'Meeting',
                       'snippet': '', 'body': 'Can you make it at 5?'}]
    v.execute('reply to john and say i will be there')
    assert v.awaiting_confirmation == {
        'action': 'send_email_reply', 'message_id': 'm1',
        'body': 'DRAFT[i will be there]', 'to': 'John Doe <john@x.com>', 'subject': 'Meeting'}
    assert v.mail.sent == [], 'must not send before confirmation'
    assert any('DRAFT[i will be there]' in s for s in v.spoken)


def test_reply_to_email_no_match_speaks_error(v):
    v.mail.unread = [{'id': 'm1', 'sender': 'john@x.com', 'subject': 'Hi', 'snippet': '', 'body': ''}]
    v.execute('reply to nobody and say hi')
    assert any("couldn't find" in s for s in v.spoken)
    assert v.awaiting_confirmation is None


def test_reply_to_email_ambiguous_match_asks_for_specificity(v):
    v.mail.unread = [
        {'id': 'm1', 'sender': 'john@x.com', 'subject': 'Meeting A', 'snippet': '', 'body': ''},
        {'id': 'm2', 'sender': 'john@x.com', 'subject': 'Meeting B', 'snippet': '', 'body': ''},
    ]
    v.execute('reply to john and say hi')
    assert any('please be more specific' in s for s in v.spoken)
    assert v.awaiting_confirmation is None


def test_confirmed_email_reply_actually_sends(v):
    v.mail.unread = [{'id': 'm1', 'sender': 'john@x.com', 'subject': 'Hi', 'snippet': '', 'body': 'body'}]
    v.execute('reply to john and say ok')
    v.execute('yes')
    assert v.mail.sent == [('m1', 'DRAFT[ok]')]
    assert v.awaiting_confirmation is None
    assert 'Sent.' in v.spoken


def test_declined_email_reply_never_sends(v):
    v.mail.unread = [{'id': 'm1', 'sender': 'john@x.com', 'subject': 'Hi', 'snippet': '', 'body': 'body'}]
    v.execute('reply to john and say ok')
    v.execute('no thanks')
    assert v.mail.sent == []
    assert v.awaiting_confirmation is None


# ---------- conversation memory recall ----------

def test_recall_memory_reaches_the_real_recall_memory_method(v):
    v.execute('do you remember my favorite color')
    _, args, _ = last_call(v, 'recall_memory')
    assert args == ('my favorite color',)


def test_recall_memory_alternate_phrasing(v):
    v.execute('what did i tell you about the project deadline')
    _, args, _ = last_call(v, 'recall_memory')
    assert args == ('the project deadline',)


# ---------- Vortex.recall_memory itself (not mocked by the `v` fixture above,
# which replaces it with a recorder to test dispatch only) ----------

def test_recall_memory_degrades_gracefully_when_rag_unavailable():
    inst = Vortex()
    inst.spoken = []
    inst.speak = lambda text: inst.spoken.append(text)
    inst.rag = None
    inst.recall_memory('anything')
    assert any("can't search past conversations" in s for s in inst.spoken)
    inst.memory.close()


def test_recall_memory_search_failure_degrades_gracefully():
    class BoomRag:
        def search_turns(self, query):
            raise RuntimeError('qdrant down')
    inst = Vortex()
    inst.spoken = []
    inst.speak = lambda text: inst.spoken.append(text)
    inst.rag = BoomRag()
    inst.recall_memory('anything')
    assert any("couldn't search my memory" in s for s in inst.spoken)
    inst.memory.close()


def test_recall_memory_no_relevant_turns_says_so():
    class EmptyRag:
        def search_turns(self, query):
            return []
    inst = Vortex()
    inst.spoken = []
    inst.speak = lambda text: inst.spoken.append(text)
    inst.rag = EmptyRag()
    inst.recall_memory('anything')
    assert inst.spoken == ["I don't have anything relevant in memory about that."]
    inst.memory.close()


def test_recall_memory_with_results_answers_via_the_llm():
    class FakeRag:
        def search_turns(self, query):
            return [{'role': 'user', 'content': 'I like Rust'}]
    inst = Vortex()
    inst.spoken = []
    inst.speak = lambda text: inst.spoken.append(text)
    inst.speak_stream = lambda gen: inst.spoken.append(''.join(gen))
    inst.rag = FakeRag()
    inst._stream_llm_answer = lambda system_prompt, user_content: iter([f'ANSWER[{user_content[:20]}]'])
    inst.recall_memory('what language do I like')
    assert any(s.startswith('ANSWER[') for s in inst.spoken)
    inst.memory.close()
