"""Device cloud credentials: the environment first, else ``<hercules home>/clouds.env``.

Each cloud's user name and access key live in the environment variables named in ``mobile.<cloud>``
(``BROWSERSTACK_USERNAME``/``BROWSERSTACK_ACCESS_KEY``, ``SAUCE_USERNAME``/``SAUCE_ACCESS_KEY`` and
``LT_USERNAME``/``LT_ACCESS_KEY`` by default). When the environment does not hold them, the private file
``clouds.env`` may: ``NAME=value`` lines, readable by its owner only (mode 0600; a file others can read is
ignored). The tester UI saves pasted keys there. Values are added to a copy of the environment mapping handed
to a run, never to the process environment; nothing here logs or returns a key in a message.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from testzeus_hercules.clouds.registry import CLOUD_IDS, CloudId
from testzeus_hercules.kernel.settings import DeviceCloudSettings, Settings

CLOUDS_FILENAME = "clouds.env"
_PRIVATE_FILE = 0o600
_PRIVATE_DIR = 0o700
_MAX_FILE_BYTES = 16_384
_MAX_VALUE = 512

Source = Literal["env", "file", "none"]


class CredentialError(ValueError):
    """A credential could not be saved; the message never contains a value."""


@dataclass(frozen=True)
class CloudCredentials:
    """A cloud's credentials and where they came from. ``repr`` never shows the access key."""

    cloud: CloudId
    username_env: str
    access_key_env: str
    source: Source
    username: str | None = None
    access_key: str | None = field(default=None, repr=False)
    problem: str | None = None

    @property
    def found(self) -> bool:
        return bool(self.username and self.access_key)

    @property
    def auth(self) -> tuple[str, str] | None:
        return (self.username, self.access_key) if self.username and self.access_key else None


# User names copied from a docs example instead of the real one: YOUR_USERNAME, <username>, ${USER}, changeme.
_PLACEHOLDER = re.compile(r"(your[_ -]?\w*|<[^>]*>|\$\{?\w+\}?|x{3,}|changeme|change[_-]me|username|user[_-]?name|example\w*|placeholder)", re.IGNORECASE)


def placeholder_problem(credentials: CloudCredentials) -> str | None:
    """Why the found user name looks like a placeholder rather than a real one (None when it looks real)."""
    username = (credentials.username or "").strip()
    if not username or not _PLACEHOLDER.fullmatch(username):
        return None
    return f"the user name {username!r} looks like a placeholder; connect again with the user name from the cloud's account page"


def clouds_file(home: Path) -> Path:
    return home / CLOUDS_FILENAME


def cloud_settings(settings: Settings, cloud: CloudId) -> DeviceCloudSettings:
    return getattr(settings.mobile, cloud)


def _parse(text: str) -> dict[str, str]:
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


def read_clouds_file(home: Path) -> tuple[dict[str, str], str | None]:
    """``(values, problem)`` from ``clouds.env``; the problem never quotes the file."""
    path = clouds_file(home)
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
    if info.st_size > _MAX_FILE_BYTES:
        return {}, f"{path} is larger than {_MAX_FILE_BYTES} bytes"
    try:
        return _parse(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"cannot read {path} ({type(exc).__name__})"


def find_credentials(cloud: CloudId, settings: Settings, env: Mapping[str, str]) -> CloudCredentials:
    """The cloud's credentials: both values from the environment, else both from ``clouds.env``."""
    config = cloud_settings(settings, cloud)
    names = (config.username_env, config.access_key_env)
    username, key = (env.get(names[0], "").strip(), env.get(names[1], "").strip())
    if username and key:
        return CloudCredentials(cloud, *names, source="env", username=username, access_key=key)
    stored, problem = read_clouds_file(settings.home_dir)
    username, key = (stored.get(names[0], ""), stored.get(names[1], ""))
    if username and key:
        return CloudCredentials(cloud, *names, source="file", username=username, access_key=key)
    return CloudCredentials(cloud, *names, source="none", problem=problem)


def with_cloud_credentials(env: Mapping[str, str], settings: Settings) -> dict[str, str]:
    """A copy of ``env`` holding every cloud's credentials that only ``clouds.env`` has (the environment wins)."""
    merged = dict(env)
    for cloud in CLOUD_IDS:
        found = find_credentials(cloud, settings, env)
        if found.source == "file" and found.username and found.access_key:
            merged[found.username_env] = found.username
            merged[found.access_key_env] = found.access_key
    return merged


def _clean(value: str, what: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > _MAX_VALUE or any(ch in cleaned for ch in "\r\n\0") or cleaned != cleaned.strip("'\""):
        raise CredentialError(f"the {what} must be one line of at most {_MAX_VALUE} characters, without quotes")
    return cleaned


def _write(home: Path, values: Mapping[str, str]) -> Path:
    home.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR)
    path = clouds_file(home)
    temporary = path.with_name(path.name + ".tmp")
    lines = ["# Device cloud credentials saved by Hercules (readable by you only). Environment variables win."]
    lines += [f"{name}={value}" for name, value in sorted(values.items())]
    temporary.unlink(missing_ok=True)  # never write through a file or link left behind
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), _PRIVATE_FILE)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, _PRIVATE_FILE)
        os.write(descriptor, ("\n".join(lines) + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    return path


def save_credentials(cloud: CloudId, settings: Settings, username: str, access_key: str) -> Path:
    """Save the cloud's user name and access key to ``clouds.env`` (mode 0600), keeping the other clouds' lines."""
    config = cloud_settings(settings, cloud)
    values, problem = read_clouds_file(settings.home_dir)
    if problem and clouds_file(settings.home_dir).exists():
        raise CredentialError(problem)
    values[config.username_env] = _clean(username, "user name")
    values[config.access_key_env] = _clean(access_key, "access key")
    return _write(settings.home_dir, values)


def remove_credentials(cloud: CloudId, settings: Settings) -> bool:
    """Remove the cloud's lines from ``clouds.env``; True when there were any. The environment is not touched."""
    config = cloud_settings(settings, cloud)
    values, problem = read_clouds_file(settings.home_dir)
    if problem:
        raise CredentialError(problem)
    removed = {name for name in (config.username_env, config.access_key_env) if name in values}
    if not removed:
        return False
    remaining = {name: value for name, value in values.items() if name not in removed}
    if remaining:
        _write(settings.home_dir, remaining)
    else:
        clouds_file(settings.home_dir).unlink(missing_ok=True)
    return True
