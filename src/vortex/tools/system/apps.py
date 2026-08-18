"""docs/REFACTOR_PLAN.md Step 5: what VORTEX does when asked to open a named
app - ported verbatim from main.py's open_target. The name -> executable/URL
tables are injected, not imported directly, so this module doesn't know or
care that platform/windows/apps.py is the Windows-specific source of them."""
import subprocess


def open_target(target, native_apps, web_apps, browser):
    """Native apps launch locally. Anything web-related routes through the
    one Playwright-controlled browser instead of the system's default
    browser, so there's a single consistent, automatable browser session
    rather than two different ones depending which path fires - and so an
    unmatched multi-word phrase gets an actual web search with results read
    back, not a silent literal-phrase Google search window.

    Returns the message the caller should speak."""
    target = target.lower().strip()
    if target in native_apps:
        try:
            subprocess.Popen(native_apps[target])
            return f'Opening {target}.'
        except OSError:
            pass
    if target in web_apps:
        browser.open(web_apps[target])
        return f'Opening {target}.'
    return browser.open(target)
