"""LiveView in a real browser: CDP screencast frames, following popups, throttling, input relay; FrameWriter framing."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import pytest

from testzeus_hercules.engine.live import FRAME_HEADER, FrameWriter, InputRejected, LiveView, ScreencastSettings

from tests.v2.browser_fixtures import BrowserHarness, browser  # noqa: F401

INPUT_PAGE = (
    "data:text/html," + "<title>t</title><body style='margin:0'>"
    "<button id=b style='position:absolute;left:0;top:0;width:400px;height:200px' onclick=\"document.title='clicked'\">Go</button>"
    "<input id=i style='position:absolute;left:0;top:300px'></body>"
)


class Frames:
    def __init__(self) -> None:
        self.items: list[tuple[int, int, int, float]] = []
        self.at: list[float] = []

    def __call__(self, jpeg: bytes, width: int, height: int, captured: float) -> None:
        assert jpeg[:2] == b"\xff\xd8"
        self.items.append((len(jpeg), width, height, captured))
        self.at.append(time.monotonic())


async def until(predicate: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


def test_screencast_streams_only_while_requested_and_respects_the_fps_cap(browser: BrowserHarness) -> None:  # noqa: F811
    frames = Frames()
    live = LiveView(frames, ScreencastSettings(max_fps=5.0))

    async def scenario() -> None:
        async with browser.page() as page:
            await page.goto("data:text/html,<body><div id=c>0</div><script>let n=0;setInterval(()=>{document.getElementById('c').textContent=++n},16)</script></body>")
            await live.attach(page)
            await asyncio.sleep(0.3)
            assert frames.items == []  # nobody watches: no screencast
            live.request(True)
            await until(lambda: len(frames.items) >= 8)
            live.request(False)
            await asyncio.sleep(0.3)
            count = len(frames.items)
            await asyncio.sleep(0.5)
            assert len(frames.items) == count  # stopped

    browser.run(scenario())
    _, width, height, _ = frames.items[0]
    assert (width, height) == (1280, 800)
    gaps = [later - earlier for earlier, later in zip(frames.at, frames.at[1:8])]
    assert min(gaps) >= 0.15  # at most ~5 fps (0.2 s apart, with timer slack)


def test_screencast_keeps_streaming_after_navigating_to_another_site(browser: BrowserHarness) -> None:  # noqa: F811
    frames = Frames()
    live = LiveView(frames)

    async def scenario() -> None:
        async with browser.page() as page:
            await live.attach(page)
            live.request(True)  # watching from the blank page, before the run navigates
            await asyncio.sleep(0.5)
            assert frames.items == []  # nothing to show on the empty start page
            await page.goto("data:text/html,<body style='background:#06c'><h1>Next site</h1></body>")
            before = len(frames.items)
            await until(lambda: len(frames.items) > before)
            assert frames.items[-1][1:3] == (1280, 800)
            await live.close()

    browser.run(scenario())


def test_screencast_follows_a_popup_and_returns_when_it_closes(browser: BrowserHarness) -> None:  # noqa: F811
    frames = Frames()
    live = LiveView(frames)

    async def scenario() -> None:
        async with browser.page() as page:
            await page.goto("data:text/html,<body>main</body>")
            live.request(True)
            await live.attach(page)
            await until(lambda: len(frames.items) >= 1)
            async with page.context.expect_page() as opened:
                await page.evaluate("window.open('about:blank')")
            popup = await opened.value
            await until(lambda: live.active_page is popup)
            before = len(frames.items)
            await popup.goto("data:text/html,<body style='background:#c00'>popup</body>")
            await until(lambda: len(frames.items) > before)
            await popup.close()
            await until(lambda: live.active_page is page)
            await live.close()

    browser.run(scenario())


def test_input_relay_clicks_types_and_presses_keys(browser: BrowserHarness) -> None:  # noqa: F811
    live = LiveView(lambda *frame: None)

    async def scenario() -> None:
        async with browser.page() as page:
            await page.goto(INPUT_PAGE)
            await live.attach(page)
            assert "clicked" in await live.dispatch({"kind": "click", "x": 100 / 1280, "y": 100 / 800})
            assert await page.title() == "clicked"
            await page.focus("#i")
            await live.dispatch({"kind": "type", "text": "hello"})
            await live.dispatch({"kind": "key", "key": "Backspace"})
            assert await page.input_value("#i") == "hell"
            with pytest.raises(InputRejected):
                await live.dispatch({"kind": "click", "x": 2, "y": 0.5})
            with pytest.raises(InputRejected):
                await live.dispatch({"kind": "key", "key": "F12"})
            with pytest.raises(InputRejected):
                await live.dispatch({"kind": "drag"})

    browser.run(scenario())


def test_frame_writer_frames_and_keeps_only_the_newest_unsent_frame() -> None:
    read_fd, write_fd = os.pipe()
    writer = FrameWriter(write_fd)
    jpeg = b"\xff\xd8" + b"x" * 200_000  # larger than a pipe buffer: the writer blocks, frames queue up
    for number in range(5):
        writer.offer(jpeg + bytes([number]), 1280, 800, float(number))
    received = []
    with os.fdopen(read_fd, "rb") as reader:
        while True:
            header = reader.read(FRAME_HEADER.size)
            length, width, height, captured = FRAME_HEADER.unpack(header)
            body = reader.read(length)
            assert len(body) == length and (width, height) == (1280, 800)
            received.append(captured)
            if captured == 4.0:
                break
        writer.close()
    assert received[-1] == 4.0 and len(received) < 5
    assert writer.dropped >= 1 and writer.dropped + len(received) == 5
