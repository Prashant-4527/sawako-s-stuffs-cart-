"""
kaoruko/execution/handlers/browser_control.py
Browser automation: open URLs, search, tab control via Playwright + OS fallback.
"""
from __future__ import annotations
import asyncio, subprocess, webbrowser
from urllib.parse import quote_plus
from typing import Optional, TYPE_CHECKING
from kaoruko.infrastructure.logging.logger import get_logger
if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig

log = get_logger("execution.browser_control")


_SEARCH_ENGINES = {
    "google":    "https://www.google.com/search?q={}",
    "youtube":   "https://www.youtube.com/results?search_query={}",
    "bing":      "https://www.bing.com/search?q={}",
    "wikipedia": "https://en.wikipedia.org/wiki/Special:Search?search={}",
}


class BrowserControlHandler:
    def __init__(self, config: "KaorukoConfig") -> None:
        self.config = config

    def open_url(self, url: str, **kwargs) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            webbrowser.open(url)
            log.info("url_opened", url=url[:80])
            return f"Opening {url[:50]}~"
        except Exception as e:
            log.error("url_open_error", error=str(e))
            return "I had trouble opening that URL~"

    def search(
        self,
        query: str,
        engine: str = "google",
        **kwargs,
    ) -> str:
        engine_url = _SEARCH_ENGINES.get(engine.lower(), _SEARCH_ENGINES["google"])
        url = engine_url.format(quote_plus(query))
        webbrowser.open(url)
        log.info("browser_search", engine=engine, query=query[:60])
        return f"Searching for '{query}' on {engine.title()}~"

    def search_youtube(self, query: str, **kwargs) -> str:
        return self.search(query, engine="youtube")

    def open_new_tab(self, **kwargs) -> str:
        # Open empty new tab
        webbrowser.open("about:newtab")
        return "Opened a new tab~"
