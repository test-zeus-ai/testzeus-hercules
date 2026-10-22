"""Pure Appium capability builder.

``build_capabilities`` is side-effect free: no environment probing, no I/O.
The Android and iOS base blocks hold only stable, platform-agnostic defaults
for unattended runs: ``noReset`` is ``False`` on both platforms (the Appium
default, i.e. a clean app state per session), no automatic permission grants
and no obsolete or heavyweight iOS options. Anything else (``autoGrantPermissions``,
``startIWDP``, ...) is opt-in through ``extra``, which is applied last so
user-supplied ``APPIUM_CAPABILITIES`` always win.
"""

from typing import Any, Optional


def _normalize_platform(platform: str) -> str:
    value = (platform or "").strip().lower()
    if value.startswith("ios"):
        return "ios"
    return "android"


def build_capabilities(
    platform: str,
    *,
    device_name: str,
    automation_name: str = "",
    app: Optional[str] = None,
    platform_version: Optional[str] = None,
    udid: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    """Build a W3C Appium capabilities dict for the requested platform.

    Args:
        platform: ``android`` or ``ios`` (case-insensitive).
        device_name: ``deviceName`` capability.
        automation_name: ``automationName`` capability; falls back to
            ``UiAutomator2`` (Android) / ``XCUITest`` (iOS).
        app: Optional path/URL of the app under test.
        platform_version: Optional ``platformVersion`` capability.
        udid: Optional device/simulator unique id.
        extra: Optional dict merged last (user overrides win).

    Returns:
        A plain dict of capabilities. No environment probing happens here.
    """
    normalized = _normalize_platform(platform)

    if normalized == "ios":
        desired_caps: dict[str, Any] = {
            "platformName": "iOS",
            "deviceName": device_name,
            "automationName": automation_name or "XCUITest",
            "useNewWDA": True,
            "wdaLaunchTimeout": 120000,
            "wdaConnectionTimeout": 120000,
            "shouldUseSingletonTestManager": False,
            "showXcodeLog": True,
            "includeSafariInWebviews": True,
            "maxTypingFrequency": 60,
            "fullReset": False,
            "noReset": False,
            "webviewConnectTimeout": 90000,
            "preventWDAAttachments": True,
            "clearSystemFiles": True,
        }
    else:
        desired_caps = {
            "platformName": "Android",
            "deviceName": device_name,
            "automationName": automation_name or "UiAutomator2",
            "noReset": False,
            "newCommandTimeout": 300,
            "settings[waitForIdleTimeout]": 0,
        }

    if platform_version:
        desired_caps["platformVersion"] = str(platform_version)
    if udid:
        desired_caps["udid"] = str(udid)
    if app:
        desired_caps["app"] = str(app)

    if extra:
        desired_caps.update(extra)

    return desired_caps
