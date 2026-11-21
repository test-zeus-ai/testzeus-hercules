"""Run-level safety: junit.xml stays parseable, a cancelled awaiter stops its work, the frame writer never closes its
descriptor under a write, and a run whose browser cannot start does not wait for its plan."""

from __future__ import annotations

import asyncio
import os
import threading
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.cases.models import RunResult, StepResult
from testzeus_hercules.engine import run_graph
from testzeus_hercules.engine.junit import write_junit
from testzeus_hercules.engine.live import FrameWriter
from testzeus_hercules.kernel.run_context import CancelToken


def test_junit_drops_characters_xml_cannot_hold(tmp_path: Path) -> None:
    step = StepResult(index=1, action="type \x1b[31mred\x00", status="failed", actual="page said \x07beep\x0b and ￾", expected="ok\x01")
    result = RunResult(run_id="r_1", status="failed", case_id="c", steps=[step], hints=["hint \x02"])

    path = write_junit(result, tmp_path)

    root = ElementTree.parse(path).getroot()  # parses: no control characters left
    case = root.find("testcase")
    assert case is not None and "red" in case.get("name", "")
    failure = case.find("failure")
    assert failure is not None and "beep" in (failure.text or "")


def test_cancellable_stops_its_work_when_the_awaiter_is_cancelled() -> None:
    async def go() -> bool:
        started, stopped = asyncio.Event(), asyncio.Event()

        async def work() -> None:
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()

        waiter = asyncio.ensure_future(run_graph._cancellable(work(), CancelToken()))
        await started.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        return stopped.is_set()

    assert asyncio.run(go())


def test_cancellable_returns_none_and_stops_work_on_a_cancelled_run() -> None:
    async def go() -> tuple[Any, bool]:
        token = CancelToken()
        stopped = asyncio.Event()

        async def work() -> str:
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()
            return "done"

        asyncio.get_running_loop().call_later(0.05, token.cancel)
        return await run_graph._cancellable(work(), token), stopped.is_set()

    assert asyncio.run(go()) == (None, True)


def test_frame_writer_leaves_closing_to_a_writer_still_in_a_write(monkeypatch: pytest.MonkeyPatch) -> None:
    read_end, write_end = os.pipe()
    release = threading.Event()
    closed: list[int] = []
    real_write, real_close = os.write, os.close

    def slow_write(fd: int, data: Any) -> int:
        if fd == write_end:
            release.wait(5)
        return real_write(fd, data)

    def tracked_close(fd: int) -> None:
        if fd == write_end:
            closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "write", slow_write)
    monkeypatch.setattr(os, "close", tracked_close)
    writer = FrameWriter(write_end)
    writer.offer(b"\xff\xd8x", 1, 1, 0.0)
    time.sleep(0.05)
    writer._thread.join = lambda timeout=None: None  # type: ignore[method-assign]  # close() gives up waiting at once
    writer.close()
    assert closed == [], "the descriptor stays open while a write is in progress"
    release.set()
    deadline = time.monotonic() + 5
    while not closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert closed == [write_end], "the writer closes it as it ends"
    real_close(read_end)


def test_a_run_whose_browser_cannot_start_does_not_wait_for_its_plan(tmp_path: Path) -> None:
    from tests.v2.engine_fixtures.fakes import ScriptedInterpreter
    from tests.v2.test_engine_run_graph import READINGS, FakeLauncher, Harness, login_case

    class SlowReader(ScriptedInterpreter):
        async def read_case(self, *args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(30)
            return await super().read_case(*args, **kwargs)

    h = Harness(tmp_path, launcher=FakeLauncher(error=RuntimeError("Executable doesn't exist at /nowhere")), interpreter=SlowReader(READINGS))
    started = time.monotonic()

    result = h.run(login_case())

    assert result.status == "error"
    assert time.monotonic() - started < 10
