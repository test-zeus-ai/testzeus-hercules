"""``hercules serve``: the local daemon (run queue, workers, HTTP API) for one project.

Binds 127.0.0.1 by default and refuses any other address unless ``--allow-remote``
is given and ``HERCULES_DAEMON_TOKEN`` holds a strong token. Writes
``~/.hercules/daemon.json`` (mode 0600) with the port, pid and API token, and
removes it on exit. SIGINT/SIGTERM stop the daemon and its workers.

Exit codes: 0 after a clean stop, 1 when a daemon is already running, 2 when it
cannot start (bad settings, refused address, port in use).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, TextIO

EXIT_OK = 0
EXIT_RUNNING = 1
EXIT_ERROR = 2


@dataclass(frozen=True)
class ServeOptions:
    project_dir: Path | None = None
    host: str | None = None
    port: int | None = None
    allow_remote: bool = False
    parallel: int | None = None
    fallback_port: bool = False
    worker_command: str | None = None


def _worker_command(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if not isinstance(value, list) or not value or not all(isinstance(part, str) and part for part in value):
        raise ValueError("--worker-command must be a JSON list of strings")
    return tuple(value)


def run_serve(options: ServeOptions, *, env: Mapping[str, str] | None = None, err: TextIO | None = None) -> int:
    from testzeus_hercules.daemon.config import DaemonConfig
    from testzeus_hercules.daemon.discovery import new_token
    from testzeus_hercules.daemon.server import TOKEN_ENV, DaemonAlreadyRunning, DaemonStartError, bind_socket, check_bind, check_not_running, serve
    from testzeus_hercules.kernel.settings import Settings, SettingsError

    errors = err if err is not None else sys.stderr
    environ = dict(os.environ) if env is None else dict(env)
    try:
        settings = Settings.load(options.project_dir, env=environ)
        host = options.host or settings.ui.host
        port = settings.ui.port if options.port is None else options.port
        configured_token = environ.get(TOKEN_ENV) or None
        check_bind(host, allow_remote=options.allow_remote, token_from_env=configured_token)
        worker_command = _worker_command(options.worker_command)
        extra = {"worker_command": worker_command} if worker_command else {}
        config = DaemonConfig(
            settings=settings,
            token=configured_token or new_token(),
            env=environ,
            host=host,
            port=port,
            allow_remote=options.allow_remote,
            parallel_runs=options.parallel or _tier_parallel_runs(settings),
            **extra,
        )
        check_not_running(config)
        sock = bind_socket(host, port, fallback_to_free_port=options.fallback_port)
    except DaemonAlreadyRunning as exc:
        errors.write(f"hercules serve: {exc}\n")
        return EXIT_RUNNING
    except (DaemonStartError, SettingsError, ValueError) as exc:
        errors.write(f"hercules serve: {exc}\n")
        return EXIT_ERROR
    config = replace(config, port=int(sock.getsockname()[1]))
    serve(config, sock, notices=errors)
    return EXIT_OK


def _tier_parallel_runs(settings: object) -> int:
    from testzeus_hercules.llm.tiers import detect_memory_bytes, select_tier

    profile = select_tier(detect_memory_bytes(), settings.llm.tier).profile  # type: ignore[attr-defined]
    return max(1, int(profile.parallel_runs))
