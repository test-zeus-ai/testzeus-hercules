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
_DEFAULT_CHROMIUM = Path.home() / "Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"


def chromium_path() -> str | None:
    configured = os.environ.get("HERCULES_CHROMIUM")
    candidate = Path(configured) if configured else _DEFAULT_CHROMIUM
    return str(candidate) if candidate.is_file() else None


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
