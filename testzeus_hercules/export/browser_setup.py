"""The run's browser and ``browser_options`` in exported code: pytest-playwright fixtures and Playwright Test ``test.use``.

Values are written as literals; secrets as environment-variable reads (``os.environ["X"]`` / ``env("X")``), which the
renderer lists as required; the auth state file as a path the test reads from ``STORAGE_STATE`` when it is set.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from testzeus_hercules.cases.automation import AutomationSpec
from testzeus_hercules.cases.browser_options import BrowserOptions, Secret, channel_of, engine_of

__all__ = ["STORAGE_STATE_ENV", "VIDEO_DIR", "launch_items", "context_items", "camel", "engine", "secrets_of"]

STORAGE_STATE_ENV = "STORAGE_STATE"
VIDEO_DIR = "videos/"
_CAMEL = {"ignore_https_errors": "ignoreHTTPSErrors", "extra_http_headers": "extraHTTPHeaders", "bypass_csp": "bypassCSP"}
_CONTEXT_SCALARS = (
    "locale",
    "timezone_id",
    "color_scheme",
    "reduced_motion",
    "forced_colors",
    "user_agent",
    "ignore_https_errors",
    "java_script_enabled",
    "bypass_csp",
    "offline",
    "accept_downloads",
    "service_workers",
    "device_scale_factor",
    "is_mobile",
    "has_touch",
)

Value = Any  # a JSON value, or a Secret


def camel(name: str) -> str:
    """Playwright's JavaScript name for a Python option name."""
    if name in _CAMEL:
        return _CAMEL[name]
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def engine(spec: AutomationSpec) -> str:
    return engine_of(spec.browser_name)


def options_of(spec: AutomationSpec) -> BrowserOptions:
    return spec.browser_options or BrowserOptions()


def secrets_of(spec: AutomationSpec) -> list[str]:
    return options_of(spec).secret_names()


def launch_items(spec: AutomationSpec) -> list[tuple[str, Value]]:
    """Launch options by Python name (``headless`` is left to the test runner's own flag)."""
    opts = options_of(spec)
    items: list[tuple[str, Value]] = []
    channel = channel_of(spec.browser_name)
    if channel:
        items.append(("channel", channel))
    if opts.slow_mo is not None:
        items.append(("slow_mo", opts.slow_mo))
    if opts.args:
        items.append(("args", list(opts.args)))
    if opts.firefox_user_prefs:
        items.append(("firefox_user_prefs", dict(opts.firefox_user_prefs)))
    if opts.proxy is not None:
        proxy: dict[str, Value] = {"server": opts.proxy.server}
        for name in ("bypass", "username", "password"):
            value = getattr(opts.proxy, name)
            if value is not None:
                proxy[name] = value
        items.append(("proxy", proxy))
    items.extend(opts.launch_extra.items())
    return items


def context_items(spec: AutomationSpec) -> list[tuple[str, Value]]:
    """Context options by Python name; ``storage_state`` and ``record_video`` are handled by the caller."""
    opts = options_of(spec)
    items: list[tuple[str, Value]] = [(name, getattr(opts, name)) for name in _CONTEXT_SCALARS if getattr(opts, name) is not None]
    if opts.geolocation is not None:
        items.append(("geolocation", opts.geolocation.model_dump(exclude_none=True)))
    if opts.permissions:
        items.append(("permissions", list(opts.permissions)))
    if opts.extra_http_headers:
        items.append(("extra_http_headers", dict(opts.extra_http_headers)))
    if opts.http_credentials is not None:
        credentials: dict[str, Value] = {"username": opts.http_credentials.username, "password": opts.http_credentials.password}
        if opts.http_credentials.origin:
            credentials["origin"] = opts.http_credentials.origin
        items.append(("http_credentials", credentials))
    items.extend(opts.context_extra.items())
    return items


def literal(value: Value, *, string: Callable[[str], str], secret: Callable[[str], str], python: bool, keys: Callable[[str], str] = str) -> str:
    """``value`` as a Python or JavaScript literal; a Secret becomes ``secret(name)``; dict keys go through ``keys``."""
    if isinstance(value, Secret):
        return secret(value.secret)
    if isinstance(value, bool):
        return ("True" if value else "False") if python else ("true" if value else "false")
    if value is None:
        return "None" if python else "null"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        return string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(literal(item, string=string, secret=secret, python=python, keys=keys) for item in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(f"{string(keys(str(name)))}: {literal(item, string=string, secret=secret, python=python)}" for name, item in value.items())
        return "{" + inner + "}"
    return string(str(value))
