"""Wake-ups for clients waiting on a run (long-poll status, SSE).

A waiter is taken *before* reading the store; ``notify`` sets every waiter taken
since the last notification, so a change between the read and the wait is never
missed.
"""

from __future__ import annotations

import asyncio
import contextlib


class EventHub:
    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Event] = {}

    def waiter(self, run_id: str) -> asyncio.Event:
        event = self._waiters.get(run_id)
        if event is None:
            event = self._waiters[run_id] = asyncio.Event()
        return event

    def notify(self, run_id: str) -> None:
        event = self._waiters.pop(run_id, None)
        if event is not None:
            event.set()

    @staticmethod
    async def wait(event: asyncio.Event, timeout_s: float) -> bool:
        """True if ``event`` fired within ``timeout_s``."""
        if timeout_s <= 0:
            return event.is_set()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(event.wait(), timeout_s)
        return event.is_set()
