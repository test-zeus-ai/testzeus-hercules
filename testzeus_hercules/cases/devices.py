"""Playwright's device profiles, for mobile web emulation (``environment.device``).

A profile ("iPhone 15", "Pixel 7", ...) sets the page's viewport, user agent, device scale factor, touch and the
mobile meta viewport. The profiles come from the Playwright package installed with Hercules, so the names are exactly
the ones Playwright Test and pytest-playwright know, and an exported test opens the same device. Older Playwright
releases ship them as ``deviceDescriptorsSource.json``, read directly; newer ones bundle them into the driver, which is
then started once (in a thread of its own, so this works inside a running event loop) to list them.
"""

from __future__ import annotations

import json
import logging
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_SOURCE = Path("driver", "package", "lib", "server", "deviceDescriptorsSource.json")
# Profile keys (camelCase, as Playwright writes them) -> ``browser.new_context`` keyword arguments.
_CONTEXT_KEYS = {"userAgent": "user_agent", "viewport": "viewport", "screen": "screen", "deviceScaleFactor": "device_scale_factor", "isMobile": "is_mobile", "hasTouch": "has_touch"}
_FROM_PYTHON = {target: key for key, target in _CONTEXT_KEYS.items()} | {"default_browser_type": "defaultBrowserType"}


class UnknownDevice(ValueError):
    """The name is not a Playwright device profile."""


@lru_cache(maxsize=1)
def _profiles() -> Mapping[str, Mapping[str, Any]]:
    try:
        import playwright
    except ImportError:  # perception is optional in some builds; no profiles then
        return {}
    path = Path(playwright.__file__).parent / _SOURCE
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else _from_driver()
    except Exception as exc:  # noqa: BLE001 - a broken install or a driver that cannot start: no profiles, said once
        logger.warning("Playwright's device profiles could not be read (%s)", type(exc).__name__)
        return {}
    return {str(name): dict(profile) for name, profile in data.items() if isinstance(profile, dict)}


def _from_driver() -> dict[str, Any]:
    """The profiles as the running driver lists them (``playwright.devices``), with Playwright's own camelCase keys."""
    found: dict[str, Any] = {}
    failed: list[BaseException] = []

    def read() -> None:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as driver:
                for name, profile in driver.devices.items():
                    found[name] = {_FROM_PYTHON.get(key, key): value for key, value in profile.items()}
        except BaseException as exc:  # noqa: BLE001 - reported by the caller
            failed.append(exc)

    worker = threading.Thread(target=read, name="playwright-devices", daemon=True)
    worker.start()
    worker.join(60)
    if failed:
        raise failed[0]
    if worker.is_alive() or not found:
        raise TimeoutError("the Playwright driver did not list its devices")
    return found


def device_names() -> tuple[str, ...]:
    """Every known profile name, sorted (empty when the Playwright package is not installed)."""
    return tuple(sorted(_profiles()))


def canonical_name(name: str) -> str:
    """The profile's exact name for ``name`` (case and repeated spaces ignored); :class:`UnknownDevice` when there is none.

    Without the profiles (no Playwright package) the name is kept as written; the browser checks it when it starts.
    """
    profiles = _profiles()
    wanted = " ".join(name.split())
    if not profiles or wanted in profiles:
        return wanted
    folded = {key.casefold(): key for key in profiles}
    found = folded.get(wanted.casefold())
    if found is None:
        close = [key for key in profiles if wanted.casefold() in key.casefold()][:5]
        hint = f"; did you mean {', '.join(repr(key) for key in close)}?" if close else "; see docs/guide/mobile-testing.md"
        raise UnknownDevice(f"unknown device {name!r}: use a Playwright device profile name such as 'iPhone 15' or 'Pixel 7'{hint}")
    return found


def context_options(name: str) -> dict[str, Any]:
    """``browser.new_context`` keyword arguments for the profile (viewport, user agent, scale, mobile, touch)."""
    profile = _profiles().get(canonical_name(name))
    if profile is None:
        raise UnknownDevice(f"unknown device {name!r}")
    return {target: profile[key] for key, target in _CONTEXT_KEYS.items() if key in profile}


def viewport_of(name: str) -> tuple[int, int] | None:
    """The profile's viewport (width, height) in CSS pixels, or None when it is not known."""
    profile = _profiles().get(canonical_name(name)) or {}
    viewport = profile.get("viewport")
    if isinstance(viewport, dict) and "width" in viewport and "height" in viewport:
        return int(viewport["width"]), int(viewport["height"])
    return None
