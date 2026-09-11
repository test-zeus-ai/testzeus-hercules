"""Offline tests for the pure capability builder and device settings."""

import pytest
from tests import conftest_mobile as cm

pytest_plugins = ["tests.conftest_mobile"]

# ── Android base capabilities ───────────────────────────────────────────────


def test_android_base_capabilities() -> None:
    caps = cm.mobile_capabilities.build_capabilities("android", device_name="emulator-5554")
    assert caps["platformName"] == "Android"
    assert caps["deviceName"] == "emulator-5554"
    assert caps["automationName"] == "UiAutomator2"
    assert caps["autoGrantPermissions"] is True
    assert caps["noReset"] is False
    assert caps["newCommandTimeout"] == 300
    assert caps["settings[waitForIdleTimeout]"] == 0


def test_platform_names_are_normalized() -> None:
    for name in ("android", "ANDROID", "Android"):
        caps = cm.mobile_capabilities.build_capabilities(name, device_name="d")
        assert caps["platformName"] == "Android"
    for name in ("ios", "iOS", "IOS", "iosx"):
        caps = cm.mobile_capabilities.build_capabilities(name, device_name="d")
        assert caps["platformName"] == "iOS"


def test_automation_name_override() -> None:
    caps = cm.mobile_capabilities.build_capabilities("android", device_name="d", automation_name="Espresso")
    assert caps["automationName"] == "Espresso"
    caps_ios = cm.mobile_capabilities.build_capabilities("ios", device_name="d")
    assert caps_ios["automationName"] == "XCUITest"


# ── iOS base capabilities (pure: no environment probing) ────────────────────


def test_ios_base_capabilities() -> None:
    caps = cm.mobile_capabilities.build_capabilities("ios", device_name="iPhone Simulator")
    assert caps["platformName"] == "iOS"
    assert caps["deviceName"] == "iPhone Simulator"
    assert caps["automationName"] == "XCUITest"
    assert caps["useNewWDA"] is True
    assert caps["wdaLaunchTimeout"] == 120000
    assert caps["wdaConnectionTimeout"] == 120000
    assert caps["noReset"] is True
    assert caps["fullReset"] is False
    assert caps["preventWDAAttachments"] is True
    assert caps["clearSystemFiles"] is True
    assert "platformVersion" not in caps  # no probing: only set when provided


def test_optional_keys_included_when_provided() -> None:
    caps = cm.mobile_capabilities.build_capabilities(
        "android",
        device_name="d",
        app="/tmp/app.apk",
        platform_version="14",
        udid="emulator-5556",
    )
    assert caps["app"] == "/tmp/app.apk"
    assert caps["platformVersion"] == "14"
    assert caps["udid"] == "emulator-5556"


def test_extra_capabilities_win_last() -> None:
    caps = cm.mobile_capabilities.build_capabilities(
        "android",
        device_name="d",
        extra={"automationName": "Espresso", "noReset": True, "appium:customOption": "x"},
    )
    assert caps["automationName"] == "Espresso"
    assert caps["noReset"] is True
    assert caps["appium:customOption"] == "x"
    assert caps["autoGrantPermissions"] is True  # untouched key survives


def test_extra_none_is_safe() -> None:
    caps = cm.mobile_capabilities.build_capabilities("android", device_name="d", extra=None)
    assert caps["platformName"] == "Android"


# ── APPIUM_CAPABILITIES JSON parsing (settings) ─────────────────────────────


def test_appium_capabilities_unset(monkeypatch) -> None:
    monkeypatch.delenv("APPIUM_CAPABILITIES", raising=False)
    assert cm.mobile_settings.get_appium_capabilities() == {}


def test_appium_capabilities_valid_json(monkeypatch) -> None:
    monkeypatch.setenv("APPIUM_CAPABILITIES", '{"noReset": true, "appActivity": ".Main"}')
    assert cm.mobile_settings.get_appium_capabilities() == {"noReset": True, "appActivity": ".Main"}


def test_appium_capabilities_malformed_json_degrades_to_empty(monkeypatch) -> None:
    monkeypatch.setenv("APPIUM_CAPABILITIES", "{not json")
    assert cm.mobile_settings.get_appium_capabilities() == {}


def test_appium_capabilities_non_object_degrades_to_empty(monkeypatch) -> None:
    monkeypatch.setenv("APPIUM_CAPABILITIES", "[1, 2, 3]")
    assert cm.mobile_settings.get_appium_capabilities() == {}


# ── settings getters and defaults ───────────────────────────────────────────


def test_server_url_defaults(monkeypatch) -> None:
    monkeypatch.delenv("APPIUM_SERVER_URL", raising=False)
    monkeypatch.delenv("APPIUM_SERVER_PORT", raising=False)
    assert cm.mobile_settings.get_appium_server_url() == "http://127.0.0.1:4723"
    assert cm.mobile_settings.get_appium_server_port() == 4723


def test_server_url_overrides(monkeypatch) -> None:
    monkeypatch.setenv("APPIUM_SERVER_URL", "http://appium:4444")
    assert cm.mobile_settings.get_appium_server_url() == "http://appium:4444"
    monkeypatch.delenv("APPIUM_SERVER_URL", raising=False)
    monkeypatch.setenv("APPIUM_SERVER_PORT", "5555")
    assert cm.mobile_settings.get_appium_server_url() == "http://127.0.0.1:5555"


def test_android_platform_defaults(monkeypatch) -> None:
    for var in ("DEVICE_OS", "PLATFORM_NAME", "DEVICE_NAME", "AUTOMATION_NAME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEVICE_OS", "android")
    assert cm.mobile_settings.get_device_os() == "android"
    assert cm.mobile_settings.get_platform_name() == "Android"
    assert cm.mobile_settings.get_device_name() == "emulator-5554"
    assert cm.mobile_settings.get_automation_name() == "UiAutomator2"


def test_ios_platform_defaults(monkeypatch) -> None:
    for var in ("PLATFORM_NAME", "DEVICE_NAME", "AUTOMATION_NAME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEVICE_OS", "ios")
    assert cm.mobile_settings.get_device_os() == "ios"
    assert cm.mobile_settings.get_platform_name() == "iOS"
    assert cm.mobile_settings.get_device_name() == "iPhone Simulator"
    assert cm.mobile_settings.get_automation_name() == "XCUITest"


def test_optional_settings(monkeypatch) -> None:
    for var in ("APP_PATH", "APPIUM_APK_PATH", "APPIUM_DEVICE_UUID", "APPIUM_PLATFORM_VERSION"):
        monkeypatch.delenv(var, raising=False)
    assert cm.mobile_settings.get_app_path() is None
    assert cm.mobile_settings.get_apk_path() is None
    assert cm.mobile_settings.get_device_uuid() is None
    assert cm.mobile_settings.get_platform_version() is None

    monkeypatch.setenv("APPIUM_DEVICE_UUID", "emulator-5556")
    monkeypatch.setenv("APPIUM_PLATFORM_VERSION", "15")
    assert cm.mobile_settings.get_device_uuid() == "emulator-5556"
    assert cm.mobile_settings.get_platform_version() == "15"


def test_device_manager_default_and_value(monkeypatch) -> None:
    monkeypatch.delenv("DEVICE_MANAGER", raising=False)
    assert cm.mobile_settings.get_device_manager() == "playwright"
    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    assert cm.mobile_settings.get_device_manager() == "appium"


def test_manager_build_default_capabilities_uses_settings(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    monkeypatch.setenv("DEVICE_OS", "android")
    monkeypatch.setenv("DEVICE_NAME", "emulator-5556")
    monkeypatch.setenv("APP_PATH", "/tmp/app.apk")
    monkeypatch.setenv("APPIUM_DEVICE_UUID", "emulator-5556")
    monkeypatch.setenv("APPIUM_CAPABILITIES", '{"newCommandTimeout": 999}')
    # Avoid touching the real proof path when constructing the manager.
    monkeypatch.setattr(
        cm.AppiumManager,
        "_resolve_proof_path",
        lambda self: str(tmp_path / "proofs"),
        raising=False,
    )
    manager = cm.AppiumManager(stake_id="captest")
    try:
        caps = manager.build_default_capabilities()
    finally:
        cm.AppiumManager.close_instance("captest")
    assert caps["deviceName"] == "emulator-5556"
    assert caps["app"] == "/tmp/app.apk"
    assert caps["udid"] == "emulator-5556"
    assert caps["automationName"] == "UiAutomator2"
    assert caps["newCommandTimeout"] == 999  # APPIUM_CAPABILITIES override won


def test_manager_build_default_capabilities_apk_alias(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    monkeypatch.delenv("APP_PATH", raising=False)
    monkeypatch.setenv("APPIUM_APK_PATH", "/tmp/pkg.apk")
    monkeypatch.setattr(
        cm.AppiumManager,
        "_resolve_proof_path",
        lambda self: str(tmp_path / "proofs"),
        raising=False,
    )
    manager = cm.AppiumManager(stake_id="captest2")
    try:
        caps = manager.build_default_capabilities()
    finally:
        cm.AppiumManager.close_instance("captest2")
    assert caps["app"] == "/tmp/pkg.apk"
