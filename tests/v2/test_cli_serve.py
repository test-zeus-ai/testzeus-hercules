"""``hercules serve`` and ``hercules worker`` as real processes."""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from testzeus_hercules.daemon.discovery import read_daemon_file
from tests.v2.engine_fixtures.browser import FIXTURES_DIR, require_chromium
from tests.v2.fixtures.daemon_support import FAKE_WORKER, isolated_env
from tests.v2.fixtures.fake_model import FakeModel, action

REPO_ROOT = Path(__file__).resolve().parents[2]


def _hercules(*args: str) -> list[str]:
    return [sys.executable, "-m", "testzeus_hercules.cli.main", *args]


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {**isolated_env(tmp_path, **extra), "PYTHONPATH": str(REPO_ROOT)}


def _wait_for_daemon(path: Path, process: subprocess.Popen[str], timeout_s: float = 10) -> None:
    deadline = time.monotonic() + timeout_s
    while read_daemon_file(path) is None:
        if process.poll() is not None:
            raise AssertionError(f"serve exited early: {process.stderr.read() if process.stderr else ''}")
        if time.monotonic() > deadline:
            raise AssertionError("daemon.json was not written")
        time.sleep(0.05)


def test_serve_writes_a_private_daemon_file_and_stops_cleanly(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    daemon_file = tmp_path / "home" / "daemon.json"
    started = time.monotonic()
    process = subprocess.Popen(
        _hercules("serve", "--port", "0", "--project", str(project), "--worker-command", json.dumps([sys.executable, str(FAKE_WORKER)])),
        env=_env(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_daemon(daemon_file, process)
        info = read_daemon_file(daemon_file)
        assert info is not None
        url = f"http://127.0.0.1:{info.port}"
        deadline = time.monotonic() + 10
        while True:
            try:
                health = httpx.get(f"{url}/api/health", headers={"Authorization": f"Bearer {info.token}"}, timeout=2)
                break
            except httpx.TransportError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
        startup_s = time.monotonic() - started
        assert health.status_code == 200
        assert startup_s < 6, startup_s
        assert stat.S_IMODE(daemon_file.stat().st_mode) == 0o600
        assert info.pid == process.pid
        assert httpx.get(f"{url}/api/health", timeout=2).status_code == 401

        case = {"id": "hang", "title": "Hang", "tags": ["fake-hang"], "steps": [{"action": "wait"}], "environment": {"base_url": "http://127.0.0.1:9"}}
        headers = {"Authorization": f"Bearer {info.token}"}
        run_id = httpx.post(f"{url}/api/runs", json={"test_case": case}, headers=headers, timeout=5).json()["run_id"]
        worker_pid = None
        deadline = time.monotonic() + 10
        while not worker_pid and time.monotonic() < deadline:
            worker_pid = httpx.get(f"{url}/api/runs/{run_id}", headers=headers, timeout=5).json().get("worker_pid")
            time.sleep(0.05)
        assert worker_pid

        process.send_signal(signal.SIGTERM)
        process.wait(timeout=20)
    finally:
        if process.poll() is None:
            process.kill()
    assert not daemon_file.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)


def test_serve_refuses_a_non_loopback_host(tmp_path: Path) -> None:
    completed = subprocess.run(_hercules("serve", "--host", "0.0.0.0", "--port", "0", "--project", str(tmp_path)), env=_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert completed.returncode == 2
    assert "loopback" in completed.stderr
    assert not (tmp_path / "home" / "daemon.json").exists()


def test_serve_refuses_remote_without_token(tmp_path: Path) -> None:
    completed = subprocess.run(_hercules("serve", "--host", "0.0.0.0", "--allow-remote", "--port", "0", "--project", str(tmp_path)), env=_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert completed.returncode == 2
    assert "HERCULES_DAEMON_TOKEN" in completed.stderr


def test_serve_refuses_to_start_twice(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    daemon_file = tmp_path / "home" / "daemon.json"
    first = subprocess.Popen(_hercules("serve", "--port", "0", "--project", str(project)), env=_env(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _wait_for_daemon(daemon_file, first)
        time.sleep(0.5)
        second = subprocess.run(_hercules("serve", "--port", "0", "--project", str(project)), env=_env(tmp_path), capture_output=True, text=True, timeout=60)
        assert second.returncode == 1
        assert "already running" in second.stderr
    finally:
        first.send_signal(signal.SIGTERM)
        first.wait(timeout=20)


def test_worker_streams_events_and_writes_evidence(tmp_path: Path) -> None:
    chromium = require_chromium()
    project = tmp_path / "project"
    run_dir = project / ".hercules" / "runs" / "r_worker_test"
    run_dir.mkdir(parents=True)
    page = project / "site" / "login.html"  # file: URLs open only inside the project
    page.parent.mkdir()
    for name in ("login.html", "welcome.html"):
        shutil.copy(FIXTURES_DIR / name, page.parent / name)
    case = {
        "id": "worker-login",
        "title": "Worker login",
        "steps": [
            {"action": f'Open "{page.as_uri()}"'},
            {"action": 'Fill "alice" in "Username"'},
            {"action": 'Click the "Sign in" button'},
        ],
    }
    case_path = run_dir / "case.json"
    case_path.write_text(json.dumps(case))
    model = FakeModel()
    model.add(case["steps"][0]["action"], action("navigate", url=page.as_uri()))
    with model.env() as model_env:
        completed = subprocess.run(
            _hercules("worker", "--run-id", "r_worker_test", "--case", str(case_path), "--project", str(project)),
            env=_env(tmp_path, HERCULES_CHROMIUM=chromium, **model_env),
            capture_output=True,
            text=True,
            timeout=180,
        )
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    kinds = [event["kind"] for event in events]
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished"
    for kind in ("step_started", "decision", "action", "screenshot", "step_finished"):
        assert kind in kinds, kinds
    assert all(set(event) == {"kind", "data", "at"} for event in events)
    result = json.loads((run_dir / "run.json").read_text())
    assert result["run_id"] == "r_worker_test" and result["status"] == "passed"


def test_worker_reports_setup_errors_as_an_event(tmp_path: Path) -> None:
    project = tmp_path / "project"
    run_dir = project / ".hercules" / "runs" / "r_worker_bad"
    run_dir.mkdir(parents=True)
    (run_dir / "case.json").write_text('{"id": "x"}')
    completed = subprocess.run(
        _hercules("worker", "--run-id", "r_worker_bad", "--case", str(run_dir / "case.json"), "--project", str(project)),
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert completed.returncode == 2
    assert events[-1]["kind"] == "error"
    assert "invalid test case" in events[-1]["data"]["message"]
