"""How the browser is set up for a web case: Playwright's launch and context options, by Playwright's own names.

An environment (or a case's ``environment``) picks the ``browser`` and may add ``browser_options``. The common options
are typed fields; any other option Playwright's ``BrowserType.launch`` or ``Browser.new_context`` takes goes in
``launch_extra`` / ``context_extra`` and is checked against Playwright's own parameter names. The same options reach
local runs, the exported Playwright code and cloud runs.

Secrets (a header value, HTTP credentials, a proxy password) are named environment variables, never values, and the
auth state (``storage_state``) is a JSON file inside the project, never inline. Options that write files to arbitrary
places (``executable_path``, ``downloads_path``, ``record_har_path``, ...) are not accepted; ``record_video`` keeps the
video with the run's evidence.
"""

from __future__ import annotations

import functools
import re
from typing import Annotated, Any, Literal, Union
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator

__all__ = [
    "BROWSERS",
    "BrowserName",
    "BrowserOptions",
    "Geolocation",
    "HttpCredentials",
    "ProxySettings",
    "Secret",
    "engine_of",
    "channel_of",
    "launch_parameters",
    "context_parameters",
]

SECRET_NAME_PATTERN = r"^[A-Z_][A-Z0-9_]*$"

# chromium: Playwright's own build; chrome / msedge: the installed Google Chrome / Microsoft Edge (Chromium channels).
BrowserName = Literal["chromium", "chrome", "msedge", "firefox", "webkit"]
BROWSERS: tuple[str, ...] = ("chromium", "chrome", "msedge", "firefox", "webkit")
_ENGINE = {"chromium": "chromium", "chrome": "chromium", "msedge": "chromium", "firefox": "firefox", "webkit": "webkit"}

Permission = Literal[
    "geolocation",
    "midi",
    "midi-sysex",
    "notifications",
    "camera",
    "microphone",
    "background-sync",
    "ambient-light-sensor",
    "accelerometer",
    "gyroscope",
    "magnetometer",
    "accessibility-events",
    "clipboard-read",
    "clipboard-write",
    "payment-handler",
    "storage-access",
]

# Typed below, set by Hercules itself, or able to write files anywhere: never accepted in the *_extra maps.
_TYPED_LAUNCH = frozenset({"headless", "slow_mo", "args", "proxy", "firefox_user_prefs", "channel"})
_TYPED_CONTEXT = frozenset(
    {
        "locale",
        "timezone_id",
        "geolocation",
        "permissions",
        "color_scheme",
        "reduced_motion",
        "forced_colors",
        "user_agent",
        "extra_http_headers",
        "http_credentials",
        "ignore_https_errors",
        "java_script_enabled",
        "bypass_csp",
        "offline",
        "accept_downloads",
        "service_workers",
        "device_scale_factor",
        "is_mobile",
        "has_touch",
        "storage_state",
        "proxy",
    }
)
_HERCULES_OWNS = frozenset({"self", "base_url", "viewport", "no_viewport", "default_browser_type", "env", "executable_path"})
_WRITES_FILES = frozenset(
    {"downloads_path", "traces_dir", "record_har_path", "record_video_dir", "client_certificates", "record_har_content", "record_har_mode", "record_har_omit_content", "record_har_url_filter"}
)
_SIGNALS = frozenset({"handle_sigint", "handle_sigterm", "handle_sighup"})


def engine_of(browser: str | None) -> str:
    """The Playwright browser type that runs ``browser``: chromium, firefox or webkit."""
    return _ENGINE.get(browser or "chromium", "chromium")


def channel_of(browser: str | None) -> str | None:
    """The Chromium channel for an installed Chrome or Edge, else None."""
    return browser if browser in ("chrome", "msedge") else None


@functools.lru_cache(maxsize=1)
def launch_parameters() -> frozenset[str]:
    """Playwright's own ``BrowserType.launch`` parameter names that ``launch_extra`` may set."""
    import inspect

    from playwright.async_api import BrowserType

    return frozenset(inspect.signature(BrowserType.launch).parameters) - _TYPED_LAUNCH - _HERCULES_OWNS - _WRITES_FILES - _SIGNALS


@functools.lru_cache(maxsize=1)
def context_parameters() -> frozenset[str]:
    """Playwright's own ``Browser.new_context`` parameter names that ``context_extra`` may set."""
    import inspect

    from playwright.async_api import Browser

    return frozenset(inspect.signature(Browser.new_context).parameters) - _TYPED_CONTEXT - _HERCULES_OWNS - _WRITES_FILES


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Secret(_Model):
    """A value read from the named environment variable at run time; never the value itself."""

    secret: Annotated[str, StringConstraints(pattern=SECRET_NAME_PATTERN)]


Text = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
TextOrSecret = Union[Text, Secret]


class Geolocation(_Model):
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    accuracy: Annotated[float, Field(ge=0)] | None = None


class HttpCredentials(_Model):
    """HTTP authentication (basic or digest) the browser answers with."""

    username: TextOrSecret
    password: Secret = Field(description='{"secret": "ENV_VAR"}: the variable that holds the password')
    origin: Annotated[str, StringConstraints(pattern=r"^https?://[^\s/?#]+$")] | None = Field(default=None, description="send them only to this origin")


class ProxySettings(_Model):
    server: Annotated[str, StringConstraints(pattern=r"^(https?|socks[45])://[^\s/?#]+$")] = Field(description="e.g. http://proxy.example.com:3128 or socks5://127.0.0.1:1080")
    bypass: Annotated[str, StringConstraints(max_length=2000)] | None = Field(default=None, description="comma-separated hosts that skip the proxy, e.g. .example.com,localhost")
    username: TextOrSecret | None = None
    password: Secret | None = None


_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}$")
_ARG = re.compile(r"^-{1,2}[A-Za-z0-9]")


class BrowserOptions(_Model):
    """Playwright launch and context options for a web run (Playwright's names)."""

    # --- launch
    headless: bool | None = Field(default=None, description="false opens a visible window (the run's --headed always does)")
    slow_mo: Annotated[int, Field(ge=0, le=10000)] | None = Field(default=None, description="milliseconds Playwright waits after each action")
    args: list[Annotated[str, StringConstraints(min_length=2, max_length=500)]] = Field(default_factory=list, max_length=50, description="browser command-line switches, e.g. --lang=de")
    firefox_user_prefs: dict[str, Union[bool, int, float, Annotated[str, StringConstraints(max_length=500)]]] = Field(default_factory=dict, description="Firefox only: about:config preferences")
    proxy: ProxySettings | None = None
    # --- context
    locale: Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")] | None = Field(default=None, description="e.g. en-GB, de-DE")
    timezone_id: str | None = Field(default=None, description="an IANA time zone, e.g. Europe/Berlin")
    geolocation: Geolocation | None = None
    permissions: list[Permission] = Field(default_factory=list, description="permissions granted to every page, e.g. geolocation, notifications, clipboard-read")
    color_scheme: Literal["light", "dark", "no-preference"] | None = None
    reduced_motion: Literal["reduce", "no-preference"] | None = None
    forced_colors: Literal["active", "none"] | None = None
    user_agent: Annotated[str, StringConstraints(min_length=1, max_length=1000)] | None = None
    extra_http_headers: dict[str, TextOrSecret] = Field(default_factory=dict, description='headers sent with every request; a value may be {"secret": "ENV_VAR"}')
    http_credentials: HttpCredentials | None = None
    ignore_https_errors: bool | None = Field(default=None, description="accept self-signed and invalid certificates")
    java_script_enabled: bool | None = None
    bypass_csp: bool | None = None
    offline: bool | None = None
    accept_downloads: bool | None = None
    service_workers: Literal["allow", "block"] | None = None
    device_scale_factor: Annotated[float, Field(gt=0, le=10)] | None = None
    is_mobile: bool | None = Field(default=None, description="Chromium and WebKit only")
    has_touch: bool | None = None
    storage_state: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = Field(
        default=None, description="auth state: a JSON file inside the project (cookies and local storage), as saved by Playwright's storage_state()"
    )
    record_video: bool | None = Field(default=None, description="record a video of the run into its evidence folder (on unless set to false)")
    # --- any other Playwright option, by its Python name
    launch_extra: dict[str, JsonValue] = Field(default_factory=dict, description='other BrowserType.launch options, e.g. {"timeout": 60000, "ignore_default_args": ["--mute-audio"]}')
    context_extra: dict[str, JsonValue] = Field(default_factory=dict, description='other Browser.new_context options, e.g. {"screen": {"width": 1920, "height": 1080}, "strict_selectors": true}')

    @field_validator("args")
    @classmethod
    def _switches(cls, value: list[str]) -> list[str]:
        bad = [arg for arg in value if not _ARG.match(arg)]
        if bad:
            raise ValueError(f"browser args are command-line switches starting with - or --; not {bad[0]!r}")
        return value

    @field_validator("timezone_id")
    @classmethod
    def _zone(cls, value: str | None) -> str | None:
        if value is not None and value not in available_timezones():
            raise ValueError(f"unknown time zone {value!r}: use an IANA name such as Europe/Berlin or America/New_York")
        return value

    @field_validator("extra_http_headers")
    @classmethod
    def _headers(cls, value: dict[str, Any]) -> dict[str, Any]:
        bad = [name for name in value if not _HEADER_NAME.match(name)]
        if bad:
            raise ValueError(f"{bad[0]!r} is not a valid header name")
        return value

    @field_validator("storage_state")
    @classmethod
    def _project_file(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = value.replace("\\", "/").split("/")
        if value.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", value) or ".." in parts:
            raise ValueError("storage_state must be a JSON file inside the project (a relative path without '..'), e.g. .hercules/auth/admin.json")
        if not value.lower().endswith(".json"):
            raise ValueError("storage_state must be a .json file (Playwright's storage_state() output)")
        return value

    @field_validator("launch_extra")
    @classmethod
    def _launch_names(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _known(value, launch_parameters(), "launch_extra", "BrowserType.launch", _TYPED_LAUNCH)

    @field_validator("context_extra")
    @classmethod
    def _context_names(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _known(value, context_parameters(), "context_extra", "Browser.new_context", _TYPED_CONTEXT)

    def check_browser(self, browser: str | None) -> None:
        """Raise ValueError when an option does not work in ``browser``."""
        engine = engine_of(browser)
        if self.firefox_user_prefs and engine != "firefox":
            raise ValueError("firefox_user_prefs is for Firefox only (browser: firefox)")
        if self.is_mobile and engine == "firefox":
            raise ValueError("is_mobile is not supported in Firefox: use chromium or webkit")

    def secret_names(self) -> list[str]:
        """The environment variables these options read."""
        found: list[str] = []

        def walk(node: object) -> None:
            if isinstance(node, Secret):
                found.append(node.secret)
            elif isinstance(node, BaseModel):
                for name in type(node).model_fields:
                    walk(getattr(node, name))
            elif isinstance(node, dict):
                for item in node.values():
                    walk(item)

        walk(self)
        return list(dict.fromkeys(found))


def _known(value: dict[str, Any], allowed: frozenset[str], field: str, where: str, typed: frozenset[str]) -> dict[str, Any]:
    for name in value:
        if name in typed:
            raise ValueError(f"{field}: set {name} with its own field in browser_options")
        if name in _WRITES_FILES or name in ("executable_path", "env"):
            raise ValueError(f"{field}: {name} is not accepted (it writes files or runs programs outside the run); use record_video for a video")
        if name not in allowed:
            close = sorted(allowed, key=lambda option: _distance(option, name))[:1]
            hint = f"; did you mean {close[0]!r}?" if close and _distance(close[0], name) <= 3 else ""
            raise ValueError(f"{field}: {name!r} is not a {where} option{hint} (Playwright's Python names, e.g. ignore_default_args)")
    return value


def _distance(left: str, right: str) -> int:
    import difflib

    return round((1 - difflib.SequenceMatcher(None, left, right).ratio()) * 10)
