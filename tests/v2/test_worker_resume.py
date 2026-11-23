"""A real ``hercules worker`` killed mid-run resumes with ``--resume`` from its last completed step.

Also drives the worker's control channel (JSON lines on stdin): pause, step and resume
at step boundaries, and an approval for a destructive click.
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Any, Iterator

import pytest

from tests.v2.browser_fixtures import FIXTURE_PAGES, chromium_executable
from tests.v2.fixtures.daemon_support import isolated_env
from tests.v2.fixtures.fake_model import FakeModel
from testzeus_hercules.engine.browser_process import BROWSER_FILE, stop_browser

# The workers read steps and decide with a fake local model served over HTTP (there is no coded fallback).
_MODEL_ENV: dict[str, str] = {}


@pytest.fixture(autouse=True)
def fake_model() -> Iterator[None]:
    with FakeModel().env() as env:
        _MODEL_ENV.clear()
        _MODEL_ENV.update(env)
        yield


@contextlib.contextmanager
def pages_server() -> Iterator[str]:
    handler = functools.partial(_QuietHandler, directory=str(FIXTURE_PAGES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return None


class Worker:
    def __init__(self, tmp_path: Path, *args: str, control: bool = False) -> None:
        chromium = chromium_executable()
        if chromium is None and not os.environ.get("CI"):
            pytest.skip("no Chromium for worker tests")
        self.project = tmp_path / "project"
        self.project.mkdir(exist_ok=True)
        env = isolated_env(tmp_path, HERCULES_CHROMIUM=chromium or "", **_MODEL_ENV)
        command = [sys.executable, "-m", "testzeus_hercules.cli.main", "worker", "--project", str(self.project), *args]
        if control:
            command.append("--control")
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd=str(self.project), text=True, start_new_session=True)
        self.events: list[dict[str, Any]] = []
        self._reader = threading.Thread(target=self._read, args=(self.proc.stdout,), daemon=True)
        self._reader.start()

    def _read(self, stream: IO[str]) -> None:
        for line in stream:
            with contextlib.suppress(ValueError):
                self.events.append(json.loads(line))

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def wait_event(self, event_kind: str, timeout: float = 60.0, **match: Any) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in list(self.events):
                if event["kind"] == event_kind and all(event["data"].get(key) == value for key, value in match.items()):
                    return event
            if self.proc.poll() is not None and not self._reader.is_alive():
                break
            time.sleep(0.02)
        raise AssertionError(f"no {event_kind} {match} event; got {[event['kind'] for event in self.events]}")

    def finish(self, timeout: float = 120.0) -> int:
        code = self.proc.wait(timeout=timeout)
        self._reader.join(timeout=5)
        return code

    def kinds(self, kind: str) -> list[dict[str, Any]]:
        return [event["data"] for event in self.events if event["kind"] == kind]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


@pytest.fixture(autouse=True)
def stop_leftover_browsers(tmp_path: Path) -> Iterator[None]:
    """A test that fails half-way never leaves the browsers its workers started running."""
    yield
    for record in tmp_path.glob(f"**/.hercules/runs/*/{BROWSER_FILE}"):
        stop_browser(record.parent)


def write_case(tmp_path: Path, base_url: str, steps: list[dict[str, Any]], case_id: str = "profile") -> Path:
    path = tmp_path / f"{case_id}.json"
    path.write_text(json.dumps({"id": case_id, "title": "Profile", "environment": {"base_url": base_url}, "steps": steps}), encoding="utf-8")
    return path


def test_killed_worker_resumes_from_the_last_completed_step(tmp_path: Path) -> None:
    run_id = "r_20261026_120000_abc123"
    with pages_server() as base_url:
        case = write_case(
            tmp_path,
            base_url,
            [
                {"action": 'Open "/form.html"'},
                {"action": 'Fill "Grace Hopper" in "Full name"'},
                {"action": "Wait 4 seconds"},
                {"action": "verify", "expected": 'I should see "Edit profile"'},
            ],
        )
        first = Worker(tmp_path, "--run-id", run_id, "--case", str(case))
        first.wait_event("step_started", step=3)
        # LangGraph writes each checkpoint in the background; let step 2's land before the crash (step 3 waits 4 s),
        # or the resume correctly starts again from step 2 and the assertions below would be about timing, not resuming.
        time.sleep(1.0)
        run_dir = first.project / ".hercules" / "runs" / run_id
        recorded = json.loads((run_dir / BROWSER_FILE).read_text())
        os.killpg(first.proc.pid, signal.SIGKILL)  # the worker dies mid-step; Chromium has its own process group
        first.finish()
        # Chromium may still be running, or may already be shutting down on its own once its driver's pipe closes
        # (tens of ms; longer on a busy machine). Either way the supervisor's clean-up leaves no browser and no record.
        # Signalling a browser that stays up is covered with a stand-in process in test_browser_process.py.
        stop_browser(run_dir)
        assert _gone(recorded["pid"]), "the dead worker's browser is gone after the supervisor's clean-up"
        assert not (run_dir / BROWSER_FILE).exists()
        assert not (run_dir / "run.json").exists()
        assert (first.project / ".hercules" / "checkpoints" / f"{run_id}.db").exists()

        started = time.monotonic()
        second = Worker(tmp_path, "--resume", run_id)
        code = second.finish()
        resume_s = time.monotonic() - started

    assert code == 0, second.events
    resumed = second.kinds("run_resumed")
    assert resumed and resumed[0]["from_step"] == 3
    assert [data["step"] for data in second.kinds("step_started")] == [3, 4]
    result = json.loads((run_dir / "run.json").read_text())
    assert result["status"] == "passed"
    assert [step["status"] for step in result["steps"]] == ["passed"] * 4
    assert result["steps"][1]["action"] == 'Fill "Grace Hopper" in "Full name"'
    assert any("resumed" in hint for hint in result["hints"])
    assert not (first.project / ".hercules" / "checkpoints" / f"{run_id}.db").exists()
    assert resume_s < 60
    assert not (run_dir / BROWSER_FILE).exists(), "a worker that closes its browser removes the record"


def test_resume_of_an_unknown_run_is_an_error(tmp_path: Path) -> None:
    worker = Worker(tmp_path, "--resume", "r_20261026_120000_ffffff")
    assert worker.finish() == 2
    assert worker.kinds("error")


def test_control_channel_pause_step_resume_and_destructive_approval(tmp_path: Path) -> None:
    run_id = "r_20261026_120001_abc124"
    with pages_server() as base_url:
        case = write_case(
            tmp_path,
            base_url,
            [
                {"action": 'Open "/form.html"'},
                {"action": 'Fill "Grace Hopper" in "Full name"'},
                {"action": 'Click "Save profile"'},
                {"action": "verify", "expected": 'I should see "Edit profile"'},
            ],
        )
        worker = Worker(tmp_path, "--run-id", run_id, "--case", str(case), "--confirm-destructive", "--destructive-terms", "save profile", control=True)
        worker.send({"type": "control", "action": "pause"})
        paused = worker.wait_event("paused", step=1)
        assert paused["data"]["where"] == "step"
        worker.send({"type": "control", "action": "step"})
        worker.wait_event("step_finished", step=1)
        worker.wait_event("paused", step=2)
        time.sleep(0.3)
        assert not worker.kinds("step_started")[1:]  # still paused before step 2
        worker.send({"type": "input", "kind": "click", "x": 0.5, "y": 0.5})
        worker.wait_event("input_result")  # no live view in this test: rejected, but answered
        sent = time.monotonic()
        worker.send({"type": "control", "action": "resume"})
        worker.wait_event("resumed", step=2)
        resume_latency = time.monotonic() - sent
        request = worker.wait_event("needs_approval", kind="destructive")
        assert request["data"]["step"] == 3 and "Save profile" in request["data"]["reason"]
        worker.send({"type": "approval", "id": request["data"]["id"], "decision": "approve"})
        code = worker.finish()

    assert code == 0, worker.events
    assert worker.kinds("approval_result")[0]["decision"] == "approve"
    assert resume_latency < 2.0
