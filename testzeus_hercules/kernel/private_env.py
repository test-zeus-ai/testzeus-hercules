from __future__ import annotations

import os
import secrets
import stat
import threading
from collections.abc import Mapping
from pathlib import Path

PRIVATE_FILE = 0o600
PRIVATE_DIR = 0o700
MAX_FILE_BYTES = 16_384
EDIT_LOCK = threading.RLock()


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        name, sep, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if sep and name.strip():
            values[name.strip()] = value.strip()
    return values


def read_private_env(path: Path, max_bytes: int = MAX_FILE_BYTES) -> tuple[dict[str, str], str | None]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        return {}, f"cannot read {path} ({type(exc).__name__})"
    if not stat.S_ISREG(info.st_mode):
        return {}, f"{path} is not a regular file"
    if os.name == "posix" and info.st_mode & 0o077:
        return {}, f"{path} is readable by other users (mode {stat.S_IMODE(info.st_mode):o}); ignored. Fix it with: chmod 600 {path}"
    if info.st_size > max_bytes:
        return {}, f"{path} is larger than {max_bytes} bytes"
    try:
        return parse_env(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"cannot read {path} ({type(exc).__name__})"


def env_text(values: Mapping[str, str], header: str) -> str:
    return "\n".join([f"# {header}"] + [f"{name}='{value}'" for name, value in sorted(values.items())]) + "\n"


def write_private_env(path: Path, values: Mapping[str, str], header: str) -> Path:
    return write_private_text(path, env_text(values, header))


def write_private_text(path: Path, text: str) -> Path:
    """Replace ``path`` atomically with ``text``, readable by its owner only (mode 0600, in a 0700 folder)."""
    with EDIT_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), PRIVATE_FILE)
        try:
            try:
                if hasattr(os, "fchmod"):
                    os.fchmod(descriptor, PRIVATE_FILE)
                os.write(descriptor, text.encode("utf-8"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return path
