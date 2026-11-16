"""Run the daemon: check the bind address, bind, write ``daemon.json``, serve with uvicorn, clean up."""

from __future__ import annotations

import asyncio
import errno
import os
import signal
import socket
import threading
from datetime import datetime, timezone
from typing import TextIO

from testzeus_hercules.daemon.config import TOKEN_ENV, DaemonConfig
from testzeus_hercules.daemon.discovery import DaemonInfo, pid_alive, read_daemon_file, remove_daemon_file, write_daemon_file
from testzeus_hercules.daemon.security import is_loopback_host

__all__ = ["TOKEN_ENV", "DaemonStartError", "DaemonAlreadyRunning", "check_bind", "bind_socket", "check_not_running", "serve"]
MIN_REMOTE_TOKEN = 32


class DaemonStartError(RuntimeError):
    """The daemon cannot start; the message says why and what to do."""


class DaemonAlreadyRunning(DaemonStartError):
    pass


def check_bind(host: str, *, allow_remote: bool, token_from_env: str | None) -> None:
    """Refuse a non-loopback bind unless ``--allow-remote`` is given and a strong token is configured.

    The daemon itself speaks plain HTTP: a remote daemon must sit behind a TLS-terminating proxy that sets
    ``X-Forwarded-Proto: https`` (the session cookie is then marked Secure). Requests must name a loopback host, the
    bound host, or a hostname listed in ``HERCULES_DAEMON_ALLOWED_HOSTS`` (comma-separated) in ``Host``; any other is refused.
    """
    if is_loopback_host(host):
        return
    if not allow_remote:
        raise DaemonStartError(f"refusing to bind {host!r}: the daemon listens on a loopback address only (127.0.0.1). Use --allow-remote with {TOKEN_ENV} set to expose it.")
    if not token_from_env:
        raise DaemonStartError(f"--allow-remote needs a token: set {TOKEN_ENV} to a random value of at least {MIN_REMOTE_TOKEN} characters")
    if len(token_from_env) < MIN_REMOTE_TOKEN:
        raise DaemonStartError(f"{TOKEN_ENV} must be at least {MIN_REMOTE_TOKEN} characters")


def bind_socket(host: str, port: int, *, fallback_to_free_port: bool = False) -> socket.socket:
    """A listening TCP socket on ``host:port`` (port 0: any free port)."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE and fallback_to_free_port and port != 0:
            sock.close()
            return bind_socket(host, 0)
        sock.close()
        if exc.errno == errno.EADDRINUSE:
            raise DaemonStartError(f"port {port} on {host} is in use; pass --port with another port (or 0 for any free port)") from None
        raise DaemonStartError(f"cannot listen on {host}:{port} ({exc.strerror or exc.__class__.__name__})") from None
    sock.listen(128)
    sock.set_inheritable(False)
    return sock


def check_not_running(config: DaemonConfig) -> None:
    info = read_daemon_file(config.paths.daemon_file)
    if info is not None and info.pid != os.getpid() and pid_alive(info.pid) and _answers(info):
        raise DaemonAlreadyRunning(f"a Hercules daemon is already running (pid {info.pid}, {info.url}); stop it first or use it")


def _answers(info: DaemonInfo) -> bool:
    """Whether the recorded daemon still answers (a reused pid alone must not block a new daemon)."""
    import httpx

    try:
        response = httpx.get(f"{info.url}/api/health", headers={"Authorization": f"Bearer {info.token}"}, timeout=1.5, trust_env=False)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def serve(config: DaemonConfig, sock: socket.socket, *, notices: TextIO) -> None:
    """Serve until SIGINT/SIGTERM; workers are terminated and ``daemon.json`` removed on the way out."""
    import uvicorn

    from testzeus_hercules.daemon.app import create_app

    paths = config.paths
    app = create_app(config)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on", access_log=False, timeout_graceful_shutdown=max(5, int(config.cancel_grace_s) + 3)))
    info = DaemonInfo(
        port=config.port, pid=os.getpid(), token=config.token, started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), host=config.host, project_dir=str(config.settings.project_dir)
    )

    async def run() -> None:
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started and not task.done():
            await asyncio.sleep(0.01)
        if server.started:
            write_daemon_file(paths.daemon_file, info)
            login = app.state.daemon.sessions.new_code()
            notices.write(f"Hercules daemon on {config.public_url} (project {config.settings.project_dir}, {config.parallel_runs} parallel run(s))\n")
            notices.write(f"Open the UI once with: {config.public_url}/login?code={login}\n")
            notices.write("(the link works once; run `hercules ui` for a new one)\n")
            notices.flush()
        await task

    if threading.current_thread() is threading.main_thread():
        # uvicorn restores the previous handlers and re-raises the stop signal after its graceful
        # shutdown; make that a no-op so daemon.json is removed and the exit code stays 0.
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda signum, frame: None)
    try:
        asyncio.run(run())
    finally:
        remove_daemon_file(paths.daemon_file, pid=os.getpid())
        sock.close()
