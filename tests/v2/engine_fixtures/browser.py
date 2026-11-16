"""Chromium for the engine tests: located via ``HERCULES_CHROMIUM`` or the local Playwright cache.

Tests skip with a clear reason when no browser is available, except in CI
(``CI`` set), where a missing browser is a failure.
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import os
import socket
import threading
from pathlib import Path
from typing import AsyncIterator, Iterator

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent
# Where Playwright's Chromium download keeps its executable, per platform (newest build first when several exist).
_EXECUTABLES = (
    "chrome-linux64/chrome",
    "chrome-linux-arm64/chrome",
    "chrome-linux/chrome",
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    "chrome-win64/chrome.exe",
    "chrome-win/chrome.exe",
)


@functools.lru_cache(maxsize=1)
def _playwright_chromium() -> Path | None:
    """The newest Chromium in Playwright's browser folder (tests/v2/conftest.py pins it before HOME can move)."""
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not root:
        return None
    builds = sorted(Path(root).glob("chromium-[0-9]*"), key=lambda path: int(path.name.split("-")[1]), reverse=True)
    for build in builds:
        for relative in _EXECUTABLES:
            if (build / relative).is_file():
                return build / relative
    return None


def chromium_path() -> str | None:
    configured = os.environ.get("HERCULES_CHROMIUM")
    candidate = Path(configured) if configured else _playwright_chromium()
    return str(candidate) if candidate is not None and candidate.is_file() else None


def require_chromium() -> str:
    path = chromium_path()
    if path is None:
        reason = "Chromium not found: set HERCULES_CHROMIUM to a Chromium executable"
        if os.environ.get("CI"):
            pytest.fail(reason)
        pytest.skip(reason)
    return path


@contextlib.asynccontextmanager
async def open_page(html: str | None = None, *, url: str | None = None) -> AsyncIterator[object]:
    """A fresh headless page with ``html`` loaded (or ``url`` opened)."""
    from playwright.async_api import async_playwright

    executable = require_chromium()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable, headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1024, "height": 700})
            if html is not None:
                await page.set_content(html)
            elif url is not None:
                await page.goto(url)
            yield page
        finally:
            await browser.close()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - signature from the base class
        return None


@contextlib.contextmanager
def serve_fixtures(directory: Path = FIXTURES_DIR) -> Iterator[str]:
    """Serve ``directory`` over HTTP on a free localhost port; yields the base URL."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
