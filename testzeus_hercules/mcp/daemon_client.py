"""HTTP client for the daemon, used by the MCP shim: find it via ``daemon.json`` or start it.

When no healthy daemon is recorded, ``hercules serve`` is started detached for the
current project and the client waits up to ``start_timeout_s`` (8 s, well inside
the 10 s some agents allow for MCP startup) for ``/api/health``. A connection
failure or a 401 re-reads ``daemon.json`` once, so a restarted daemon (new port or
token) is picked up without restarting the agent. A daemon recorded for another project
is refused with :class:`DaemonUnavailable` (one daemon serves one project).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

import httpx

from testzeus_hercules.daemon.discovery import DaemonInfo, daemon_file_path, home_dir, read_daemon_file

START_TIMEOUT_S = 8.0
HEALTH_TIMEOUT_S = 1.5
REQUEST_TIMEOUT_S = 15.0
_POLL_S = 0.1


class DaemonUnavailable(RuntimeError):
    """No daemon is reachable and none could be started."""


class DaemonClient:
    def __init__(self, *, env: Mapping[str, str] | None = None, project_dir: Path | None = None, start_timeout_s: float = START_TIMEOUT_S, autostart: bool = True) -> None:
        self.env = dict(os.environ if env is None else env)
        self.project_dir = Path(project_dir or os.getcwd())
        self.start_timeout_s = start_timeout_s
        self.autostart = autostart
        self.daemon_file = daemon_file_path(self.env)
        self.info: DaemonInfo | None = None
        self.started_now = False
        self._lock = asyncio.Lock()
        # The coding agent on the other end of the MCP connection (a fixed name such as "claude-code"), sent to the
        # daemon so runs it starts are marked as started by that agent.
        self.agent: str | None = None
        self._http = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, follow_redirects=False, trust_env=False)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ discovery

    async def ensure(self, *, start: bool | None = None, refresh: bool = False) -> DaemonInfo:
        async with self._lock:
            if self.info is not None and not refresh:
                return self.info
            info = read_daemon_file(self.daemon_file)
            if info is not None and await self._healthy(info):
                self._check_project(info)
                self.info = info
                return info
            if not (self.autostart if start is None else start):
                raise DaemonUnavailable("the Hercules daemon is not running; start it with `hercules serve`")
            started = await self._start(previous=info)
            self._check_project(started)
            self.info = started
            self.started_now = True
            return self.info

    def _check_project(self, info: DaemonInfo) -> None:
        """Refuse a daemon serving another project: its runs, cases and environments are not this project's."""
        if not info.project_dir or _same_dir(Path(info.project_dir), self.project_dir):
            return
        raise DaemonUnavailable(
            f"the running Hercules daemon (pid {info.pid}, {info.url}) serves another project ({info.project_dir}), not {self.project_dir}; "
            "stop it (or finish its runs) and call again, or start the agent from that project"
        )

    async def _healthy(self, info: DaemonInfo) -> bool:
        try:
            response = await self._http.get(f"{info.url}/api/health", headers=_auth(info), timeout=HEALTH_TIMEOUT_S)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def _start(self, previous: DaemonInfo | None) -> DaemonInfo:
        log_path = home_dir(self.env) / "daemon.log"
        log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        command = [sys.executable, "-m", "testzeus_hercules.cli.main", "serve", "--project", str(self.project_dir), "--fallback-port"]
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log_fd, stderr=log_fd, env=self.env, cwd=str(self.project_dir), start_new_session=True)  # noqa: S603 - fixed argv
        finally:
            os.close(log_fd)
        deadline = time.monotonic() + self.start_timeout_s
        while time.monotonic() < deadline:
            info = read_daemon_file(self.daemon_file)
            if info is not None and info != previous and await self._healthy(info):
                return info
            if process.poll() is not None:
                info = read_daemon_file(self.daemon_file)
                if info is not None and await self._healthy(info):
                    return info  # another daemon won the race
                raise DaemonUnavailable(f"`hercules serve` exited with code {process.returncode}: {_last_line(log_path)}")
            await asyncio.sleep(_POLL_S)
        raise DaemonUnavailable(f"the Hercules daemon did not become ready within {self.start_timeout_s:.0f} s (see {log_path})")

    # ------------------------------------------------------------------ requests

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        timeout_s: float | None = None,
        start: bool | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        info = await self.ensure(start=start)
        try:
            response = await self._send(info, method, path, json=json, params=params, timeout_s=timeout_s, headers=headers)
        except httpx.TransportError:
            response = None
        if response is None or response.status_code == 401:
            self.info = None
            info = await self.ensure(start=start, refresh=True)
            response = await self._send(info, method, path, json=json, params=params, timeout_s=timeout_s, headers=headers)
        return response

    async def _send(
        self, info: DaemonInfo, method: str, path: str, *, json: Any, params: Mapping[str, Any] | None, timeout_s: float | None, headers: Mapping[str, str] | None = None
    ) -> httpx.Response:
        sent = {**(headers or {}), **_auth(info), **({AGENT_HEADER: self.agent} if self.agent else {})}
        return await self._http.request(method, f"{info.url}{path}", json=json, params=params, headers=sent, timeout=timeout_s or REQUEST_TIMEOUT_S)


def _same_dir(first: Path, second: Path) -> bool:
    try:
        return first.resolve() == second.resolve()
    except OSError:
        return os.path.abspath(first) == os.path.abspath(second)


AGENT_HEADER = "X-Hercules-Agent"


def _auth(info: DaemonInfo) -> dict[str, str]:
    return {"Authorization": f"Bearer {info.token}"}


def _last_line(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return "no log"
    return lines[-1][:300] if lines else "no output"
