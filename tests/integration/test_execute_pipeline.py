"""docs/REFACTOR_PLAN.md Step 9: integration tests - real intent_router,
real capability_registry, real tools/system/*, real fileops, all wired
through a real Vortex().execute() call, not mocked at the module boundary
the way tests/unit/test_registry.py's fixture mocks open_target/
close_named_app/etc. directly. Only genuinely dangerous or environment-
dependent operations are swapped for fakes:

  - PlatformAdapter (shutdown/restart/lock) -> FakePlatformAdapter, so
    dispatching a confirmed shutdown/restart/lock intent is provably
    reached without ever calling subprocess.Popen('shutdown ...').
  - BrowserAgent -> FakeBrowser (no real Playwright session).
  - send2trash.send2trash -> mocked, so delete-confirmation tests don't
    depend on a real Windows Recycle Bin.

Everything else - regex classification, dispatch, psutil process
enumeration, real filesystem operations against a tmp_path - runs for
real. The one test that launches an actual OS process (test_open_and_close_
a_real_notepad_process_end_to_end) is marked `hardware` and skipped by
default in CI (needs a real Windows desktop with notepad.exe present),
exactly like this plan step says tests needing "a real microphone, Ollama,
or a Windows GUI" should be.
"""
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.app import Vortex
from vortex.platform.base import PlatformAdapter


class FakePlatformAdapter(PlatformAdapter):
    def __init__(self):
        self.calls = []

    def shutdown(self):
        self.calls.append('shutdown')

    def restart(self):
        self.calls.append('restart')

    def lock(self):
        self.calls.append('lock')


class FakeBrowser:
    def close(self):
        pass

    def open(self, target):
        return f'opened {target}'


@pytest.fixture
def v():
    inst = Vortex()
    inst.spoken = []
    inst.speak = lambda text: inst.spoken.append(text)
    inst.platform = FakePlatformAdapter()
    inst.browser = FakeBrowser()
    yield inst
    inst.memory.close()
    if inst.rag is not None:
        inst.rag.close()


# ---------- real intent_router -> real capability_registry -> real tools/system ----------

def test_open_target_reaches_the_real_open_target_capability(v):
    """Real route() classifies "open notepad" as OpenTarget, real
    CapabilityRegistry.dispatch() calls the real tools/system/apps.py logic
    - which recognizes "notepad" as a known native app and would try to
    launch it. subprocess.Popen is mocked here (not the app-level
    open_target method), so the classification/dispatch machinery all runs
    for real; only the actual OS-level process launch is stubbed."""
    with patch('vortex.tools.system.apps.subprocess.Popen') as mock_popen:
        v.execute('open notepad')
    mock_popen.assert_called_once_with('notepad.exe')
    assert any('Opening notepad' in s for s in v.spoken)


def test_close_all_prompt_then_confirm_reaches_the_real_close_all_logic(v):
    """Real route() -> CloseAllPrompt -> real capability_registry sets
    awaiting_confirmation -> real is_affirmative("yes") -> real
    tools/system/process.close_all_apps()'s own filtering logic (skip
    unnamed processes, skip VORTEX's own pid, skip anything in
    `protected`). psutil.process_iter() itself is mocked to return three
    fake processes - real process enumeration/termination is exactly the
    thing that must never run against the real machine a test happens to
    execute on, so only the boundary call is faked, not the dispatch logic
    around it."""
    class FakeProc:
        def __init__(self, pid, name):
            self.info = {'pid': pid, 'name': name}
            self.terminated = False

        def terminate(self):
            self.terminated = True

    fake_procs = [
        FakeProc(111, 'notepad.exe'),        # should be terminated
        FakeProc(v.current_pid, 'python.exe'),  # skipped: this process's own pid
        FakeProc(222, 'explorer.exe'),        # skipped: in the protected set
    ]
    with patch('vortex.tools.system.process.psutil.process_iter', return_value=fake_procs):
        v.execute('close all')
        assert v.awaiting_confirmation == {'action': 'close_all'}
        v.execute('yes')

    assert v.awaiting_confirmation is None
    assert fake_procs[0].terminated is True, 'the one non-protected fake process should be terminated'
    assert fake_procs[1].terminated is False, "VORTEX's own pid must never be terminated"
    assert fake_procs[2].terminated is False, 'a protected process must never be terminated'
    assert any('Closed 1 applications' in s for s in v.spoken)


def test_shutdown_prompt_then_confirm_reaches_the_fake_platform_adapter(v):
    v.execute('shutdown system')
    assert v.awaiting_confirmation == {'action': 'shutdown'}
    v.execute('yes')
    assert v.platform.calls == ['shutdown']


def test_shutdown_prompt_then_misheard_negative_does_not_reach_the_adapter(v):
    """The exact flagged docs/CURRENT_STATE.md §6 scenario, exercised
    end-to-end through the real pipeline this time (Steps 5/6's
    is_affirmative() unit tests already cover the function in isolation)."""
    v.execute('shutdown system')
    v.execute("no, not yes I don't want that")
    assert v.platform.calls == []
    assert v.awaiting_confirmation is None


def test_lock_reaches_the_fake_platform_adapter_with_no_confirmation_needed(v):
    v.execute('lock the computer')
    assert v.platform.calls == ['lock']


def test_delete_file_prompt_then_confirm_reaches_real_fileops(v, tmp_path, monkeypatch):
    from vortex import files as fileops
    desktop = tmp_path / 'Desktop'
    desktop.mkdir()
    target = desktop / 'a.txt'
    target.write_text('x', encoding='utf-8')
    monkeypatch.setattr(fileops, 'SEARCH_DIRS', [desktop])

    with patch('send2trash.send2trash') as mock_trash:
        v.execute('delete file a.txt')
        assert v.awaiting_confirmation['action'] == 'delete_file'
        mock_trash.assert_not_called()
        v.execute('yes')
        mock_trash.assert_called_once_with(str(target))
    assert v.awaiting_confirmation is None


# ---------- real OS process (hardware) ----------

@pytest.mark.hardware
def test_open_and_close_a_real_notepad_process_end_to_end(v):
    """No mocks at all here - a real notepad.exe is launched and terminated
    through the full execute() -> intent_router -> capability_registry ->
    tools/system/* pipeline. Needs a real Windows desktop with notepad.exe
    on PATH; skipped by default in CI (see pyproject.toml's `hardware`
    marker registration and .github/workflows/ci.yml)."""
    v.execute('open notepad')
    assert any('Opening notepad' in s for s in v.spoken)
    time.sleep(1.5)
    v.execute('close notepad')
    assert any('Closed notepad' in s for s in v.spoken)
