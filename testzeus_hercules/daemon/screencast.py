"""Live screencast relay: frames from a worker's pipe to the WebSocket viewers of its run.

Each viewer holds only the newest frame it has not sent yet, so a slow client skips
frames instead of falling behind (``dropped`` counts them). The hub remembers each
run's latest frame for viewers that join later, tells the manager when a run gains its
first viewer or loses its last (the worker streams only while someone watches), and
ends every viewer when the run finishes.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

from testzeus_hercules.engine.live import FRAME_HEADER, MAX_FRAME_BYTES


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    width: int
    height: int
    captured: float
    seq: int

    def packet(self) -> bytes:
        """The frame as one binary WebSocket message: the header, then the JPEG."""
        return FRAME_HEADER.pack(len(self.jpeg), self.width, self.height, self.captured) + self.jpeg


@dataclass(eq=False)
class Viewer:
    run_id: str
    _frame: Frame | None = None
    _ended: str | None = None
    _wake: asyncio.Event = field(default_factory=asyncio.Event)
    sent: int = 0
    dropped: int = 0

    def offer(self, frame: Frame) -> None:
        if self._frame is not None:
            self.dropped += 1
        self._frame = frame
        self._wake.set()

    def end(self, status: str) -> None:
        self._ended = status
        self._wake.set()

    async def next(self, timeout: float) -> Frame | str | None:
        """The newest unsent frame, the run's final status once it ended, or None after ``timeout``."""
        if self._frame is None and self._ended is None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout)
        self._wake.clear()
        if self._frame is not None:
            frame, self._frame = self._frame, None
            self.sent += 1
            return frame
        return self._ended


# Finished runs whose final status is remembered (for a viewer that joins just after the end); older ones are forgotten:
# a viewer of a long-finished run is told its status by the app from the run store instead.
MAX_ENDED = 256


class ScreencastHub:
    def __init__(self, on_viewers: Callable[[str, int], None] | None = None) -> None:
        self._viewers: dict[str, set[Viewer]] = {}
        self._latest: dict[str, Frame] = {}
        self._ended: OrderedDict[str, str] = OrderedDict()
        self._counts: dict[str, int] = {}
        self.on_viewers = on_viewers

    def publish(self, run_id: str, jpeg: bytes, width: int, height: int, captured: float) -> None:
        if run_id in self._ended:
            return
        seq = self._counts.get(run_id, 0) + 1
        self._counts[run_id] = seq
        frame = Frame(jpeg=jpeg, width=width, height=height, captured=captured, seq=seq)
        self._latest[run_id] = frame
        for viewer in self._viewers.get(run_id, ()):
            viewer.offer(frame)

    def subscribe(self, run_id: str) -> Viewer:
        viewer = Viewer(run_id)
        if run_id in self._ended:
            viewer.end(self._ended[run_id])
        elif run_id in self._latest:
            viewer.offer(self._latest[run_id])
        viewers = self._viewers.setdefault(run_id, set())
        viewers.add(viewer)
        if self.on_viewers is not None and run_id not in self._ended:
            self.on_viewers(run_id, len(viewers))
        return viewer

    def unsubscribe(self, viewer: Viewer) -> None:
        viewers = self._viewers.get(viewer.run_id)
        if viewers is None or viewer not in viewers:
            return
        viewers.discard(viewer)
        if not viewers:
            self._viewers.pop(viewer.run_id, None)
        if self.on_viewers is not None and viewer.run_id not in self._ended:
            self.on_viewers(viewer.run_id, len(viewers))

    def viewers(self, run_id: str) -> int:
        return len(self._viewers.get(run_id, ()))

    def frames(self, run_id: str) -> int:
        return self._counts.get(run_id, 0)

    def end(self, run_id: str, status: str) -> None:
        """The run finished: every viewer gets the final status; the latest frame and its frame count are dropped."""
        self._ended[run_id] = status
        self._ended.move_to_end(run_id)
        while len(self._ended) > MAX_ENDED:
            self._ended.popitem(last=False)
        self._latest.pop(run_id, None)
        self._counts.pop(run_id, None)
        for viewer in self._viewers.get(run_id, ()):
            viewer.end(status)

    def forget(self, run_id: str) -> None:
        self._ended.pop(run_id, None)
        self._counts.pop(run_id, None)


async def read_frames(reader: asyncio.StreamReader, publish: Callable[[bytes, int, int, float], None]) -> int:
    """Read framed JPEGs from a worker's pipe until it closes; returns how many were read."""
    count = 0
    while True:
        try:
            header = await reader.readexactly(FRAME_HEADER.size)
            length, width, height, captured = FRAME_HEADER.unpack(header)
            if length > MAX_FRAME_BYTES:
                return count  # a broken stream: stop reading rather than allocate
            jpeg = await reader.readexactly(length)
        except (asyncio.IncompleteReadError, ConnectionError):
            return count
        count += 1
        publish(jpeg, width, height, captured)
