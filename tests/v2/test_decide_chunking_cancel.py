"""When one part of a split pick fails, the other parts are cancelled and nothing is left unretrieved."""

from __future__ import annotations

import asyncio

import pytest

from testzeus_hercules.decide.chunking import decide_in_chunks
from testzeus_hercules.decide.contract import Decision, DecisionRequest
from testzeus_hercules.decide.errors import DecisionUnavailable
from testzeus_hercules.decide.questions import MAX_TEXT_BYTES

from tests.v2.test_decide_chunking import pick


def test_a_failing_part_cancels_its_siblings_and_raises_its_own_error() -> None:
    request = pick(250)
    started: list[int] = []
    cancelled: list[int] = []
    finished: list[int] = []
    unretrieved: list[object] = []

    async def decide(part: DecisionRequest) -> Decision:
        number = len(started)
        started.append(number)
        if number == 0:
            await asyncio.sleep(0.01)
            raise DecisionUnavailable("part 0 failed")
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(number)
            raise
        finished.append(number)
        raise AssertionError("never reached")

    async def run() -> None:
        asyncio.get_running_loop().set_exception_handler(lambda _loop, context: unretrieved.append(context))
        with pytest.raises(DecisionUnavailable, match="part 0 failed"):
            await decide_in_chunks(decide, request, max_text_bytes=MAX_TEXT_BYTES, max_parallel=8)
        # Checked before asyncio.run tears the loop down (which would cancel leftovers itself).
        assert len(started) > 1, "precondition: the pick was split into parallel parts"
        assert sorted(cancelled) == started[1:], "siblings are cancelled before the error is raised"

    asyncio.run(run())

    assert finished == []
    assert unretrieved == []


def test_cancelling_the_pick_cancels_every_part() -> None:
    request = pick(250)
    cancelled: list[int] = []

    async def decide(part: DecisionRequest) -> Decision:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(1)
            raise
        raise AssertionError("never reached")

    async def run() -> None:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(decide_in_chunks(decide, request, max_text_bytes=MAX_TEXT_BYTES, max_parallel=8), timeout=0.05)
        assert len(cancelled) > 1

    asyncio.run(run())
