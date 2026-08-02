"""Browser automation (Phase 3, scoped v1).

A thin Playwright wrapper for voice-driven browsing: open a site or search
term, read back what's on the page, and click things by their visible text.
Launched lazily on first use (not at VORTEX startup) and kept alive for
reuse across commands in the same session.

This is an honestly-scoped first slice of the blueprint's browser-automation
phase - navigate/search/click/extract - not yet form-filling, uploads,
downloads, or multi-page authenticated workflows. The browser window is
visible (not headless) deliberately: this is a personal assistant meant to
be watched acting, not a silent scraper.
"""
import re

_URL_RE = re.compile(r'^https?://', re.IGNORECASE)
_LOOKS_LIKE_DOMAIN = re.compile(r'^[\w-]+(\.[\w-]+)+$')


class BrowserAgent:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None

    def _ensure_started(self):
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=False)
        self._page = self._browser.new_page()

    @staticmethod
    def _normalize_url(target):
        target = target.strip()
        if _URL_RE.match(target):
            return target
        if _LOOKS_LIKE_DOMAIN.match(target):
            return f'https://{target}'
        return None

    def open(self, target):
        """Navigate to a URL/domain, or fall back to a search if it's not one."""
        try:
            self._ensure_started()
            url = self._normalize_url(target)
            if url:
                self._page.goto(url, wait_until='domcontentloaded', timeout=15000)
                return f'Opened {target}.'
            return self.search(target)
        except Exception as e:
            return f"I couldn't open {target}: {e}"

    def search(self, query):
        """Uses DuckDuckGo's plain HTML results endpoint rather than Google: Google
        serves an automated-browser a bot-detection page ("unusual traffic")
        almost immediately, and defeating that would mean bypassing an anti-abuse
        mechanism, which is out of bounds. DuckDuckGo's html.duckduckgo.com
        endpoint is a simple server-rendered results page with no such wall."""
        try:
            self._ensure_started()
            self._page.goto(f'https://html.duckduckgo.com/html/?q={query}',
                            wait_until='domcontentloaded', timeout=15000)
            results = self._page.locator('.result__title').all_inner_texts()[:3]
            if not results:
                return f"I searched for {query} but couldn't read back any results."
            return f'Top results for {query}: ' + '; '.join(r.strip() for r in results)
        except Exception as e:
            return f"The search for {query} didn't go through: {e}"

    def play_youtube(self, query):
        """Search YouTube directly and click the first actual video result, so
        "play X on youtube" plays a video instead of the old, wrong behavior of
        opening a plain Google search of the literal phrase in the system's
        default browser (see IMPLEMENTED.md for that bug)."""
        try:
            self._ensure_started()
            self._page.goto(f'https://www.youtube.com/results?search_query={query}',
                            wait_until='domcontentloaded', timeout=15000)
            video = self._page.locator('a#video-title').first
            title = video.inner_text(timeout=8000)
            video.click(timeout=8000)
            return f'Playing {title}.'
        except Exception as e:
            return f"I couldn't find a video for {query} on YouTube: {e}"

    def read_page(self):
        if self._page is None:
            return "There's no page open yet."
        try:
            title = self._page.title()
            text = self._page.inner_text('body')[:800]
            return f'{title}. {text}'
        except Exception as e:
            return f"I couldn't read the page: {e}"

    def click_text(self, text):
        if self._page is None:
            return "There's no page open yet."
        try:
            self._page.get_by_text(text, exact=False).first.click(timeout=5000)
            return f'Clicked {text}.'
        except Exception:
            return f"I couldn't find anything matching {text} to click."

    def close(self):
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._page = self._browser = self._playwright = None
