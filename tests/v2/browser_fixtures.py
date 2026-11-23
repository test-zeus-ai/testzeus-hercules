"""Shared real-browser fixture for v2 tests.

Import the fixture into a test module with::

    from tests.v2.browser_fixtures import browser  # noqa: F401

Tests stay synchronous and drive async Playwright code through ``browser.run(...)``,
so no async pytest plugin is needed. The browser is headless Chromium. Set
``HERCULES_TEST_CHROMIUM`` to a Chromium/Chrome executable to override the default.
Without a launchable browser the tests are skipped locally and fail in CI (``CI`` set).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TypeVar

import pytest

FIXTURE_PAGES = Path(__file__).resolve().parent / "fixtures" / "pages"
DEFAULT_VIEWPORT = (1280, 800)

T = TypeVar("T")


def chromium_executable() -> str | None:
    """The executable to launch, or None to let Playwright use its bundled Chromium."""
    configured = os.environ.get("HERCULES_TEST_CHROMIUM")
    if configured:
        return configured
    from tests.v2.engine_fixtures.browser import chromium_path  # Playwright's own download, on any platform

    return chromium_path()


def fixture_url(name: str) -> str:
    """``file://`` URL of a fixture page, e.g. ``fixture_url("login.html")``."""
    path = FIXTURE_PAGES / name
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.as_uri()


class BrowserHarness:
    """One event loop and one headless browser shared by a test session."""

    def __init__(self, loop: asyncio.AbstractEventLoop, playwright: object, browser: object) -> None:
        self.loop = loop
        self.playwright = playwright
        self.browser = browser

    def run(self, awaitable: Awaitable[T]) -> T:
        """Run a coroutine on the harness loop and return its result."""
        return self.loop.run_until_complete(awaitable)

    @asynccontextmanager
    async def page(self, name: str | None = None, *, viewport: tuple[int, int] = DEFAULT_VIEWPORT) -> AsyncIterator[object]:
        """A fresh page in its own context, optionally opened on a fixture page."""
        context = await self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})  # type: ignore[attr-defined]
        try:
            page = await context.new_page()
            if name is not None:
                await page.goto(fixture_url(name))
                await page.wait_for_load_state("load")
            yield page
        finally:
            await context.close()


def _unavailable(reason: str) -> None:
    if os.environ.get("CI"):
        pytest.fail(f"browser tests need Chromium in CI: {reason}")
    pytest.skip(f"no launchable Chromium for browser tests: {reason}")


@pytest.fixture(scope="session")
def browser() -> Iterator[BrowserHarness]:
    """Session-wide headless Chromium; see the module docstring."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - playwright is a core dependency
        _unavailable(f"playwright not importable ({exc})")
    loop = asyncio.new_event_loop()
    playwright = None
    try:
        playwright = loop.run_until_complete(async_playwright().start())
        launched = loop.run_until_complete(playwright.chromium.launch(executable_path=chromium_executable(), headless=True))
    except Exception as exc:  # noqa: BLE001 - any launch failure means "no browser here"
        if playwright is not None:
            loop.run_until_complete(playwright.stop())
        loop.close()
        _unavailable(f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}")
    harness = BrowserHarness(loop, playwright, launched)
    try:
        yield harness
    finally:
        loop.run_until_complete(launched.close())
        loop.run_until_complete(playwright.stop())
        loop.close()
