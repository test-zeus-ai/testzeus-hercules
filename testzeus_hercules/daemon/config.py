"""Daemon configuration: one immutable object built by ``hercules serve`` (or a test)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.settings import Settings

DEFAULT_CANCEL_GRACE_S = 5.0
MAX_PARALLEL_RUNS = 8
TOKEN_ENV = "HERCULES_DAEMON_TOKEN"
# With --allow-remote: the hostnames (comma-separated) clients may name in ``Host``, besides loopback and the bound host.
ALLOWED_HOSTS_ENV = "HERCULES_DAEMON_ALLOWED_HOSTS"


def default_worker_command() -> tuple[str, ...]:
    """``hercules worker`` with the daemon's own interpreter, so workers use the same install."""
    return (sys.executable, "-m", "testzeus_hercules.cli.main", "worker")


@dataclass(frozen=True)
class DaemonConfig:
    settings: Settings
    token: str
    # Environment for workers (model keys travel only here) and for checking which keys are set.
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    host: str = "127.0.0.1"
    port: int = 7373
    allow_remote: bool = False
    parallel_runs: int = 1
    worker_command: tuple[str, ...] = field(default_factory=default_worker_command)
    cancel_grace_s: float = DEFAULT_CANCEL_GRACE_S
    # Built UI files; None serves the ones packaged in ``testzeus_hercules/ui/static``.
    ui_dir: Path | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.parallel_runs <= MAX_PARALLEL_RUNS:
            raise ValueError(f"parallel_runs must be between 1 and {MAX_PARALLEL_RUNS}")
        if len(self.token) < 32:
            raise ValueError("the daemon token must be at least 32 characters")
        if not self.worker_command:
            raise ValueError("worker_command must not be empty")

    @property
    def paths(self) -> HerculesPaths:
        return HerculesPaths.from_settings(self.settings)

    @property
    def worker_env(self) -> dict[str, str]:
        """The environment a worker starts with: ``env`` without the daemon's token (a worker never calls the daemon)."""
        return {name: value for name, value in self.env.items() if name != TOKEN_ENV}

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """Extra ``Host`` names accepted with remote access, from ``HERCULES_DAEMON_ALLOWED_HOSTS``."""
        if not self.allow_remote:
            return ()
        return tuple(name.strip() for name in self.env.get(ALLOWED_HOSTS_ENV, "").split(",") if name.strip())

    @property
    def public_url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::", "localhost") else self.host
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{self.port}"
