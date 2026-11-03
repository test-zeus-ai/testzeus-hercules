"""Device-automation settings for mobile sessions via Appium.

Values are resolved lazily, at call time, through the global configuration
(``testzeus_hercules.config.get_global_conf()``, which also loads ``.env``)
with the process environment as fallback. Keys therefore work from the
environment, a ``.env`` file or a config file (``device_manager: appium`` in a
``--config`` YAML/JSON), and nothing is frozen at import time. Mobile
automation stays fully optional: nothing here requires the Appium client to be
importable and no keys were added to the global config defaults.

Environment variables
---------------------

===========================  ============================================  ======================
Variable                     Purpose                                       Default
===========================  ============================================  ======================
DEVICE_MANAGER               ``appium`` enables mobile automation          ``playwright``
DEVICE_OS                    Target OS: ``android`` or ``ios``             ``android``
                             (case-insensitive; ``ios*`` prefix accepted)
APPIUM_SERVER_URL            Appium server base URL (``http://`` is        derived from port
                             prefixed when the scheme is missing)
APPIUM_SERVER_PORT           Appium server port (1-65535)                  ``4723``
APPIUM_COMMAND_TIMEOUT_SECONDS        Max wait for one Appium command      ``120``
APPIUM_SESSION_START_TIMEOUT_SECONDS  Max wait for session creation        ``180``
DEVICE_NAME                  Capability ``deviceName``                     ``emulator-5554`` /
                                                                           ``iPhone Simulator``
AUTOMATION_NAME              Capability ``automationName``                 ``UiAutomator2`` /
                                                                           ``XCUITest``
APP_PATH                     Path/URL of the app under test                None
APPIUM_APK_PATH              Android app package path (``APP_PATH`` wins   None
                             when both are set)
APPIUM_DEVICE_UUID           Capability ``udid``                           None
APPIUM_PLATFORM_VERSION      Capability ``platformVersion``                None
APPIUM_CAPABILITIES          JSON object (or a mapping in a ``--config``   ``{}`` (on parse
                             file) merged over the base capabilities       error)
ENABLE_UI_ASSERTIONS         ``true`` adds ``assert_ui`` / ``capture_ui_value``  ``false``
                             guidance to the mobile agent prompt
===========================  ============================================  ======================

The ``platformName`` capability is always derived from ``DEVICE_OS`` (a
``platformName`` inside ``APPIUM_CAPABILITIES`` that disagrees is ignored with a
warning, see :func:`reconcile_platform_name`). The former
``PLATFORM_NAME`` knob is deprecated and ignored (a warning is logged when it is
set).
"""

import ast
import json
import os
from typing import Optional

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.logger import logger

DEFAULT_APPIUM_SERVER_PORT = 4723
DEFAULT_COMMAND_TIMEOUT_SECONDS = 120.0
DEFAULT_SESSION_START_TIMEOUT_SECONDS = 180.0

_warned_messages: set = set()


def _warn_once(message: str, *args: object) -> None:
    """Log a warning once per distinct formatted message (settings are read on every call)."""
    key = message % args if args else message
    if key in _warned_messages:
        return
    _warned_messages.add(key)
    logger.warning(key)


def _raw_setting(key: str) -> object:
    """Return the raw value for ``key`` (config first, then environment), or None."""
    value = None
    try:
        value = get_global_conf().get_config().get(key)
    except Exception:  # pragma: no cover - defensive: config must never break mobile settings
        value = None
    if value is None or (isinstance(value, str) and not value.strip()):
        value = os.environ.get(key)
    return value


def _setting(key: str) -> str:
    """Return the stripped value for ``key`` ("" when unset).

    The global config is consulted first (config file / programmatic keys),
    then the live process environment. Both are read on every call, so the
    result never depends on import order or on when ``.env`` was loaded.
    """
    value = _raw_setting(key)
    return str(value).strip() if value is not None else ""


def get_device_manager() -> str:
    """Return the active device manager: ``appium`` or ``playwright`` (default)."""
    return _setting("DEVICE_MANAGER").lower() or "playwright"


def _device_os_is_valid(value: str) -> bool:
    return value == "android" or value.startswith("ios")


def get_device_os() -> str:
    """Return the target device OS: ``android`` (default) or ``ios``.

    ``ios*`` prefixes (``iOS``, ``ios17``...) keep selecting iOS. An unknown
    value still resolves to ``android`` here (this getter never raises) but is
    logged, and :func:`validate_mobile_settings` turns it into a hard error
    when a session is started.
    """
    value = (_setting("DEVICE_OS") or "android").lower()
    if not _device_os_is_valid(value):
        _warn_once("Invalid DEVICE_OS %r; expected 'android' or 'ios'. Falling back to android.", value)
    return "ios" if value.startswith("ios") else "android"


def get_appium_server_port() -> int:
    """Return the Appium server port (default 4723)."""
    raw = _setting("APPIUM_SERVER_PORT")
    if not raw:
        return DEFAULT_APPIUM_SERVER_PORT
    try:
        port = int(raw)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        _warn_once("Invalid APPIUM_SERVER_PORT %r (must be an integer 1-65535); falling back to %d", raw, DEFAULT_APPIUM_SERVER_PORT)
        return DEFAULT_APPIUM_SERVER_PORT
    return port


def get_appium_server_url() -> str:
    """Return the Appium server URL, defaulting to ``http://127.0.0.1:<port>``.

    A configured URL without a scheme (``host:4723``) gets ``http://``
    prefixed: the HTTP client cannot use a scheme-less address and the failure
    it raises is obscure.
    """
    url = _setting("APPIUM_SERVER_URL")
    if not url:
        return f"http://127.0.0.1:{get_appium_server_port()}"
    if "://" not in url:
        _warn_once("APPIUM_SERVER_URL %r has no scheme; using http://%s", url, url)
        return f"http://{url}"
    return url


def _positive_float_setting(key: str, default: float) -> float:
    raw = _setting(key)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not value > 0 or value == float("inf"):
        _warn_once("Invalid %s %r (must be a positive number of seconds); using %s", key, raw, default)
        return default
    return value


def get_command_timeout_seconds() -> float:
    """Return how long one Appium driver command may take (``APPIUM_COMMAND_TIMEOUT_SECONDS``, default 120)."""
    return _positive_float_setting("APPIUM_COMMAND_TIMEOUT_SECONDS", DEFAULT_COMMAND_TIMEOUT_SECONDS)


def get_session_start_timeout_seconds() -> float:
    """Return how long session creation may take (``APPIUM_SESSION_START_TIMEOUT_SECONDS``, default 180)."""
    return _positive_float_setting("APPIUM_SESSION_START_TIMEOUT_SECONDS", DEFAULT_SESSION_START_TIMEOUT_SECONDS)


def validate_mobile_settings() -> "list[str]":
    """Return human-readable problems with the mobile configuration (empty when valid)."""
    problems = []
    device_os = (_setting("DEVICE_OS") or "android").lower()
    if not _device_os_is_valid(device_os):
        problems.append(f"DEVICE_OS={device_os!r} is not valid; use 'android' or 'ios'")
    raw_port = _setting("APPIUM_SERVER_PORT")
    if raw_port:
        try:
            port_ok = 1 <= int(raw_port) <= 65535
        except ValueError:
            port_ok = False
        if not port_ok:
            problems.append(f"APPIUM_SERVER_PORT={raw_port!r} is not valid; use an integer from 1 to 65535")
    return problems


def get_platform_name() -> str:
    """Return the ``platformName`` capability (``Android``/``iOS``), derived from ``DEVICE_OS``.

    ``PLATFORM_NAME`` used to be documented as this capability but never
    reached the session (``DEVICE_OS`` decides the platform). It is now
    deprecated and ignored (see :func:`warn_if_platform_name_set`).
    """
    return "iOS" if get_device_os() == "ios" else "Android"


def warn_if_platform_name_set() -> None:
    """Log a deprecation warning when the ignored ``PLATFORM_NAME`` setting is present."""
    value = _setting("PLATFORM_NAME")
    if value:
        logger.warning("PLATFORM_NAME=%r is deprecated and ignored; the platform is taken from DEVICE_OS (%s).", value, get_device_os())


def reconcile_platform_name(capabilities: dict) -> dict:
    """Return ``capabilities`` with ``platformName`` forced to the ``DEVICE_OS``-derived value.

    ``APPIUM_CAPABILITIES`` is merged last, so a ``platformName`` in it would
    silently contradict ``DEVICE_OS`` (Hercules' element lookup, gestures and
    prompt follow ``DEVICE_OS``). A disagreeing value is dropped with a warning;
    one that agrees (case-insensitive) is left alone.
    """
    expected = get_platform_name()
    for key in ("platformName", "appium:platformName"):
        if key in capabilities and str(capabilities[key]).strip().lower() != expected.lower():
            logger.warning("APPIUM_CAPABILITIES %s=%r conflicts with DEVICE_OS (%s); keeping platformName=%s", key, capabilities[key], get_device_os(), expected)
            capabilities = {**capabilities, key: expected}
    return capabilities


def get_device_name() -> str:
    """Return the ``deviceName`` capability."""
    value = _setting("DEVICE_NAME")
    if value:
        return value
    return "iPhone Simulator" if get_device_os() == "ios" else "emulator-5554"


def get_automation_name() -> str:
    """Return the ``automationName`` capability (``UiAutomator2``/``XCUITest``)."""
    value = _setting("AUTOMATION_NAME")
    if value:
        return value
    return "XCUITest" if get_device_os() == "ios" else "UiAutomator2"


def get_app_path() -> Optional[str]:
    """Return the app-under-test path/URL from ``APP_PATH`` (may be None)."""
    return _setting("APP_PATH") or None


def get_apk_path() -> Optional[str]:
    """Return the Android app path from ``APPIUM_APK_PATH`` (may be None)."""
    return _setting("APPIUM_APK_PATH") or None


def get_device_uuid() -> Optional[str]:
    """Return the ``udid`` capability (may be None)."""
    return _setting("APPIUM_DEVICE_UUID") or None


def get_platform_version() -> Optional[str]:
    """Return the ``platformVersion`` capability (may be None)."""
    return _setting("APPIUM_PLATFORM_VERSION") or None


def get_appium_capabilities() -> dict:
    """Return extra user capabilities from ``APPIUM_CAPABILITIES``.

    Accepts a mapping (programmatic config) as-is, and a string as a JSON
    object or, failing that, a Python literal mapping: a mapping written in a
    ``--config`` YAML/JSON file reaches the global config as ``str(dict)``
    (``True``/``None``/single quotes), which ``ast.literal_eval`` parses safely.
    A malformed payload degrades gracefully to ``{}`` (logged) so a bad value
    can never break session creation entirely.
    """
    raw = _raw_setting("APPIUM_CAPABILITIES")
    if isinstance(raw, dict):
        return dict(raw)
    raw = str(raw).strip() if raw is not None else ""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as json_error:
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            logger.error("Failed to parse APPIUM_CAPABILITIES as a JSON object or mapping literal: %s", json_error)
            return {}
    if not isinstance(parsed, dict):
        logger.error("APPIUM_CAPABILITIES must be a JSON object; got %s. Ignoring.", type(parsed).__name__)
        return {}
    return parsed


def ui_assertions_enabled() -> bool:
    """Return True when the opt-in ``assert_ui``/``capture_ui_value`` tools are enabled.

    The tools themselves are gated by ``ENABLE_UI_ASSERTIONS`` in the API
    verification layer; the mobile prompt only mentions them when the same flag
    is on, so the agent is never told about tools it does not have.
    """
    try:
        return bool(get_global_conf().get_enable_ui_assertions())
    except Exception:  # pragma: no cover - defensive: config must never break the mobile prompt
        return False
