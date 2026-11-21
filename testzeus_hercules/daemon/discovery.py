"""``~/.hercules/daemon.json``: how clients find the running daemon (port, pid, token).

Standard library only, so the MCP shim can import it cheaply. The file is
written atomically with mode 0600 inside a 0700 directory; the token in it is
the only credential for the daemon's API.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

DAEMON_FILENAME = "daemon.json"
TOKEN_BYTES = 32
_FILE_MODE = 0o600
_DIR_MODE = 0o700


@dataclass(frozen=True)
class DaemonInfo:
    """What ``daemon.json`` holds."""

    port: int
    pid: int
    token: str
    started_at: str
    host: str = "127.0.0.1"
    project_dir: str | None = None

    @property
    def url(self) -> str:
        host = self.host
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{self.port}"


def new_token() -> str:
    """A fresh random API token (32 random bytes, URL-safe)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def home_dir(env: Mapping[str, str] | None = None) -> Path:
    """The Hercules home directory, resolved like ``Settings.load`` does (``HERCULES_HOME``, else ``~/.hercules``)."""
    environ = os.environ if env is None else env
    configured = environ.get("HERCULES_HOME")
    if configured:
        return Path(os.path.abspath(os.path.expanduser(configured)))
    home = environ.get("HOME")
    return (Path(home) if home else Path.home()) / ".hercules"


def daemon_file_path(env: Mapping[str, str] | None = None) -> Path:
    return home_dir(env) / DAEMON_FILENAME


def write_daemon_file(path: Path, info: DaemonInfo) -> None:
    """Write ``info`` atomically; the file is readable by the current user only."""
    try:
        path.parent.mkdir(parents=True, mode=_DIR_MODE)
    except FileExistsError:
        pass
    else:
        path.parent.chmod(_DIR_MODE)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, _FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(info), handle, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    path.chmod(_FILE_MODE)


def read_daemon_file(path: Path) -> DaemonInfo | None:
    """The daemon record at ``path``, or None when it is missing or malformed."""
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    port, pid, token, started = data.get("port"), data.get("pid"), data.get("token"), data.get("started_at")
    host, project = data.get("host", "127.0.0.1"), data.get("project_dir")
    if not (isinstance(port, int) and 0 < port < 65536 and isinstance(pid, int) and pid > 0):
        return None
    if not (isinstance(token, str) and token and isinstance(started, str) and isinstance(host, str)):
        return None
    if project is not None and not isinstance(project, str):
        return None
    return DaemonInfo(port=port, pid=pid, token=token, started_at=started, host=host, project_dir=project)


def remove_daemon_file(path: Path, *, pid: int) -> None:
    """Delete ``path`` if it still describes the daemon with ``pid`` (another daemon may have replaced it)."""
    info = read_daemon_file(path)
    if info is not None and info.pid == pid:
        path.unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
