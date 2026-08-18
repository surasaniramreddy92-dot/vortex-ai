"""docs/REFACTOR_PLAN.md Step 5: what VORTEX does when asked to close a
named app or close everything - ported verbatim from main.py's
close_named_app/close_all_apps. The protected-process set is injected, not
imported directly, so this module doesn't know or care that
platform/windows/protected_processes.py is the Windows-specific source of
it."""
import psutil


def close_named_app(target, native_apps, audit):
    """Returns the message the caller should speak."""
    exe = native_apps.get(target.lower())
    if not exe:
        audit.record('close_app', target, 'failed', reason='unknown_app')
        return f"I don't know how to close {target} yet."
    closed = False
    for p in psutil.process_iter(['name']):
        try:
            if (p.info['name'] or '').lower() == exe.lower():
                p.terminate()
                closed = True
        except psutil.Error:
            pass
    if closed:
        audit.record('close_app', target, 'executed')
    else:
        audit.record('close_app', target, 'failed', reason='not_running')
    return f'Closed {target}.' if closed else f'{target} was not running.'


def close_all_apps(current_pid, protected_processes, audit):
    """Returns the message the caller should speak."""
    count = 0
    for p in psutil.process_iter(['pid', 'name']):
        try:
            name = (p.info['name'] or '').lower()
            pid = p.info['pid']
            if not name or pid == current_pid or name in protected_processes:
                continue
            p.terminate()
            count += 1
        except psutil.Error:
            pass
    audit.record('close_all', 'all_non_protected_processes', 'executed', count=count)
    return f'Closed {count} applications.'
