"""The run's browser process record: a supervisor stops only the browser the dead worker started."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

import pytest

from testzeus_hercules.engine.browser_process import BROWSER_FILE, BrowserProcess, describe_process, forget_browser, record_browser, stop_browser

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups and ps are POSIX")


@pytest.fixture
def fake_browser(tmp_path: Path) -> Iterator[subprocess.Popen[bytes]]:
    """A long-running process that leads its own group and names a user-data directory, like Playwright's Chromium."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", f"--user-data-dir={tmp_path / 'profile'}"], start_new_session=True)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)


def _write(run_dir: Path, process: BrowserProcess) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / BROWSER_FILE).write_text(json.dumps(process.__dict__), encoding="utf-8")


def _wait_exit(proc: subprocess.Popen[bytes], timeout: float = 10.0) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.05)
    return proc.poll()


def test_the_recorded_browser_is_described_by_its_group_user_data_dir_and_start_time(fake_browser: subprocess.Popen[bytes], tmp_path: Path) -> None:
    process = describe_process(fake_browser.pid)

    assert process is not None
    assert process.pgid == fake_browser.pid
    assert process.user_data_dir == str(tmp_path / "profile")
    assert process.started


def test_a_dead_workers_browser_is_stopped_with_its_process_group(fake_browser: subprocess.Popen[bytes], tmp_path: Path) -> None:
    process = describe_process(fake_browser.pid)
    assert process is not None
    _write(tmp_path / "run", process)

    assert stop_browser(tmp_path / "run") is True

    assert _wait_exit(fake_browser) == -signal.SIGKILL
    assert not (tmp_path / "run" / BROWSER_FILE).exists()


@pytest.mark.parametrize("change", [{"user_data_dir": "/elsewhere/profile"}, {"started": "Thu Jan  1 00:00:00 1970"}, {"user_data_dir": None, "started": None}])
def test_a_process_that_is_not_the_recorded_browser_is_never_signalled(fake_browser: subprocess.Popen[bytes], tmp_path: Path, change: dict[str, Any]) -> None:
    process = describe_process(fake_browser.pid)
    assert process is not None
    _write(tmp_path / "run", replace(process, **change))

    assert stop_browser(tmp_path / "run") is False

    assert fake_browser.poll() is None, "a recycled pid, or one we cannot verify, is left alone"
    assert not (tmp_path / "run" / BROWSER_FILE).exists()


def test_nothing_happens_without_a_record_or_after_the_browser_closed(tmp_path: Path) -> None:
    assert stop_browser(tmp_path) is False
    (tmp_path / BROWSER_FILE).write_text("{not json", encoding="utf-8")
    assert stop_browser(tmp_path) is False
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(timeout=10)
    _write(tmp_path, BrowserProcess(pid=gone.pid, pgid=gone.pid, user_data_dir="/x", started="now"))
    assert stop_browser(tmp_path) is False
    forget_browser(tmp_path)  # removing a missing record is fine


def test_the_worker_records_the_pid_the_browser_reports_over_cdp(fake_browser: subprocess.Popen[bytes], tmp_path: Path) -> None:
    class Session:
        async def send(self, method: str) -> dict[str, Any]:
            assert method == "SystemInfo.getProcessInfo"
            return {"processInfo": [{"type": "renderer", "id": 1}, {"type": "browser", "id": fake_browser.pid}]}

        async def detach(self) -> None:
            return None

    class Browser:
        async def new_browser_cdp_session(self) -> Session:
            return Session()

    class Page:
        context = type("Context", (), {"browser": Browser()})()

    recorded = asyncio.run(record_browser(Page(), tmp_path / "run"))

    assert recorded is not None and recorded.pid == fake_browser.pid
    saved = json.loads((tmp_path / "run" / BROWSER_FILE).read_text())
    assert saved["pid"] == fake_browser.pid and saved["user_data_dir"] == str(tmp_path / "profile")


def test_a_page_without_a_browser_records_nothing(tmp_path: Path) -> None:
    assert asyncio.run(record_browser(object(), tmp_path)) is None
    assert not (tmp_path / BROWSER_FILE).exists()
