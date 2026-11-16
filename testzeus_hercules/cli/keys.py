"""Where the JEV (OpenRouter) key comes from: the environment, else a private key file in the Hercules home.

The key is never stored in configuration. Hercules looks for it, in order:

1. the environment variable (``OPENROUTER_API_KEY`` unless ``decider.api_key_env`` or
   ``--jev-key-env`` names another);
2. ``<hercules home>/openrouter.env``, one ``NAME=value`` line, readable by its owner
   only (mode 0600). A file that others can read is ignored with a warning.

A key found in the file is added to the run's environment mapping (never to the
process environment). Nothing here prints, logs or returns the key itself.

The model endpoint's key (``llm.api_key_env``, for a hosted OpenAI-compatible endpoint) is found the same way, in
``<hercules home>/llm.env``; an OpenRouter model shares the OpenRouter key and its file.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = ["DEFAULT_KEY_ENV", "KEY_FILENAME", "MODEL_KEY_FILENAME", "KeyStatus", "find_key", "key_file", "with_key", "with_model_key", "write_key_file"]

DEFAULT_KEY_ENV = "OPENROUTER_API_KEY"
KEY_FILENAME = "openrouter.env"
MODEL_KEY_FILENAME = "llm.env"
_PRIVATE_FILE = 0o600
_PRIVATE_DIR = 0o700
_MAX_FILE_BYTES = 4096

KeySource = Literal["env", "file", "none"]


@dataclass(frozen=True)
class KeyStatus:
    """Which variable holds the key, where it was found, and any problem with the key file (never the key)."""

    variable: str
    source: KeySource
    path: Path
    problem: str | None = None

    @property
    def found(self) -> bool:
        return self.source != "none"

    def describe(self) -> str:
        if self.source == "env":
            return f"from the environment variable {self.variable}"
        if self.source == "file":
            return f"from {self.path} ({self.variable})"
        return f"not set (looked for {self.variable} in the environment and in {self.path})"


def key_file(home: Path, filename: str = KEY_FILENAME) -> Path:
    return home / filename


def _parse(text: str, variable: str) -> str | None:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        name, sep, value = line.partition("=")
        if sep and name.strip() == variable:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            return value.strip() or None
    return None


def _read_file(path: Path, variable: str) -> tuple[str | None, str | None]:
    """``(key, problem)`` from the key file; the problem never quotes the file's content."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"cannot read {path} ({type(exc).__name__})"
    if not stat.S_ISREG(info.st_mode):
        return None, f"{path} is not a regular file"
    if os.name == "posix" and info.st_mode & 0o077:
        return None, f"{path} is readable by other users (mode {stat.S_IMODE(info.st_mode):o}); ignored. Fix it with: chmod 600 {path}"
    if info.st_size > _MAX_FILE_BYTES:
        return None, f"{path} is larger than {_MAX_FILE_BYTES} bytes; expected one NAME=value line"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"cannot read {path} ({type(exc).__name__})"
    value = _parse(text, variable)
    if value is None:
        return None, f"{path} has no {variable}=... line"
    return value, None


def find_key(env: Mapping[str, str], home: Path, variable: str = DEFAULT_KEY_ENV, filename: str = KEY_FILENAME) -> tuple[str | None, KeyStatus]:
    """The key and where it came from; the environment wins over the file."""
    path = key_file(home, filename)
    value = env.get(variable, "").strip()
    if value:
        return value, KeyStatus(variable, "env", path)
    found, problem = _read_file(path, variable)
    if found:
        return found, KeyStatus(variable, "file", path)
    return None, KeyStatus(variable, "none", path, problem)


def with_key(env: Mapping[str, str], home: Path, variable: str = DEFAULT_KEY_ENV) -> tuple[dict[str, str], KeyStatus]:
    """A copy of ``env`` holding the key when only the key file has it, and where the key came from."""
    value, status = find_key(env, home, variable)
    merged = dict(env)
    if status.source == "file" and value:
        merged[variable] = value
    return merged, status


def with_model_key(env: Mapping[str, str], home: Path, variable: str | None) -> dict[str, str]:
    """A copy of ``env`` holding the model endpoint's key (``variable``) when only a key file has it: ``llm.env``,
    else ``openrouter.env`` (an OpenRouter model uses the OpenRouter key)."""
    merged = dict(env)
    if not variable or env.get(variable, "").strip():
        return merged
    for filename in (MODEL_KEY_FILENAME, KEY_FILENAME):
        value, _problem = _read_file(key_file(home, filename), variable)
        if value:
            merged[variable] = value
            break
    return merged


def write_key_file(home: Path, value: str, variable: str = DEFAULT_KEY_ENV, filename: str = KEY_FILENAME) -> Path:
    """Save the key to ``<home>/<filename>`` (``openrouter.env``) readable by its owner only; returns the path."""
    cleaned = value.strip()
    if not cleaned or any(ch in cleaned for ch in "\r\n\0") or len(cleaned) > 1024:
        raise ValueError("the key must be a single line of at most 1024 characters")
    home.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR)
    path = key_file(home, filename)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _PRIVATE_FILE)
    try:
        os.fchmod(descriptor, _PRIVATE_FILE)
        os.write(descriptor, f"{variable}={cleaned}\n".encode("utf-8"))
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    return path
