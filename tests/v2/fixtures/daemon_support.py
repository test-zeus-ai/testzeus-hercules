"""Shared helpers for the daemon and MCP tests: configs, an in-process client and a daemon thread."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import threading
import time
import warnings
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

with warnings.catch_warnings():
    warnings.simplefilter("ignore")  # Starlette suggests a different httpx package for TestClient
    from starlette.testclient import TestClient

from testzeus_hercules.daemon.app import create_app
from testzeus_hercules.daemon.config import DaemonConfig
from testzeus_hercules.daemon.discovery import DaemonInfo, write_daemon_file
from testzeus_hercules.daemon.server import bind_socket
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.settings import Settings

FAKE_WORKER = Path(__file__).resolve().with_name("fake_worker.py")
TOKEN = "t" * 43
BASE_URL = "http://127.0.0.1:7373"
FINAL = {"passed", "failed", "error", "cancelled"}


def isolated_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """Environment for settings and workers: an isolated home, telemetry off."""
    # PLAYWRIGHT_BROWSERS_PATH (pinned in tests/v2/conftest.py) keeps the browsers findable under the moved HOME.
    keep = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "TMPDIR", "LANG", "PLAYWRIGHT_BROWSERS_PATH") if name in os.environ}
    return {**keep, "HOME": str(tmp_path), "HERCULES_HOME": str(tmp_path / "home"), "HERCULES_TELEMETRY": "0", "PYTHONUNBUFFERED": "1", **extra}


def make_config(tmp_path: Path, *, env: dict[str, str] | None = None, **overrides: Any) -> DaemonConfig:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    environ = env if env is not None else isolated_env(tmp_path)
    settings = Settings.load(project, env=environ)
    HerculesPaths.from_settings(settings).ensure_dirs()
    fields: dict[str, Any] = {
        "settings": settings,
        "token": TOKEN,
        "env": environ,
        "worker_command": (sys.executable, str(FAKE_WORKER)),
        "cancel_grace_s": 1.0,
    }
    fields.update(overrides)
    return DaemonConfig(**fields)


@contextlib.contextmanager
def client_for(config: DaemonConfig, *, auth: bool = True) -> Iterator[TestClient]:
    headers = {"Authorization": f"Bearer {config.token}"} if auth else {}
    with TestClient(create_app(config), base_url=BASE_URL, headers=headers) as client:
        yield client


def case_dict(*tags: str, case_id: str = "demo-login", steps: int = 3, status: str = "ready") -> dict[str, Any]:
    return {
        "id": case_id,
        "title": "Demo login",
        "tags": list(tags),
        "environment": {"base_url": "http://127.0.0.1:9"},
        "steps": [{"action": f"step number {index}"} for index in range(1, steps)] + [{"action": "verify", "expected": "Welcome"}],
        "status": status,
    }


def submit(client: TestClient, *tags: str, **body: Any) -> str:
    payload = body or {"test_case": case_dict(*tags)}
    response = client.post("/api/runs", json=payload)
    assert response.status_code == 201, response.text
    return str(response.json()["run_id"])


def wait_for(client: TestClient, run_id: str, statuses: set[str] = FINAL, timeout_s: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in statuses:
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} stayed {body['status']}: {body}")
        time.sleep(0.05)


@contextlib.contextmanager
def daemon_thread(config: DaemonConfig) -> Iterator[DaemonInfo]:
    """Serve the daemon over real HTTP on a free loopback port and write daemon.json."""
    import uvicorn

    sock = bind_socket("127.0.0.1", 0)
    port = int(sock.getsockname()[1])
    config = replace(config, port=port)
    server = uvicorn.Server(uvicorn.Config(create_app(config), log_level="warning", lifespan="on"))
    thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[sock])), name="test-daemon", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("daemon did not start")
        time.sleep(0.02)
    paths = HerculesPaths.from_settings(config.settings)
    info = DaemonInfo(host="127.0.0.1", port=port, pid=os.getpid(), token=config.token, started_at=datetime.now(timezone.utc).isoformat(), project_dir=str(config.settings.project_dir))
    write_daemon_file(paths.daemon_file, info)
    try:
        yield info
    finally:
        server.should_exit = True
        thread.join(15)
        sock.close()
