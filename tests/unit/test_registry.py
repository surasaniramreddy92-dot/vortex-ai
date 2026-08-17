"""Unit tests for the capability registry (src/vortex/main.py's
_build_registry/execute) added 2026-08-16 (Phase 2). Every existing voice
command from before this refactor is asserted here to still route to the
exact same action, proving the if/elif-chain -> registry rewrite changed
only HOW dispatch happens, not WHAT any command does. New file-ops/search
commands are asserted alongside them since they register through the exact
same mechanism, not as separate, disconnected work.

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

from vortex.main import Vortex


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

def test_registry_entries_have_required_shape(v):
    for entry in v._registry:
        assert set(entry) == {'name', 'matcher', 'handler', 'destructive', 'description'}
        assert callable(entry['matcher'])
        assert callable(entry['handler'])
        assert isinstance(entry['destructive'], bool)
        assert isinstance(entry['description'], str) and entry['description']


def test_registry_names_are_unique(v):
    names = [entry['name'] for entry in v._registry]
    assert len(names) == len(set(names))


def test_new_file_commands_are_registered(v):
    names = {entry['name'] for entry in v._registry}
    assert {'list_files', 'search_files', 'delete_file_prompt', 'move_file_prompt',
            'copy_file', 'rename_file_prompt'} <= names


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
