"""Reading a device screen (page source → elements) and resolving where a native app runs (egress, files, capabilities)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from testzeus_hercules.cases.models import MobileApp
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.mobile.screen import parse, xpath_literal
from testzeus_hercules.mobile.target import TargetError, capabilities, resolve_target

ANDROID = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <android.widget.FrameLayout class="android.widget.FrameLayout" package="com.android.settings" text="" clickable="false" displayed="true" bounds="[0,0][1080,2400]">
    <android.widget.ImageButton class="android.widget.ImageButton" content-desc="Navigate up" clickable="true" enabled="true" displayed="true" bounds="[0,128][144,272]" />
    <android.widget.LinearLayout class="android.widget.LinearLayout" clickable="true" enabled="true" displayed="true" bounds="[0,588][1080,787]">
      <android.widget.TextView class="android.widget.TextView" resource-id="android:id/title" text="Internet" clickable="false" displayed="true" bounds="[185,629][360,697]" />
      <android.widget.TextView class="android.widget.TextView" resource-id="android:id/summary" text="Wi-Fi &amp; mobile" clickable="false" displayed="true" bounds="[185,697][420,746]" />
    </android.widget.LinearLayout>
    <android.widget.LinearLayout class="android.widget.LinearLayout" clickable="true" enabled="true" displayed="true" bounds="[0,986][1080,1136]">
      <android.widget.TextView class="android.widget.TextView" text="Airplane mode" clickable="false" displayed="true" bounds="[185,1027][514,1095]" />
      <android.widget.Switch class="android.widget.Switch" resource-id="android:id/switch_widget" checkable="true" checked="true" clickable="false" displayed="true" bounds="[905,999][1039,1122]" />
    </android.widget.LinearLayout>
    <android.widget.EditText class="android.widget.EditText" resource-id="com.example:id/pin" text="4321" password="true" clickable="true" displayed="true" bounds="[0,1200][1080,1300]" />
    <android.widget.TextView class="android.widget.TextView" text="Hidden" displayed="false" bounds="[0,0][10,10]" />
    <android.widget.TextView class="android.widget.TextView" text="It's &quot;on&quot;" displayed="true" bounds="[0,1400][500,1450]" />
  </android.widget.FrameLayout>
</hierarchy>"""

IOS = """<?xml version="1.0" encoding="UTF-8"?>
<XCUIElementTypeApplication type="XCUIElementTypeApplication" name="Settings" label="Settings" enabled="true" visible="true" x="0" y="0" width="393" height="852">
  <XCUIElementTypeNavigationBar type="XCUIElementTypeNavigationBar" name="Settings" enabled="true" visible="true" x="0" y="47" width="393" height="96"/>
  <XCUIElementTypeCell type="XCUIElementTypeCell" name="com.apple.settings.wifi" label="Wi-Fi" enabled="true" visible="true" accessible="true" x="16" y="300" width="361" height="44"/>
  <XCUIElementTypeSwitch type="XCUIElementTypeSwitch" name="Airplane Mode" label="Airplane Mode" value="0" enabled="true" visible="true" x="320" y="250" width="51" height="31"/>
  <XCUIElementTypeSecureTextField type="XCUIElementTypeSecureTextField" name="Passcode" value="••••" enabled="true" visible="true" x="16" y="400" width="361" height="44"/>
  <XCUIElementTypeStaticText type="XCUIElementTypeStaticText" name="Off screen" label="Off screen" enabled="true" visible="false" x="0" y="900" width="10" height="10"/>
</XCUIElementTypeApplication>"""


def test_android_page_source_becomes_numbered_elements() -> None:
    screen = parse(ANDROID, "android")
    lines = screen.text().splitlines()
    assert lines[0].startswith('[1] button "Navigate up" | clickable')
    # A settings row is a clickable container named after the texts inside it; its switch takes the row's name.
    row = next(element for element in screen.elements if element.name == "Internet · Wi-Fi & mobile")
    assert row.role == "button" and row.clickable and row.center == (540, 687)
    switch = next(element for element in screen.elements if element.role == "switch")
    assert switch.name == "Airplane mode" and switch.checked is True
    assert screen.app == "com.android.settings"
    assert not screen.shows("Hidden") and screen.shows("internet") and screen.shows('It\'s "on"')


def test_password_fields_never_show_their_value() -> None:
    screen = parse(ANDROID, "android")
    pin = next(element for element in screen.elements if element.secure)
    assert "4321" not in screen.text() and pin.name == "••••" and "password" in pin.line()
    assert all("4321" not in value for _, value in pin.locators)


def test_locators_prefer_labels_and_skip_shared_ids() -> None:
    screen = parse(ANDROID, "android")
    up = screen.element(1)
    assert up is not None and up.locators[0] == ("accessibility id", "Navigate up")
    internet = next(element for element in screen.elements if element.name == "Internet")
    assert ("id", "android:id/title") not in internet.locators and internet.locators[0] == ("xpath", "//*[@text='Internet']")
    assert xpath_literal('It\'s "on"') == "concat('It', \"'\", 's \"on\"')"


def test_ios_page_source() -> None:
    screen = parse(IOS, "ios")
    assert screen.app == "Settings"
    wifi = next(element for element in screen.elements if element.name == "Wi-Fi")
    assert wifi.role == "listitem" and wifi.locators[0] == ("accessibility id", "com.apple.settings.wifi")
    switch = next(element for element in screen.elements if element.role == "switch")
    assert switch.checked is False and switch.name == "Airplane Mode"
    passcode = next(element for element in screen.elements if element.secure)
    assert passcode.name == "Passcode" and "••••" not in passcode.line()
    assert not screen.shows("Off screen")


def test_an_ios_back_button_reads_as_going_back() -> None:
    # A navigation bar's back button is labelled with the previous screen's title, which reads as a link to it.
    source = (
        '<XCUIElementTypeApplication type="XCUIElementTypeApplication" name="Settings" label="Settings" x="0" y="0" width="402" height="874">'
        '<XCUIElementTypeButton type="XCUIElementTypeButton" name="BackButton" label="General" visible="true" accessible="true" x="16" y="62" width="44" height="44"/>'
        '<XCUIElementTypeButton type="XCUIElementTypeButton" name="com.apple.settings.general" label="General" visible="true" accessible="true" x="16" y="380" width="370" height="53"/>'
        "</XCUIElementTypeApplication>"
    )
    back, row = parse(source, "ios").elements
    assert back.name == "Back (General)" and back.locators[0] == ("accessibility id", "BackButton")
    assert row.name == "General"


def test_an_empty_or_broken_page_source_reads_as_no_elements() -> None:
    assert parse("", "android").elements == ()
    assert "no elements" in parse("<hierarchy><broken", "android").text()


# ── targets ──────────────────────────────────────────────────────────────────


def settings(tmp_path: Path, policy: str = "hybrid", **config: str) -> Settings:
    env = {"HERCULES_HOME": str(tmp_path / "home"), "HERCULES_EGRESS": policy, **config}
    return Settings.load(tmp_path, env=env)


APP = MobileApp(platform="android", package="com.android.settings", activity=".Settings", device="emulator-5554")


def test_a_local_appium_server_is_allowed_under_every_policy(tmp_path: Path) -> None:
    target = resolve_target(APP, settings(tmp_path, "local"), {}, tmp_path)
    assert target.provider == "local" and target.is_local and target.hub_url == "http://127.0.0.1:4723" and target.auth is None
    caps = capabilities(target)
    assert caps["platformName"] == "Android" and caps["appium:automationName"] == "UiAutomator2"
    assert caps["appium:appPackage"] == "com.android.settings" and caps["appium:appActivity"] == ".Settings" and caps["appium:deviceName"] == "emulator-5554"


def test_a_local_ios_simulator_runs_without_a_window(tmp_path: Path) -> None:
    ios = MobileApp(platform="ios", bundle_id="com.apple.Preferences", device="iPhone 17")
    local = capabilities(resolve_target(ios, settings(tmp_path), {}, tmp_path))
    assert local["appium:isHeadless"] is True and local["appium:wdaLaunchTimeout"] == 120_000
    shown = ios.model_copy(update={"capabilities": {"appium:isHeadless": False}})
    assert capabilities(resolve_target(shown, settings(tmp_path), {}, tmp_path))["appium:isHeadless"] is False
    cloud = MobileApp(platform="ios", app="bs://abc", cloud="browserstack")
    assert "appium:isHeadless" not in capabilities(resolve_target(cloud, settings(tmp_path), {"BROWSERSTACK_USERNAME": "u", "BROWSERSTACK_ACCESS_KEY": "k"}, tmp_path))


def test_a_remote_appium_server_needs_an_egress_policy_that_allows_it(tmp_path: Path) -> None:
    remote = APP.model_copy(update={"appium_url": "http://10.1.2.3:4723/wd/hub"})
    with pytest.raises(TargetError, match="egress policy 'local' forbids the remote Appium server"):
        resolve_target(remote, settings(tmp_path, "local"), {}, tmp_path)
    assert not resolve_target(remote, settings(tmp_path, "hybrid-text"), {}, tmp_path).is_local
    listed = tmp_path / "listed"
    (listed / ".hercules").mkdir(parents=True)
    (listed / ".hercules" / "config.yaml").write_text("egress:\n  policy: local\n  local_hosts: [10.1.2.3]\n")
    assert resolve_target(remote, Settings.load(listed, env={"HERCULES_HOME": str(tmp_path / "home")}), {}, listed).is_local


def test_device_clouds_are_remote_and_need_credentials_from_named_variables(tmp_path: Path) -> None:
    cloud = MobileApp(platform="ios", app="bs://abc", cloud="browserstack", device="iPhone 15", platform_version="17")
    creds = {"BROWSERSTACK_USERNAME": "user-7f3a", "BROWSERSTACK_ACCESS_KEY": "key-9c1e"}
    with pytest.raises(TargetError, match="egress policy 'local' forbids the device cloud browserstack"):
        resolve_target(cloud, settings(tmp_path, "local"), creds, tmp_path)
    with pytest.raises(TargetError, match="BROWSERSTACK_ACCESS_KEY") as missing:
        resolve_target(cloud, settings(tmp_path), {"BROWSERSTACK_USERNAME": "user-7f3a"}, tmp_path)
    assert "user-7f3a" not in str(missing.value)
    target = resolve_target(cloud, settings(tmp_path), creds, tmp_path)
    assert target.hub_url == "https://hub-cloud.browserstack.com/wd/hub" and target.auth == ("user-7f3a", "key-9c1e")
    assert "key-9c1e" not in repr(target) and "user-7f3a" not in target.label
    caps = capabilities(target, run_name="case r1")
    assert caps["bstack:options"] == {"projectName": "Hercules", "buildName": "hercules", "sessionName": "case r1", "deviceName": "iPhone 15", "osVersion": "17"}
    assert caps["appium:app"] == "bs://abc" and "appium:deviceName" not in caps and caps["appium:automationName"] == "XCUITest"
    assert "key-9c1e" not in str(caps) and "user-7f3a" not in str(caps)


def test_sauce_labs_regions_are_known_data_centers_only(tmp_path: Path) -> None:
    cloud = MobileApp(platform="android", app="storage:f00d", cloud="saucelabs", device="Google Pixel 7 Emulator", platform_version="13")
    env = {"SAUCE_USERNAME": "u", "SAUCE_ACCESS_KEY": "k"}
    target = resolve_target(cloud, settings(tmp_path), env, tmp_path)
    assert target.hub_url == "https://ondemand.us-west-1.saucelabs.com:443/wd/hub" and target.api_url == "https://api.us-west-1.saucelabs.com"
    caps = capabilities(target)
    assert caps["appium:deviceName"] == "Google Pixel 7 Emulator" and caps["sauce:options"]["build"] == "hercules"
    (tmp_path / ".hercules").mkdir()
    (tmp_path / ".hercules" / "config.yaml").write_text("mobile:\n  saucelabs:\n    region: evil-example\n")
    with pytest.raises(TargetError, match="region must be one of"):
        resolve_target(cloud, Settings.load(tmp_path, env={"HERCULES_HOME": str(tmp_path / "home")}), env, tmp_path)


def test_app_files_must_be_inside_the_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "apps").mkdir(parents=True)
    (project / "apps" / "demo.apk").write_bytes(b"PK")
    (project / "apps" / "Demo.app").mkdir()
    outside = tmp_path / "outside.apk"
    outside.write_bytes(b"PK")
    os.symlink(outside, project / "apps" / "link.apk")
    s = settings(project)
    target = resolve_target(MobileApp(platform="android", app="apps/demo.apk"), s, {}, project)
    assert target.app_file == (project / "apps" / "demo.apk").resolve() and capabilities(target)["appium:app"] == str(target.app_file)
    assert resolve_target(MobileApp(platform="ios", app="apps/Demo.app"), s, {}, project).app_file is not None
    with pytest.raises(TargetError, match="outside the project"):
        resolve_target(MobileApp(platform="android", app="apps/link.apk"), s, {}, project)
    with pytest.raises(TargetError, match="was not found"):
        resolve_target(MobileApp(platform="android", app="apps/missing.apk"), s, {}, project)


def test_extra_capabilities_merge_into_vendor_options(tmp_path: Path) -> None:
    cloud = MobileApp(platform="android", app="bs://abc", cloud="browserstack", capabilities={"bstack:options": {"networkLogs": True}, "appium:language": "fr"})
    target = resolve_target(cloud, settings(tmp_path), {"BROWSERSTACK_USERNAME": "u", "BROWSERSTACK_ACCESS_KEY": "k"}, tmp_path)
    caps = capabilities(target)
    assert caps["bstack:options"]["networkLogs"] is True and caps["bstack:options"]["projectName"] == "Hercules" and caps["appium:language"] == "fr"
