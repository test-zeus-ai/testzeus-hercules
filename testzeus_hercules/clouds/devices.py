"""The devices and desktop browsers a connected cloud offers its account: read-only REST calls with HTTP Basic auth.

* BrowserStack: mobile from ``GET https://api-cloud.browserstack.com/app-automate/devices.json`` (real devices);
  desktop from ``GET https://api.browserstack.com/automate/browsers.json`` (entries without a device).
* Sauce Labs (in the account's region): virtual mobile devices from ``GET /rest/v1/info/platforms/appium`` (the newest
  OS of each), real ones from ``GET /v1/rdc/devices``; desktop from ``GET /rest/v1/info/platforms/webdriver``.
* TestMu AI: real mobile devices from ``GET https://mobile-api.lambdatest.com/mobile-automation/api/v1/list?region=``
  (``eu-mobile-api`` for ``eu``); desktop from ``GET https://api.lambdatest.com/automation/api/v1/platforms``.

Each cloud's calls run together; a part that fails becomes a note and the others are kept. A fetched list is saved in
``<hercules home>/cache/cloud-devices-<cloud>.json`` (mode 0600) until the cloud is disconnected or other keys are
saved. Nothing here returns or logs a key.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx

from testzeus_hercules.clouds.credentials import CloudCredentials, sauce_region, testmu_region
from testzeus_hercules.clouds.registry import CloudId, cloud_info
from testzeus_hercules.kernel.private_env import EDIT_LOCK, write_private_text

FETCH_TIMEOUT_S = 30.0
CATALOG_VERSION = 1
_MAX_ITEMS = 10_000  # per answer: more is not a device list

ClientFactory = Callable[..., Any]
Platform = Literal["android", "ios"]
Kind = Literal["phone", "tablet"]


class DeviceFetchError(Exception):
    """No part of the cloud's device list could be fetched; the message is safe to show."""


@dataclass(frozen=True)
class MobileDevice:
    name: str
    platform: Platform
    os_version: str
    kind: Kind
    real: bool
    tier: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class DesktopBrowser:
    os: str
    os_version: str
    browser: str
    versions: tuple[str, ...]


@dataclass(frozen=True)
class DeviceCatalog:
    cloud: CloudId
    username: str
    region: str | None
    fetched_at: str
    mobile: tuple[MobileDevice, ...] = ()
    desktop: tuple[DesktopBrowser, ...] = ()
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        view = asdict(self)
        view["desktop"] = [{**item, "versions": list(item["versions"])} for item in view["desktop"]]
        return view


# --------------------------------------------------------------------------- mapping the clouds' answers

_TABLET = re.compile(r"\b(ipad|tab|tablet)\b", re.IGNORECASE)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else ""


def _platform(value: Any) -> Platform | None:
    lowered = _text(value).lower()
    return "android" if lowered == "android" else "ios" if lowered in ("ios", "iphone", "ipad") else None


def _kind(name: str) -> Kind:
    return "tablet" if _TABLET.search(name) else "phone"


def version_key(version: str) -> tuple[int, tuple[int, ...], str]:
    """Newest first when sorted: words such as ``latest`` or ``beta`` before numbers, then numbers descending."""
    numbers = re.findall(r"\d+", version)
    if not numbers:
        return (0, (), version.lower())
    return (1, tuple(-int(number) for number in numbers[:4]), version.lower())


def _items(payload: Any, *keys: str) -> list[Any]:
    """The list in an answer: the answer itself, or the first of ``keys`` holding one."""
    if isinstance(payload, dict):
        for key in keys:
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise ValueError("the answer is not a list")
    return [item for item in payload[:_MAX_ITEMS] if isinstance(item, dict)]


def _os_family(name: str) -> str:
    lowered = name.lower()
    if lowered.startswith("win"):
        return "Windows"
    if lowered.startswith(("os x", "osx", "mac")):
        return "macOS"
    if lowered.startswith(("linux", "ubuntu")):
        return "Linux"
    return name.strip() or "Other"


# Sauce Labs names older Windows desktops after the server release they match.
_WINDOWS_SERVER_NAMES = {"2008": "7", "2012": "8", "2012 r2": "8.1"}


def _split_os(text: str) -> tuple[str, str]:
    """``("Windows", "11")`` from ``Windows 11``, ``("macOS", "Sonoma")`` from ``macOS Sonoma`` or ``Mac 14``."""
    match = re.match(r"^\s*(windows|win|os x|osx|macos|mac|linux|ubuntu)\s*(.*?)\s*$", text, re.IGNORECASE)
    if match is None:
        return _os_family(text), ""
    family, version = _os_family(match.group(1)), match.group(2)
    if family == "Windows":
        version = _WINDOWS_SERVER_NAMES.get(version.lower(), version)
    return family, version


_BROWSER_NAMES = {"microsoftedge": "edge", "msedge": "edge", "internet explorer": "ie", "googlechrome": "chrome"}


def _browser(name: str) -> str:
    lowered = name.strip().lower()
    return _BROWSER_NAMES.get(lowered, lowered)


def parse_browserstack_mobile(payload: Any) -> list[MobileDevice]:
    found = []
    for item in _items(payload, "devices"):
        name, platform, version = _text(item.get("device")), _platform(item.get("os")), _text(item.get("os_version"))
        if name and platform and version:
            tier = _text(item.get("device_tier")) or None
            found.append(MobileDevice(name, platform, version, _kind(name), bool(item.get("realMobile", True)), tier=tier))
    return found


def parse_browserstack_desktop(payload: Any) -> list[DesktopBrowser]:
    rows = []
    for item in _items(payload, "browsers"):
        if item.get("device") or item.get("real_mobile"):
            continue
        os_name, browser = _text(item.get("os")), _text(item.get("browser"))
        if os_name and browser:
            rows.append((_os_family(os_name), _text(item.get("os_version")), _browser(browser), _text(item.get("browser_version")) or "latest"))
    return _grouped(rows)


def parse_sauce_virtual(payload: Any) -> list[MobileDevice]:
    """Sauce Labs emulators and simulators, the newest OS of each (as the device pool script keeps them)."""
    newest: dict[str, MobileDevice] = {}
    for item in _items(payload, "platforms"):
        api_name, name, version = _text(item.get("api_name")).lower(), _text(item.get("long_name")), _text(item.get("short_version"))
        platform = _platform(api_name)
        if platform is None or not name or not version:
            continue
        kind: Kind = "tablet" if api_name == "ipad" or (platform == "android" and "tablet" in name.lower()) else "phone"
        device = MobileDevice(name, platform, version, kind, real=False)
        current = newest.get(name)
        if current is None or version_key(version) < version_key(current.os_version):
            newest[name] = device
    return list(newest.values())


def parse_sauce_real(payload: Any) -> list[MobileDevice]:
    found = []
    for item in _items(payload, "entities", "devices"):
        name, platform, version = _text(item.get("name")), _platform(item.get("os")), _text(item.get("osVersion"))
        if name and platform and version:
            tablet = item.get("isTablet")
            kind: Kind = ("tablet" if tablet else "phone") if isinstance(tablet, bool) else _kind(name)
            found.append(MobileDevice(name, platform, version, kind, real=True, tier="private" if item.get("isPrivate") is True else None))
    return found


def parse_sauce_desktop(payload: Any) -> list[DesktopBrowser]:
    rows = []
    for item in _items(payload, "platforms"):
        api_name, os_text = _text(item.get("api_name")), _text(item.get("os"))
        if not api_name or not os_text or _platform(api_name) is not None:
            continue
        os_name, os_version = _split_os(os_text)
        rows.append((os_name, os_version, _browser(api_name), _text(item.get("short_version")) or "latest"))
    return _grouped(rows)


def parse_testmu_mobile(payload: Any) -> list[MobileDevice]:
    found = []
    for item in _items(payload, "data", "devices"):
        name, platform, version = _text(item.get("deviceName")), _platform(item.get("platformName")), _text(item.get("platformVersion"))
        if name and platform and version:
            found.append(MobileDevice(name, platform, version, _kind(name), real=True, status=_text(item.get("deviceStatus")) or None))
    return found


def parse_testmu_desktop(payload: Any) -> list[DesktopBrowser]:
    platforms: Any = payload.get("platforms") if isinstance(payload, dict) else payload
    if isinstance(platforms, dict):  # grouped by kind: keep the desktop group
        platforms = next((value for key, value in platforms.items() if "desktop" in str(key).lower()), [])
    rows = []
    for entry in _items(platforms):
        os_name, os_version = _split_os(_text(entry.get("platform")))
        browsers = entry.get("browsers")
        for item in browsers[:_MAX_ITEMS] if isinstance(browsers, list) else []:
            if isinstance(item, dict) and _text(item.get("browser_name")):
                rows.append((os_name, os_version, _browser(_text(item["browser_name"])), _text(item.get("version")) or "latest"))
    return _grouped(rows)


def _grouped(rows: Iterable[tuple[str, str, str, str]]) -> list[DesktopBrowser]:
    versions: dict[tuple[str, str, str], set[str]] = {}
    for os_name, os_version, browser, version in rows:
        versions.setdefault((os_name, os_version, browser), set()).add(version)
    return [DesktopBrowser(*key, versions=tuple(sorted(found, key=version_key))) for key, found in versions.items()]


def _sorted_mobile(devices: Iterable[MobileDevice]) -> tuple[MobileDevice, ...]:
    unique = {(device.name.lower(), device.platform, device.os_version, device.real): device for device in devices}
    return tuple(sorted(unique.values(), key=lambda d: (d.platform, version_key(d.os_version), d.name.lower(), not d.real)))


def _sorted_desktop(browsers: Iterable[DesktopBrowser]) -> tuple[DesktopBrowser, ...]:
    return tuple(sorted(browsers, key=lambda b: (b.os, version_key(b.os_version), b.browser)))


# --------------------------------------------------------------------------- fetching


@dataclass(frozen=True)
class _Part:
    what: str  # "mobile devices", "real devices", "desktop browsers"
    url: str
    parse: Callable[[Any], list[Any]]
    # Answers that mean "this account has none of these" rather than an error (Sauce Labs real devices without a plan).
    absent_on: tuple[int, ...] = ()


def _testmu_mobile_url(region: str) -> str:
    host = "eu-mobile-api.lambdatest.com" if region == "eu" else "mobile-api.lambdatest.com"
    return f"https://{host}/mobile-automation/api/v1/list?region={region}"


def _parts(cloud: CloudId, credentials: CloudCredentials) -> tuple[list[_Part], str | None]:
    if cloud == "browserstack":
        return [
            _Part("mobile devices", "https://api-cloud.browserstack.com/app-automate/devices.json", parse_browserstack_mobile),
            _Part("desktop browsers", "https://api.browserstack.com/automate/browsers.json", parse_browserstack_desktop),
        ], None
    if cloud == "saucelabs":
        region = sauce_region(credentials)
        api = f"https://api.{region}.saucelabs.com"
        return [
            _Part("virtual devices", f"{api}/rest/v1/info/platforms/appium", parse_sauce_virtual),
            _Part("real devices", f"{api}/v1/rdc/devices", parse_sauce_real, absent_on=(403, 404)),
            _Part("desktop browsers", f"{api}/rest/v1/info/platforms/webdriver", parse_sauce_desktop),
        ], region
    region = testmu_region(credentials)
    return [
        _Part("real devices", _testmu_mobile_url(region), parse_testmu_mobile),
        _Part("desktop browsers", "https://api.lambdatest.com/automation/api/v1/platforms", parse_testmu_desktop),
    ], region


def device_urls(cloud: CloudId, credentials: CloudCredentials) -> list[str]:
    return [part.url for part in _parts(cloud, credentials)[0]]


async def _fetch_part(client: Any, part: _Part, name: str) -> tuple[list[Any], str | None, int | None]:
    """``(items, note, refused status)`` for one part."""
    try:
        response = await client.get(part.url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        return [], f"{part.what}: {name} could not be reached ({type(exc).__name__})", None
    if response.status_code in part.absent_on:
        return [], f"{part.what}: none on this account (HTTP {response.status_code})", None
    if response.status_code in (401, 403):
        return [], f"{part.what}: {name} refused the credentials (HTTP {response.status_code})", response.status_code
    if response.status_code >= 400:
        return [], f"{part.what}: {name} answered HTTP {response.status_code}", None
    try:
        items = part.parse(response.json())
    except (ValueError, TypeError, AttributeError):
        return [], f"{part.what}: {name} sent an answer Hercules does not understand", None
    if not items and part.absent_on:
        return [], f"{part.what}: none on this account", None
    return items, None, None


async def fetch_devices(cloud: CloudId, credentials: CloudCredentials, client_factory: ClientFactory = httpx.AsyncClient) -> DeviceCatalog:
    """The mobile devices and desktop browsers ``cloud`` offers the account; :class:`DeviceFetchError` when none could be read."""
    name = cloud_info(cloud).name
    if credentials.auth is None or credentials.username is None:
        raise DeviceFetchError(f"{name} is not connected")
    try:
        parts, region = _parts(cloud, credentials)
    except ValueError as exc:
        raise DeviceFetchError(str(exc)) from None
    async with client_factory(timeout=FETCH_TIMEOUT_S, auth=credentials.auth, follow_redirects=False) as client:
        results = await asyncio.gather(*(_fetch_part(client, part, name) for part in parts))
    notes = [note for _, note, _ in results if note]
    fetched = [(part, items) for part, (items, note, _) in zip(parts, results) if note is None]
    if not fetched:
        refused = next((status for _, _, status in results if status), None)
        if refused is not None:
            raise DeviceFetchError(f"{name} refused the credentials (HTTP {refused}): check the user name and access key")
        raise DeviceFetchError("; ".join(notes) or f"{name} sent no devices")
    if cloud == "testmu":
        notes.append("virtual devices: TestMu AI does not list its emulators and simulators; only real devices are shown")
    mobile = [item for part, items in fetched for item in items if isinstance(item, MobileDevice)]
    desktop = [item for part, items in fetched for item in items if isinstance(item, DesktopBrowser)]
    return DeviceCatalog(
        cloud=cloud,
        username=credentials.username,
        region=region,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        mobile=_sorted_mobile(mobile),
        desktop=_sorted_desktop(desktop),
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- the saved list


def catalog_path(home: Path, cloud: CloudId) -> Path:
    return home / "cache" / f"cloud-devices-{cloud}.json"


def save_catalog(home: Path, catalog: DeviceCatalog) -> Path:
    return write_private_text(catalog_path(home, catalog.cloud), json.dumps({"version": CATALOG_VERSION, **catalog.to_dict()}) + "\n")


def load_catalog(home: Path, cloud: CloudId) -> DeviceCatalog | None:
    """The saved list, or None when there is none (or it is unreadable, from another version, or for another cloud)."""
    try:
        data = json.loads(catalog_path(home, cloud).read_text(encoding="utf-8"))
        if data.get("version") != CATALOG_VERSION or data.get("cloud") != cloud:
            return None
        return DeviceCatalog(
            cloud=cloud,
            username=str(data["username"]),
            region=data.get("region"),
            fetched_at=str(data["fetched_at"]),
            mobile=tuple(MobileDevice(**item) for item in data.get("mobile", [])),
            desktop=tuple(DesktopBrowser(**{**item, "versions": tuple(item["versions"])}) for item in data.get("desktop", [])),
            notes=tuple(str(note) for note in data.get("notes", [])),
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def forget_catalog(home: Path, cloud: CloudId) -> None:
    with EDIT_LOCK:
        catalog_path(home, cloud).unlink(missing_ok=True)
