from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Literal

from testzeus_hercules.kernel.private_env import read_private_env, write_private_env

SECRETS_FILENAME = "secrets.env"
SECRET_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
MAX_SECRET_CHARS = 4096
MAX_SECRETS_BYTES = 262_144

SecretSource = Literal["env", "file", "none"]


class SecretStoreError(ValueError):
    pass


def secrets_file(home: Path) -> Path:
    return home / SECRETS_FILENAME


def read_saved_secrets(home: Path) -> tuple[dict[str, str], str | None]:
    return read_private_env(secrets_file(home), MAX_SECRETS_BYTES)


def _saved(home: Path) -> dict[str, str]:
    values, problem = read_saved_secrets(home)
    if problem and secrets_file(home).exists():
        raise SecretStoreError(problem)
    return values


def _checked_name(name: str) -> str:
    if not SECRET_NAME.fullmatch(name):
        raise SecretStoreError(f"{name!r} is not an environment variable name: use UPPER_CASE letters, digits and _")
    return name


def save_secret(home: Path, name: str, value: str) -> Path:
    _checked_name(name)
    if not value or len(value) > MAX_SECRET_CHARS or any(ch in value for ch in "\r\n\0") or value != value.strip():
        raise SecretStoreError(f"the value must be one line of at most {MAX_SECRET_CHARS} characters, without leading or trailing spaces")
    values = _saved(home)
    values[name] = value
    return write_private_env(secrets_file(home), values, "Secrets saved by Hercules (readable by you only). Environment variables win.")


def remove_secret(home: Path, name: str) -> bool:
    _checked_name(name)
    values = _saved(home)
    if name not in values:
        return False
    del values[name]
    if values:
        write_private_env(secrets_file(home), values, "Secrets saved by Hercules (readable by you only). Environment variables win.")
    else:
        secrets_file(home).unlink(missing_ok=True)
    return True


def with_saved_secrets(env: Mapping[str, str], home: Path) -> dict[str, str]:
    merged = dict(env)
    values, _ = read_saved_secrets(home)
    for name, value in values.items():
        if SECRET_NAME.fullmatch(name) and not merged.get(name):
            merged[name] = value
    return merged


def secret_sources(names: Iterable[str], env: Mapping[str, str], home: Path) -> dict[str, SecretSource]:
    values, _ = read_saved_secrets(home)
    return {name: "env" if env.get(name) else "file" if values.get(name) else "none" for name in names}
