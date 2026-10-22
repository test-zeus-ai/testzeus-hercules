"""Regression tests for the PR #160 review fixes (scroll direction, capability
payloads, iOS page sources, element lookup fallbacks, iOS back/modifiers,
APPIUM_CAPABILITIES parsing, PLATFORM_NAME deprecation)."""

import asyncio
import importlib.util

import pytest
from tests import conftest_mobile as cm
from tests.conftest_mobile import (  # noqa: F401
    FakeDriver,
    clean_manager_state,
    fake_manager,
    mobile_env,
)

pytest_plugins = ["tests.conftest_mobile"]

requires_appium = pytest.mark.skipif(importlib.util.find_spec("appium") is None, reason="appium client (mobile extra) not installed")

am = cm.appium_manager_module


def _run(coro):
    return asyncio.run(coro)


def _ios_manager(monkeypatch, stake, driver):
    monkeypatch.setenv("DEVICE_OS", "ios")
    manager = cm.AppiumManager(stake_id=stake, _driver_factory=lambda: driver)
    manager.driver = driver
    return manager


# ── 1. scroll direction matches the tool descriptions ───────────────────────


def test_scroll_down_drags_finger_upward_and_scroll_up_downward(fake_manager) -> None:
    manager, driver = fake_manager
    _run(manager.scroll_down())
    _run(manager.scroll_up())
    (down_sx, down_sy, down_ex, down_ey, _), (up_sx, up_sy, up_ex, up_ey, _) = driver.swipes
    # 1080x1920 screen: scroll_down reveals content below -> finger 0.7h -> 0.3h.
    assert (down_sx, down_sy, down_ex, down_ey) == (540, 1344, 540, 576)
    # scroll_up reveals content above -> finger 0.3h -> 0.7h.
    assert (up_sx, up_sy, up_ex, up_ey) == (540, 576, 540, 1344)


# ── 2. capability values reach the server unchanged ─────────────────────────


@requires_appium
def test_create_session_passes_structured_capabilities_through_real_appium_options(monkeypatch, clean_manager_state, tmp_path) -> None:
    import appium.webdriver as appium_webdriver

    captured = {}

    class _Driver:
        session_id = "s-real-opts"

        def quit(self) -> None:
            pass

    def fake_remote(command_executor=None, options=None):
        captured["caps"] = options.to_capabilities()
        return _Driver()

    monkeypatch.setattr(appium_webdriver, "Remote", fake_remote)
    monkeypatch.setattr(cm.AppiumManager, "_resolve_proof_path", lambda self: str(tmp_path), raising=False)
    manager = cm.AppiumManager(stake_id="real-opts")
    manager._record_video = False
    caps = {
        "platformName": "iOS",
        "processArguments": {"args": ["-x"], "env": {"A": "1"}},
        "otherApps": ["/a.app", "/b.app"],
        "permissions": '{"com.x": {"camera": "yes"}}',
        "noReset": False,
        "newCommandTimeout": 300,
        "app": None,
    }
    try:
        _run(manager.create_session(caps))
    finally:
        cm.AppiumManager.close_instance("real-opts")
    sent = captured["caps"]
    assert sent["appium:processArguments"] == {"args": ["-x"], "env": {"A": "1"}}
    assert sent["appium:otherApps"] == ["/a.app", "/b.app"]
    assert sent["appium:permissions"] == '{"com.x": {"camera": "yes"}}'
    assert sent["appium:noReset"] is False
    assert sent["appium:newCommandTimeout"] == 300
    assert "appium:app" not in sent  # None unsets instead of becoming "None"


# ── 3. iOS page sources: name -> id, bounds from x/y/width/height ───────────

IOS_PAGE_SOURCE = (
    '<?xml version="1.0" encoding="UTF-8"?><AppiumAUT>'
    '<XCUIElementTypeApplication type="XCUIElementTypeApplication" name="App" label="App" enabled="true" visible="true" x="0" y="0" width="390" height="844">'
    '<XCUIElementTypeButton type="XCUIElementTypeButton" name="login_btn" label="Log in" enabled="true" visible="true" x="20" y="700" width="350" height="44"/>'
    '<XCUIElementTypeTextField type="XCUIElementTypeTextField" name="email_field" label="" value="Email" enabled="true" x="20" y="300" width="350" height="44"/>'
    "</XCUIElementTypeApplication></AppiumAUT>"
)


def test_ios_page_source_maps_name_to_id_and_builds_bounds() -> None:
    tree = am.parse_accessibility_tree(IOS_PAGE_SOURCE, "ios")
    button = next(node for node in tree["nodes"] if node["type"] == "Button")
    assert button["id"] == "login_btn"
    assert button["label"] == "Log in"  # visible text stays the label
    assert button["bounds"] == "[20,700][370,744]"
    field = next(node for node in tree["nodes"] if node["type"] == "TextField")
    assert field["id"] == "email_field"
    assert field["value"] == "Email"
    assert field["input"] is True
    assert field["bounds"] == "[20,300][370,344]"
    legend = am.render_screen_legend(tree)
    assert 'Button "Log in" id=login_btn [clickable] @[20,700][370,744]' in legend


# ── 4. element lookup fallbacks and escaping ────────────────────────────────


class _SelectiveDriver(FakeDriver):
    """Finds an element only for one (strategy, selector); raises otherwise."""

    def __init__(self, hit) -> None:
        super().__init__()
        self.hit = hit

    def find_element(self, by, value):
        self.finds.append((by, value))
        if (by, value) == self.hit:
            return self.element
        raise RuntimeError("no such element")


def test_android_lookup_order() -> None:
    assert am.element_locators("com.example:id/ok", "android") == [
        ("accessibility id", "com.example:id/ok"),
        ("id", "com.example:id/ok"),
        ("-android uiautomator", 'new UiSelector().text("com.example:id/ok")'),
    ]


def test_ios_lookup_order() -> None:
    assert am.element_locators("Log in", "ios") == [
        ("accessibility id", "Log in"),
        ("-ios predicate string", 'label == "Log in"'),
    ]


def test_selector_literals_escape_quotes_and_backslashes() -> None:
    raw = 'say "hi" \\ now'
    assert am.element_locators(raw, "android")[2] == ("-android uiautomator", 'new UiSelector().text("say \\"hi\\" \\\\ now")')
    assert am.element_locators(raw, "ios")[1] == ("-ios predicate string", 'label == "say \\"hi\\" \\\\ now"')
    # The accessibility id and resource-id are sent verbatim (no escaping).
    assert am.element_locators(raw, "android")[0] == ("accessibility id", raw)


@pytest.mark.parametrize(
    ("identifier", "hit"),
    [
        ("OK", ("accessibility id", "OK")),
        ("ok", ("id", "ok")),  # bare resource-id
        ("com.example:id/ok", ("id", "com.example:id/ok")),  # package-qualified
        ("Sign in", ("-android uiautomator", 'new UiSelector().text("Sign in")')),
    ],
)
def test_android_click_falls_back_through_strategies(mobile_env, clean_manager_state, monkeypatch, identifier, hit) -> None:
    monkeypatch.setenv("DEVICE_OS", "android")
    driver = _SelectiveDriver(hit)
    manager = cm.AppiumManager(stake_id="lookup-a", _driver_factory=lambda: driver)
    manager.driver = driver
    manager._take_screenshots = False
    try:
        _run(manager.click_element(identifier))
        assert driver.finds[-1] == hit
        assert driver.element.clicked == 1
    finally:
        manager.shutdown_pool()


def test_ios_enter_text_falls_back_to_label_predicate(mobile_env, clean_manager_state, monkeypatch) -> None:
    driver = _SelectiveDriver(("-ios predicate string", 'label == "Email"'))
    manager = _ios_manager(monkeypatch, "lookup-i", driver)
    manager._take_screenshots = False
    try:
        _run(manager.enter_text("Email", "a@b.c"))
        assert driver.finds == [("accessibility id", "Email"), ("-ios predicate string", 'label == "Email"')]
        assert driver.element.sent_keys == ["a@b.c"]
    finally:
        manager.shutdown_pool()


def test_lookup_reports_not_found_after_all_strategies(mobile_env, clean_manager_state, monkeypatch) -> None:
    monkeypatch.setenv("DEVICE_OS", "android")
    driver = _SelectiveDriver(None)
    manager = cm.AppiumManager(stake_id="lookup-miss", _driver_factory=lambda: driver)
    manager.driver = driver
    manager._take_screenshots = False
    try:
        with pytest.raises(am.MobileEnvironmentError, match="No element found"):
            _run(manager.clear_text("nope"))
        assert len(driver.finds) == 3
    finally:
        manager.shutdown_pool()


def test_prompt_documents_exactly_the_supported_lookups() -> None:
    from testzeus_hercules.core.mobile.prompts import MOBILE_NAV_SYSTEM_PROMPT

    assert "resource-id" in MOBILE_NAV_SYSTEM_PROMPT
    assert "visible text" in MOBILE_NAV_SYSTEM_PROMPT
    assert "visible label" in MOBILE_NAV_SYSTEM_PROMPT
    assert "identified by their resource-id / content-desc / text" not in MOBILE_NAV_SYSTEM_PROMPT


# ── 5/6. iOS capability defaults ────────────────────────────────────────────


def test_ios_defaults_are_minimal_with_command_timeout() -> None:
    caps = cm.mobile_capabilities.build_capabilities("ios", device_name="d")
    assert caps["newCommandTimeout"] == 300
    for key in ("useNewWDA", "clearSystemFiles", "preventWDAAttachments"):
        assert key not in caps
    opted_in = cm.mobile_capabilities.build_capabilities("ios", device_name="d", extra={"useNewWDA": True})
    assert opted_in["useNewWDA"] is True


# ── 7. PLATFORM_NAME is deprecated and ignored ──────────────────────────────


def test_platform_name_is_ignored_with_deprecation_warning(monkeypatch, clean_manager_state, tmp_path) -> None:
    warnings = []
    monkeypatch.setattr(cm.mobile_settings.logger, "warning", lambda msg, *args: warnings.append(msg % args))
    monkeypatch.setenv("DEVICE_OS", "android")
    monkeypatch.setenv("PLATFORM_NAME", "iOS")
    monkeypatch.setattr(cm.AppiumManager, "_resolve_proof_path", lambda self: str(tmp_path), raising=False)
    manager = cm.AppiumManager(stake_id="pn")
    try:
        caps = manager.build_default_capabilities()
    finally:
        cm.AppiumManager.close_instance("pn")
    assert caps["platformName"] == "Android"
    assert cm.mobile_settings.get_platform_name() == "Android"
    assert any("PLATFORM_NAME" in w and "deprecated" in w for w in warnings)


def test_no_platform_name_warning_when_unset(monkeypatch) -> None:
    warnings = []
    monkeypatch.setattr(cm.mobile_settings.logger, "warning", lambda msg, *args: warnings.append(msg % args))
    monkeypatch.delenv("PLATFORM_NAME", raising=False)
    cm.mobile_settings.warn_if_platform_name_set()
    assert warnings == []


# ── 8. iOS key sequences with modifiers are rejected ────────────────────────


def test_ios_key_sequence_with_modifier_is_rejected(mobile_env, clean_manager_state, monkeypatch) -> None:
    sent = cm.install_stub_selenium(monkeypatch)
    driver = FakeDriver()
    manager = _ios_manager(monkeypatch, "ios-mod", driver)
    try:
        with pytest.raises(ValueError, match="Modifier key combinations"):
            _run(manager.press_key_sequence("Control+A,Enter"))
        assert sent == []  # nothing was pressed
        _run(manager.press_key_sequence("A,Enter"))  # unmodified keys still work
        assert len(sent) == 2
    finally:
        manager.shutdown_pool()


# ── 9. APPIUM_CAPABILITIES from a config file mapping ───────────────────────


class _Conf:
    def __init__(self, config: dict) -> None:
        self._config = config

    def get_config(self) -> dict:
        return self._config


def test_appium_capabilities_accepts_mapping_value(monkeypatch) -> None:
    mapping = {"processArguments": {"args": ["-x"]}, "noReset": True}
    monkeypatch.setattr(cm.mobile_settings, "get_global_conf", lambda: _Conf({"APPIUM_CAPABILITIES": mapping}))
    assert cm.mobile_settings.get_appium_capabilities() == mapping


def test_appium_capabilities_accepts_str_of_mapping_from_config_file(monkeypatch) -> None:
    # A YAML/JSON --config mapping reaches the global config as str(dict).
    mapping = {"processArguments": {"args": ["-x"]}, "noReset": True, "app": None}
    monkeypatch.setattr(cm.mobile_settings, "get_global_conf", lambda: _Conf({"APPIUM_CAPABILITIES": str(mapping)}))
    assert cm.mobile_settings.get_appium_capabilities() == mapping


def test_appium_capabilities_unparseable_literal_degrades_to_empty(monkeypatch) -> None:
    monkeypatch.setattr(cm.mobile_settings, "get_global_conf", lambda: _Conf({"APPIUM_CAPABILITIES": "{'a': __import__('os')}"}))
    assert cm.mobile_settings.get_appium_capabilities() == {}


# ── 10. iOS back only taps a real back button ───────────────────────────────


def test_ios_back_class_chain_targets_back_buttons_only() -> None:
    chain = am.IOS_BACK_BUTTON_CLASS_CHAIN
    assert "XCUIElementTypeButton[1]" not in chain  # never "whatever the first button is"
    assert 'name == "Back"' in chain and 'label == "Back"' in chain
    assert 'name CONTAINS "Back"' in chain and 'name CONTAINS "back"' in chain
    assert 'NOT (label IN {"Edit", "Cancel", "Close"})' in chain


def test_ios_back_without_back_button_swipes_and_taps_nothing(mobile_env, clean_manager_state, monkeypatch) -> None:
    driver = _SelectiveDriver(None)  # e.g. nav bar only has an Edit button
    manager = _ios_manager(monkeypatch, "ios-back-none", driver)
    try:
        _run(manager.press_back())
        assert driver.finds == [("-ios class chain", am.IOS_BACK_BUTTON_CLASS_CHAIN)]
        assert driver.element.clicked == 0
        assert len(driver.swipes) == 1
    finally:
        manager.shutdown_pool()
