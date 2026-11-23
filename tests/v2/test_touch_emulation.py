"""Touch is turned on once for each page of a touch device's context, and a page that closed early is no error."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from testzeus_hercules.engine.run_graph import _Touch


class FakeSession:
    def __init__(self, sent: list[tuple[str, str, dict[str, Any]]], page: str) -> None:
        self.sent, self.page = sent, page

    async def send(self, method: str, params: dict[str, Any]) -> None:
        self.sent.append((self.page, method, params))


class FakeContext:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    async def new_cdp_session(self, page: Any) -> FakeSession:
        await asyncio.sleep(0)
        if page.closed:
            raise RuntimeError("Target page, context or browser has been closed")
        return FakeSession(self.sent, page.name)


class FakePage:
    def __init__(self, name: str, closed: bool = False) -> None:
        self.name, self.closed = name, closed


def test_each_page_gets_touch_once_and_a_closed_page_is_only_logged(caplog: pytest.LogCaptureFixture) -> None:
    context = FakeContext()
    first, popup, gone = FakePage("first"), FakePage("popup"), FakePage("gone", closed=True)

    async def go() -> None:
        touch = _Touch(context)
        touch.schedule(first)  # the page event comes while new_page() runs
        await touch.ready(first)  # and new_page() returns the same page
        touch.schedule(popup)
        touch.schedule(popup)
        await touch.ready(popup)
        await touch.ready(FakePage("no event"))  # a page whose event never came is still covered
        touch.schedule(gone)
        await asyncio.gather(*touch.tasks.values())

    with caplog.at_level(logging.DEBUG, logger="testzeus_hercules.engine.run_graph"):
        asyncio.run(go())
    assert [page for page, _, _ in context.sent] == ["first", "popup", "no event"]
    assert all(method == "Emulation.setTouchEmulationEnabled" and params == {"enabled": True, "maxTouchPoints": 1} for _, method, params in context.sent)
    assert "touch emulation not set on a page (RuntimeError" in caplog.text
