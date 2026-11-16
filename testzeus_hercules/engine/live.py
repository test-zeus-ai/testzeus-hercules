"""Live view of a run: a CDP screencast of the active page, and a minimal input relay for a paused run.

The screencast (``Page.startScreencast``: JPEG, quality 60, at most 1280 px wide) runs
only while someone watches. It follows the page the run works on and any popup or new
tab it opens (back to the previous page when that one closes). Frames are throttled to
``max_fps`` (the last frame of a burst is always delivered, so the view never stays
stale) and handed to a sink; :class:`FrameWriter` writes them to the daemon's pipe
from a thread, keeping only the newest unsent frame when the reader is slow.

Frame wire format: ``FRAME_HEADER`` (JPEG length, CSS width, CSS height, capture time)
followed by the JPEG bytes.

Input (click, type, key, scroll) goes through CDP ``Input.*`` on the active page; the
caller must only relay it while the run is paused.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

FRAME_HEADER = struct.Struct(">IHHd")
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_TYPED_TEXT = 1000
_KEYS: Mapping[str, tuple[str, int]] = {
    "Enter": ("Enter", 13),
    "Tab": ("Tab", 9),
    "Backspace": ("Backspace", 8),
    "Escape": ("Escape", 27),
    "ArrowUp": ("ArrowUp", 38),
    "ArrowDown": ("ArrowDown", 40),
    "ArrowLeft": ("ArrowLeft", 37),
    "ArrowRight": ("ArrowRight", 39),
}
_MAX_SCROLL_PX = 5000

FrameSink = Callable[[bytes, int, int, float], None]


@dataclass(frozen=True)
class ScreencastSettings:
    quality: int = 60
    max_width: int = 1280
    max_height: int = 1280
    every_nth_frame: int = 2
    max_fps: float = 8.0


class InputRejected(ValueError):
    """The input event is malformed or cannot be delivered now."""


class FrameWriter:
    """Writes frames to a file descriptor from a background thread; only the newest unsent frame is kept."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._lock = threading.Condition()
        self._slot: bytes | None = None
        self._closed = False
        # The descriptor is closed by whichever ends last: close() once the writer has ended, else the writer as it ends
        # (closing it under a write in progress could hand the number to another file mid-write).
        self._writer_done = False
        self._writer_closes = False
        self.sent = 0
        self.dropped = 0
        self._thread = threading.Thread(target=self._run, name="hercules-frames", daemon=True)
        self._thread.start()

    def offer(self, jpeg: bytes, width: int, height: int, captured: float) -> None:
        if len(jpeg) > MAX_FRAME_BYTES:
            self.dropped += 1
            return
        packet = FRAME_HEADER.pack(len(jpeg), min(width, 65535), min(height, 65535), captured) + jpeg
        with self._lock:
            if self._closed:
                return
            if self._slot is not None:
                self.dropped += 1
            self._slot = packet
            self._lock.notify()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify()
        self._thread.join(timeout=2)
        with self._lock:
            if not self._writer_done:  # still in a write (a full pipe): it closes the descriptor when it ends
                self._writer_closes = True
                return
        with contextlib.suppress(OSError):
            os.close(self._fd)

    def _run(self) -> None:
        try:
            self._send()
        finally:
            with self._lock:
                self._writer_done = True
                close = self._writer_closes
            if close:
                with contextlib.suppress(OSError):
                    os.close(self._fd)

    def _send(self) -> None:
        while True:
            with self._lock:
                while self._slot is None and not self._closed:
                    self._lock.wait()
                if self._slot is None:
                    return
                packet, self._slot = self._slot, None
            try:
                view = memoryview(packet)
                while view:
                    written = os.write(self._fd, view)
                    view = view[written:]
                self.sent += 1
            except OSError:  # the daemon closed its end: stop quietly
                with self._lock:
                    self._closed = True
                return


class LiveView:
    """Screencast and input relay for one run's browser (see the module docstring)."""

    def __init__(self, sink: FrameSink, settings: ScreencastSettings | None = None) -> None:
        self.sink = sink
        self.settings = settings or ScreencastSettings()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pages: list[Any] = []
        self._session: Any = None
        self._session_page: Any = None
        self._wanted = False
        self._size = (0, 0)
        self._last_sent = 0.0
        self._held: tuple[bytes, int, int, float] | None = None
        self._flush: asyncio.TimerHandle | None = None
        self._switching: asyncio.Lock | None = None  # created on the run's loop in attach()
        self.frames = 0
        self._poller: asyncio.Future[None] | None = None

    # ------------------------------------------------------------------ wiring

    async def attach(self, page: Any) -> None:
        """Follow ``page`` (and the pages its context opens later); start streaming if someone watches."""
        self._loop = asyncio.get_running_loop()
        self._switching = asyncio.Lock()
        self._pages = [page]
        viewport = getattr(page, "viewport_size", None)
        if isinstance(viewport, Mapping):
            self._size = (int(viewport.get("width", 0)), int(viewport.get("height", 0)))
        context = getattr(page, "context", None)
        on = getattr(context, "on", None)
        if callable(on):
            on("page", lambda new_page: self._spawn(self._follow(new_page)))
        self._watch(page)
        if self._wanted:
            await self._restart()

    def request(self, enabled: bool) -> None:
        """Turn streaming on or off; safe from any thread, and before :meth:`attach`."""
        loop = self._loop
        if loop is None:
            self._wanted = enabled
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(lambda: self._spawn(self._set(enabled)))

    async def close(self) -> None:
        self._wanted = False
        await self._stop()

    @property
    def active_page(self) -> Any:
        return self._pages[-1] if self._pages else None

    def _spawn(self, work: Any) -> None:
        task = asyncio.ensure_future(work)
        task.add_done_callback(lambda done: done.cancelled() or done.exception())

    async def _set(self, enabled: bool) -> None:
        self._wanted = enabled
        await (self._restart() if enabled else self._stop())

    def _watch(self, page: Any) -> None:
        page_on = getattr(page, "on", None)
        if not callable(page_on):
            return
        page_on("close", lambda closed: self._spawn(self._closed(closed)))
        # A navigation to another site swaps the page's renderer and silently ends its screencast.
        page_on("load", lambda loaded: self._spawn(self._loaded(loaded)))

    async def _loaded(self, page: Any) -> None:
        if self._wanted and page is self.active_page:
            await self._restart()

    async def _follow(self, page: Any) -> None:
        self._pages.append(page)
        self._watch(page)
        if self._wanted:
            await self._restart()

    async def _closed(self, page: Any) -> None:
        was_active = self.active_page is page
        self._pages = [known for known in self._pages if known is not page]
        if was_active and self._wanted:
            await self._restart()

    # ------------------------------------------------------------------ screencast

    async def _restart(self) -> None:
        assert self._switching is not None
        async with self._switching:
            await self._stop_session()
            page = self.active_page
            if page is None or not self._wanted:
                return
            try:
                session = await page.context.new_cdp_session(page)
            except Exception:  # no CDP (Firefox, WebKit): screenshots stand in for the screencast
                closed = getattr(page, "is_closed", None)
                if not (callable(closed) and closed()):
                    self._session, self._session_page = _POLLING, page
                    self._poller = asyncio.ensure_future(self._poll(page))
                return
            try:
                session.on("Page.screencastFrame", lambda params: self._on_frame(session, params))
                await session.send(
                    "Page.startScreencast",
                    {
                        "format": "jpeg",
                        "quality": self.settings.quality,
                        "maxWidth": self.settings.max_width,
                        "maxHeight": self.settings.max_height,
                        "everyNthFrame": self.settings.every_nth_frame,
                    },
                )
            except Exception:  # a page that closed meanwhile: frames follow on the next page
                return
            self._session, self._session_page = session, page
            await self._first_frame(session, page)

    async def _first_frame(self, session: Any, page: Any) -> None:
        """A still page sends no screencast frames: show what it looks like right away."""
        delivered = self.frames
        if _blank(page):
            return
        try:
            shot = await session.send("Page.captureScreenshot", {"format": "jpeg", "quality": self.settings.quality})
            jpeg = base64.b64decode(shot["data"])
        except Exception:  # the page went away; frames will follow on the next page
            return
        viewport = getattr(page, "viewport_size", None) or {}
        width, height = int(viewport.get("width") or self._size[0]), int(viewport.get("height") or self._size[1])
        if width and height:
            self._size = (width, height)
        if session is self._session and self.frames == delivered:  # unless the screencast was quicker
            self._deliver((jpeg, width, height, time.time()))

    async def _stop(self) -> None:
        if self._switching is None:
            return
        async with self._switching:
            await self._stop_session()

    async def _poll(self, page: Any) -> None:
        """Screenshots of ``page`` at the frame rate, while it is the watched page (browsers without a screencast)."""
        interval = max(1.0 / self.settings.max_fps if self.settings.max_fps > 0 else 0.0, _POLL_MIN_S)
        while self._session is _POLLING and self._session_page is page:
            if not _blank(page):
                try:
                    jpeg = await page.screenshot(type="jpeg", quality=self.settings.quality)
                except Exception:  # navigating, or closed: try again on the next tick
                    jpeg = b""
                if jpeg and self._session is _POLLING and self._session_page is page:
                    viewport = getattr(page, "viewport_size", None) or {}
                    width, height = int(viewport.get("width") or self._size[0]), int(viewport.get("height") or self._size[1])
                    if width and height:
                        self._size = (width, height)
                    self._deliver((jpeg, width, height, time.time()))
            await asyncio.sleep(interval)

    async def _stop_session(self) -> None:
        session, self._session, self._session_page = self._session, None, None
        if self._flush is not None:
            self._flush.cancel()
            self._flush = None
        poller, self._poller = self._poller, None
        if poller is not None:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await poller
        if session is None or session is _POLLING:
            return
        with contextlib.suppress(Exception):
            await session.send("Page.stopScreencast")
        with contextlib.suppress(Exception):
            await session.detach()

    def _on_frame(self, session: Any, params: Mapping[str, Any]) -> None:
        self._spawn(_ack(session, params.get("sessionId")))
        if session is not self._session or _blank(self._session_page):
            return  # an old session, or the empty page before the run opened anything
        try:
            jpeg = base64.b64decode(str(params.get("data", "")))
        except ValueError:
            return
        metadata = params.get("metadata") or {}
        width = int(metadata.get("deviceWidth") or self._size[0] or 0)
        height = int(metadata.get("deviceHeight") or self._size[1] or 0)
        if width and height:
            self._size = (width, height)
        captured = float(metadata.get("timestamp") or time.time())
        self._throttle((jpeg, width, height, captured))

    def _throttle(self, frame: tuple[bytes, int, int, float]) -> None:
        interval = 1.0 / self.settings.max_fps if self.settings.max_fps > 0 else 0.0
        now = time.monotonic()
        wait = self._last_sent + interval - now
        if wait <= 0:
            self._deliver(frame)
            return
        self._held = frame  # newest frame of a burst; delivered when the interval is over
        if self._flush is None and self._loop is not None:
            self._flush = self._loop.call_later(wait, self._flush_held)

    def _flush_held(self) -> None:
        self._flush = None
        held, self._held = self._held, None
        if held is not None and self._session is not None:
            self._deliver(held)

    def _deliver(self, frame: tuple[bytes, int, int, float]) -> None:
        self._last_sent = time.monotonic()
        self._held = None
        self.frames += 1
        with contextlib.suppress(Exception):
            self.sink(*frame)

    # ------------------------------------------------------------------ input

    async def dispatch(self, event: Mapping[str, Any]) -> str:
        """Deliver one input event to the active page; returns what was done or raises :class:`InputRejected`."""
        page = self.active_page
        if page is None:
            raise InputRejected("the browser is not open yet")
        kind = str(event.get("kind", ""))
        try:
            session = await page.context.new_cdp_session(page)
        except Exception:  # no CDP (Firefox, WebKit): Playwright's own input
            return await self._dispatch_playwright(page, kind, event)
        try:
            if kind == "click":
                x, y = self._point(event)
                for phase in ("mouseMoved", "mousePressed", "mouseReleased"):
                    await session.send("Input.dispatchMouseEvent", {"type": phase, "x": x, "y": y, "button": "left" if phase != "mouseMoved" else "none", "clickCount": 1})
                return f"clicked at ({x:.0f}, {y:.0f})"
            if kind == "type":
                text = str(event.get("text", ""))
                if not text or len(text) > MAX_TYPED_TEXT:
                    raise InputRejected(f"text must be 1 to {MAX_TYPED_TEXT} characters")
                await session.send("Input.insertText", {"text": text})
                return f"typed {len(text)} characters"
            if kind == "key":
                key = str(event.get("key", ""))
                if key not in _KEYS:
                    raise InputRejected(f"key must be one of {', '.join(_KEYS)}")
                name, code = _KEYS[key]
                for phase in ("rawKeyDown", "keyUp"):
                    await session.send("Input.dispatchKeyEvent", {"type": phase, "key": name, "code": name, "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code})
                if key == "Enter":
                    await session.send("Input.dispatchKeyEvent", {"type": "char", "text": "\r", "key": "Enter"})
                return f"pressed {key}"
            if kind == "scroll":
                x, y = self._point(event)
                delta = max(-_MAX_SCROLL_PX, min(_MAX_SCROLL_PX, int(event.get("dy", 0))))
                await session.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": delta})
                return f"scrolled {delta} px"
            raise InputRejected("kind must be click, type, key or scroll")
        finally:
            with contextlib.suppress(Exception):
                await session.detach()

    async def _dispatch_playwright(self, page: Any, kind: str, event: Mapping[str, Any]) -> str:
        if kind == "click":
            x, y = self._point(event)
            await page.mouse.click(x, y)
            return f"clicked at ({x:.0f}, {y:.0f})"
        if kind == "type":
            text = str(event.get("text", ""))
            if not text or len(text) > MAX_TYPED_TEXT:
                raise InputRejected(f"text must be 1 to {MAX_TYPED_TEXT} characters")
            await page.keyboard.insert_text(text)
            return f"typed {len(text)} characters"
        if kind == "key":
            key = str(event.get("key", ""))
            if key not in _KEYS:
                raise InputRejected(f"key must be one of {', '.join(_KEYS)}")
            await page.keyboard.press(_KEYS[key][0])
            return f"pressed {key}"
        if kind == "scroll":
            x, y = self._point(event)
            delta = max(-_MAX_SCROLL_PX, min(_MAX_SCROLL_PX, int(event.get("dy", 0))))
            await page.mouse.move(x, y)
            await page.mouse.wheel(0, delta)
            return f"scrolled {delta} px"
        raise InputRejected("kind must be click, type, key or scroll")

    def _point(self, event: Mapping[str, Any]) -> tuple[float, float]:
        try:
            x, y = float(event.get("x", -1)), float(event.get("y", -1))
        except (TypeError, ValueError):
            raise InputRejected("x and y must be numbers between 0 and 1") from None
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise InputRejected("x and y must be numbers between 0 and 1")
        width, height = self._size
        if not width or not height:
            viewport = getattr(self.active_page, "viewport_size", None) or {}
            width, height = int(viewport.get("width", 0)), int(viewport.get("height", 0))
        if not width or not height:
            raise InputRejected("the page size is not known yet")
        return x * width, y * height


_POLLING = object()  # the "session" of a live view that takes screenshots instead of a screencast
_POLL_MIN_S = 0.25


def _blank(page: Any) -> bool:
    return str(getattr(page, "url", "") or "") in ("", "about:blank")


async def _ack(session: Any, session_id: Any) -> None:
    if session_id is None:
        return
    with contextlib.suppress(Exception):
        await session.send("Page.screencastFrameAck", {"sessionId": session_id})
