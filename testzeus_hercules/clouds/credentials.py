"""Device cloud credentials: the environment first, else ``<hercules home>/clouds.env``.

Each cloud's user name and access key live in the environment variables named in ``mobile.<cloud>``
(``BROWSERSTACK_USERNAME``/``BROWSERSTACK_ACCESS_KEY``, ``SAUCE_USERNAME``/``SAUCE_ACCESS_KEY`` and
``LT_USERNAME``/``LT_ACCESS_KEY`` by default). When the environment does not hold them, the private file
``clouds.env`` may: ``NAME=value`` lines, readable by its owner only (mode 0600; a file others can read is
ignored). The tester UI saves pasted keys there. The Sauce Labs data center comes from ``SAUCE_REGION`` in the
environment, else the one saved in ``clouds.env``, else ``mobile.saucelabs.region``, else ``us-west-1``. Values are added to a copy of the environment mapping handed
to a run, never to the process environment; nothing here logs or returns a key in a message.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from testzeus_hercules.clouds.registry import CLOUD_IDS, CloudId
from testzeus_hercules.kernel.private_env import read_private_env, write_private_env
from testzeus_hercules.kernel.settings import DeviceCloudSettings, Settings

CLOUDS_FILENAME = "clouds.env"
SAUCE_REGION_ENV = "SAUCE_REGION"
SAUCELABS_REGIONS = ("us-west-1", "us-east-4", "eu-central-1")
DEFAULT_SAUCE_REGION = "us-west-1"
_MAX_FILE_BYTES = 16_384
_MAX_VALUE = 512

Source = Literal["env", "file", "none"]
RegionSource = Literal["env", "file", "config", "default"]


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
    region: str | None = None
    region_source: RegionSource | None = None

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


def read_clouds_file(home: Path) -> tuple[dict[str, str], str | None]:
    """``(values, problem)`` from ``clouds.env``; the problem never quotes the file."""
    return read_private_env(clouds_file(home), _MAX_FILE_BYTES)


def _region(cloud: CloudId, config: DeviceCloudSettings, env: Mapping[str, str], stored: Mapping[str, str]) -> tuple[str | None, RegionSource | None]:
    if cloud != "saucelabs":
        return None, None
    for value, source in ((env.get(SAUCE_REGION_ENV, ""), "env"), (stored.get(SAUCE_REGION_ENV, ""), "file"), (config.region or "", "config")):
        if value.strip():
            return value.strip().lower(), source  # type: ignore[return-value]
    return DEFAULT_SAUCE_REGION, "default"


_REGION_WHERE = f"choose it on the Integrations screen, with hercules cloud connect saucelabs --region, in {SAUCE_REGION_ENV}, or in mobile.saucelabs.region"


def sauce_region(credentials: CloudCredentials) -> str:
    region = credentials.region or DEFAULT_SAUCE_REGION
    if region not in SAUCELABS_REGIONS:
        raise ValueError(f"the Sauce Labs region must be one of {', '.join(SAUCELABS_REGIONS)} ({_REGION_WHERE})")
    return region


def find_credentials(cloud: CloudId, settings: Settings, env: Mapping[str, str]) -> CloudCredentials:
    """The cloud's credentials: both values from the environment, else both from ``clouds.env``."""
    config = cloud_settings(settings, cloud)
    names = (config.username_env, config.access_key_env)
    stored, problem = read_clouds_file(settings.home_dir)
    region, region_source = _region(cloud, config, env, stored)
    username, key = (env.get(names[0], "").strip(), env.get(names[1], "").strip())
    if username and key:
        return CloudCredentials(cloud, *names, source="env", username=username, access_key=key, region=region, region_source=region_source)
    username, key = (stored.get(names[0], ""), stored.get(names[1], ""))
    if username and key:
        return CloudCredentials(cloud, *names, source="file", username=username, access_key=key, region=region, region_source=region_source)
    return CloudCredentials(cloud, *names, source="none", problem=problem, region=region, region_source=region_source)


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
    return write_private_env(clouds_file(home), values, "Device cloud credentials saved by Hercules (readable by you only). Environment variables win.")


def _checked_region(cloud: CloudId, region: str) -> str:
    if cloud != "saucelabs":
        raise CredentialError("only Sauce Labs has a region")
    cleaned = region.strip().lower()
    if cleaned not in SAUCELABS_REGIONS:
        raise CredentialError(f"the Sauce Labs region must be one of {', '.join(SAUCELABS_REGIONS)}")
    return cleaned


def _saved_values(settings: Settings) -> dict[str, str]:
    values, problem = read_clouds_file(settings.home_dir)
    if problem and clouds_file(settings.home_dir).exists():
        raise CredentialError(problem)
    return values


def save_credentials(cloud: CloudId, settings: Settings, username: str, access_key: str, region: str | None = None) -> Path:
    """Save the cloud's user name and access key (and Sauce Labs region) to ``clouds.env`` (mode 0600), keeping the
    other clouds' lines."""
    config = cloud_settings(settings, cloud)
    checked = _checked_region(cloud, region) if region is not None else None
    values = _saved_values(settings)
    values[config.username_env] = _clean(username, "user name")
    values[config.access_key_env] = _clean(access_key, "access key")
    if checked is not None:
        values[SAUCE_REGION_ENV] = checked
    return _write(settings.home_dir, values)


def save_region(cloud: CloudId, settings: Settings, region: str) -> Path:
    checked = _checked_region(cloud, region)
    values = _saved_values(settings)
    values[SAUCE_REGION_ENV] = checked
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
