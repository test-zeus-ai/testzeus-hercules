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
APPIUM_SERVER_URL            Appium server base URL (remote server)        derived from port
APPIUM_SERVER_PORT           Appium server port                            ``4723``
PLATFORM_NAME                Capability ``platformName``                   ``Android`` / ``iOS``
DEVICE_NAME                  Capability ``deviceName``                     ``emulator-5554`` /
                                                                           ``iPhone Simulator``
AUTOMATION_NAME              Capability ``automationName``                 ``UiAutomator2`` /
                                                                           ``XCUITest``
APP_PATH                     Path/URL of the app under test                None
APPIUM_APK_PATH              Android app package path (alias of APP_PATH)  None
APPIUM_DEVICE_UUID           Capability ``udid``                           None
APPIUM_PLATFORM_VERSION      Capability ``platformVersion``                None
APPIUM_CAPABILITIES          JSON object merged over the base capabilities ``{}`` (on parse
                                                                           error)
ENABLE_UI_ASSERTIONS         ``true`` adds ``assert_ui`` / ``capture_ui_value``  ``false``
                             guidance to the mobile agent prompt
===========================  ============================================  ======================
"""

import json
import os
from typing import Optional

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.logger import logger

DEFAULT_APPIUM_SERVER_PORT = 4723


def _setting(key: str) -> str:
    """Return the stripped value for ``key`` ("" when unset).

    The global config is consulted first (config file / programmatic keys),
    then the live process environment. Both are read on every call, so the
    result never depends on import order or on when ``.env`` was loaded.
    """
    value = None
    try:
        value = get_global_conf().get_config().get(key)
    except Exception:  # pragma: no cover - defensive: config must never break mobile settings
        value = None
    if value is None or not str(value).strip():
        value = os.environ.get(key)
    return str(value).strip() if value is not None else ""


def get_device_manager() -> str:
    """Return the active device manager: ``appium`` or ``playwright`` (default)."""
    return _setting("DEVICE_MANAGER").lower() or "playwright"


def get_device_os() -> str:
    """Return the target device OS: ``android`` (default) or ``ios``."""
    value = (_setting("DEVICE_OS") or "android").lower()
    return "ios" if value.startswith("ios") else "android"


def get_appium_server_port() -> int:
    """Return the Appium server port (default 4723)."""
    raw = _setting("APPIUM_SERVER_PORT")
    try:
        return int(raw) if raw else DEFAULT_APPIUM_SERVER_PORT
    except ValueError:
        logger.warning("Invalid APPIUM_SERVER_PORT %r; falling back to %d", raw, DEFAULT_APPIUM_SERVER_PORT)
        return DEFAULT_APPIUM_SERVER_PORT


def get_appium_server_url() -> str:
    """Return the Appium server URL, defaulting to ``http://127.0.0.1:<port>``."""
    url = _setting("APPIUM_SERVER_URL")
    return url or f"http://127.0.0.1:{get_appium_server_port()}"


def get_platform_name() -> str:
    """Return the ``platformName`` capability (``Android``/``iOS``)."""
    value = _setting("PLATFORM_NAME")
    if value:
        return value
    return "iOS" if get_device_os() == "ios" else "Android"


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
    """Return extra user capabilities parsed from ``APPIUM_CAPABILITIES`` JSON.

    A malformed payload degrades gracefully to ``{}`` (logged) so a bad env
    var can never break session creation entirely.
    """
    raw = _setting("APPIUM_CAPABILITIES")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse APPIUM_CAPABILITIES as JSON: %s", e)
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
    return _setting("ENABLE_UI_ASSERTIONS").lower() in {"1", "true", "yes", "on"}
