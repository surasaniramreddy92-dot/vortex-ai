"""docs/REFACTOR_PLAN.md Step 5: processes close_all_apps must never
terminate. Moved out of main.py's inline set literal into its own
reviewable file, and expanded per the gap flagged in
docs/CURRENT_STATE.md §6: the original 11-entry list covered VORTEX's own
process, its LLM engine, and the most obvious shell/session processes, but
missed several others whose termination can crash or destabilize Windows
outright. Still a denylist of known-critical processes (not an inverted
allowlist-of-safe-to-close) - CURRENT_STATE.md offered that as an
alternative design, but inverting the logic would make close_all_apps
refuse to close any application it doesn't already recognize by name,
which is a much bigger behavior change than this step's "confirm the known
gap, keep the existing shape" scope."""

PROTECTED_PROCESSES = {
    # Original 11
    'python.exe', 'pythonw.exe', 'ollama.exe', 'explorer.exe', 'winlogon.exe', 'csrss.exe',
    'services.exe', 'lsass.exe', 'dwm.exe', 'system', 'taskhostw.exe', 'shellhost.exe',
    # Added - core OS processes named in docs/CURRENT_STATE.md §6 whose
    # termination can crash or destabilize Windows.
    'svchost.exe', 'wininit.exe', 'smss.exe', 'spoolsv.exe', 'registry', 'fontdrvhost.exe',
    # Windows' own built-in antivirus - present on every modern Windows
    # install even when a third-party AV is layered on top of it. Third-party
    # AV/EDR process names aren't included: they vary per vendor and can't be
    # hardcoded reliably the way a built-in OS component can.
    'msmpeng.exe',
}
