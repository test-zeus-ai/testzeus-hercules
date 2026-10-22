"""Hardening tests for the Appium manager: iOS buttons, session creation, XML, refresh, prompt."""

import asyncio
import xml.etree.ElementTree as ET

import pytest
from testzeus_hercules.config import get_global_conf
from tests import conftest_mobile as cm
from tests.conftest_mobile import (  # noqa: F401
    FakeDriver,
    clean_manager_state,
    fake_manager,
    mobile_env,
    requires_selenium,
    touch_sequences,
)

pytest_plugins = ["tests.conftest_mobile"]


def _ios_manager(monkeypatch, stake, driver):
    monkeypatch.setenv("DEVICE_OS", "ios")
    manager = cm.AppiumManager(stake_id=stake, _driver_factory=lambda: driver)
    manager.driver = driver
    return manager


# ── iOS hardware buttons use the documented pressButton names ───────────────


def test_ios_hardware_button_names_use_documented_casing(mobile_env, clean_manager_state, monkeypatch) -> None:
    driver = FakeDriver()
    manager = _ios_manager(monkeypatch, "ios-btn", driver)
    try:
        for key in ("volume_up", "volume_down", "home"):
            asyncio.run(manager.press_hardware_key(key))
        asyncio.run(manager.press_home())
        assert driver.scripts == [
            ("mobile: pressButton", ({"name": "volumeUp"},)),
            ("mobile: pressButton", ({"name": "volumeDown"},)),
            ("mobile: pressButton", ({"name": "home"},)),
            ("mobile: pressButton", ({"name": "home"},)),
        ]
    finally:
        manager.shutdown_pool()


def test_ios_unsupported_hardware_key_is_rejected(mobile_env, clean_manager_state, monkeypatch) -> None:
    driver = FakeDriver()
    manager = _ios_manager(monkeypatch, "ios-power", driver)
    try:
        with pytest.raises(ValueError, match="not supported on iOS"):
            asyncio.run(manager.press_hardware_key("power"))
        assert driver.scripts == []
    finally:
        manager.shutdown_pool()


def test_ios_back_taps_navigation_bar_button_and_never_sends_press_button_back(mobile_env, clean_manager_state, monkeypatch) -> None:
    driver = FakeDriver()
    manager = _ios_manager(monkeypatch, "ios-back", driver)
    try:
        asyncio.run(manager.press_back())
        assert driver.finds == [("-ios class chain", "**/XCUIElementTypeNavigationBar[1]/XCUIElementTypeButton[1]")]
        assert driver.element.clicked == 1
        assert driver.scripts == []  # "back" is not a valid pressButton name on iOS
    finally:
        manager.shutdown_pool()


def test_ios_back_falls_back_to_left_edge_swipe(mobile_env, clean_manager_state, monkeypatch) -> None:
    class NoNavBarDriver(FakeDriver):
        def find_element(self, by, value):
            raise RuntimeError("no such element")

    driver = NoNavBarDriver()
    manager = _ios_manager(monkeypatch, "ios-back-swipe", driver)
    try:
        asyncio.run(manager.press_back())
        assert driver.scripts == []
        assert len(driver.swipes) == 1
        start_x, start_y, end_x, end_y, _duration = driver.swipes[0]
        assert start_x <= 2 and end_x > start_x  # starts at the left edge, moves right
        assert start_y == end_y == 960  # vertical middle of the 1920-high fake screen
    finally:
        manager.shutdown_pool()


# ── Android modifiers ride on the key's metastate ───────────────────────────


def test_android_metastate_masks() -> None:
    keymaps = cm.mobile_keymaps
    assert keymaps.android_metastate([113]) == 0x3000  # ctrl
    assert keymaps.android_metastate([59]) == 0x41  # shift
    assert keymaps.android_metastate([113, 59]) == 0x3041
    assert keymaps.android_metastate([]) == 0
    with pytest.raises(ValueError, match="Unknown modifier keycode"):
        keymaps.android_metastate([1])


def test_android_multi_modifier_sequence_sends_combined_metastate(fake_manager) -> None:
    manager, driver = fake_manager
    asyncio.run(manager.press_key_sequence("ctrl+shift+z"))
    assert driver.keycodes == [54]
    assert driver.metastates == [0x3041]


# ── W3C gestures (real selenium action classes) ─────────────────────────────


@requires_selenium
def test_perform_tap_and_long_press_send_w3c_touch_actions(fake_manager) -> None:
    manager, driver = fake_manager
    asyncio.run(manager.perform_tap(5, 6))
    asyncio.run(manager.long_press_element("ok", duration_ms=700))
    assert touch_sequences(driver) == [
        [("pointerMove", 5, 6), ("pointerDown",), ("pause", 0), ("pointerUp",)],
        [("pointerMove", 60, 40), ("pointerDown",), ("pause", 700), ("pointerUp",)],
    ]


# ── create_session: serialized, no leaked sessionless driver ────────────────


def test_concurrent_create_session_opens_a_single_session(mobile_env, clean_manager_state, tmp_path) -> None:
    created = []

    def factory():
        driver = FakeDriver(session_id=f"s{len(created)}")
        created.append(driver)
        return driver

    manager = cm.AppiumManager(stake_id="race", _driver_factory=factory)
    manager._video_dir = str(tmp_path / "v")
    manager._screenshots_dir = str(tmp_path / "s")
    manager._record_video = False

    async def go():
        return await asyncio.gather(*[manager.create_session({"platformName": "Android"}) for _ in range(5)])

    try:
        results = asyncio.run(go())
        assert len(created) == 1
        assert sorted(r["reused"] for r in results) == [False, True, True, True, True]
    finally:
        manager.shutdown_pool()


def test_driver_without_session_id_is_quit_and_not_kept(mobile_env, clean_manager_state, tmp_path) -> None:
    driver = FakeDriver(session_id="")
    manager = cm.AppiumManager(stake_id="nosid", _driver_factory=lambda: driver)
    manager._video_dir = str(tmp_path / "v")
    manager._screenshots_dir = str(tmp_path / "s")
    try:
        with pytest.raises(cm.appium_manager_module.MobileEnvironmentError, match="session id"):
            asyncio.run(manager.create_session({"platformName": "Android"}))
        assert driver.quit_called == 1
        assert manager.driver is None
    finally:
        manager.shutdown_pool()


# ── accessibility XML parsing ───────────────────────────────────────────────


def test_page_source_with_dtd_or_entities_is_rejected() -> None:
    bomb = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><hierarchy><node text="&b;"/></hierarchy>'
    tree = cm.appium_manager_module.parse_accessibility_tree(bomb, "android")
    assert tree["node_count"] == 0
    assert "DTD" in tree["error"]


def test_oversized_page_source_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(cm.appium_manager_module, "MAX_PAGE_SOURCE_CHARS", 50)
    tree = cm.appium_manager_module.parse_accessibility_tree("<hierarchy>" + "<node/>" * 20 + "</hierarchy>", "android")
    assert tree["node_count"] == 0
    assert "too large" in tree["error"]


def test_normal_page_source_still_parses() -> None:
    tree = cm.appium_manager_module.parse_accessibility_tree(FakeDriver.PAGE_SOURCE, "android")
    assert tree["node_count"] >= 2 and "error" not in tree
    assert isinstance(cm.appium_manager_module._parse_page_source("<a/>"), ET.Element)


# ── read_screen refresh ─────────────────────────────────────────────────────


def test_read_screen_cache_and_refresh(mobile_env, clean_manager_state) -> None:
    driver = cm.CountingSourceDriver()
    manager = cm.AppiumManager(stake_id="refresh", _driver_factory=lambda: driver)
    manager.driver = driver
    try:
        asyncio.run(manager.read_screen())
        asyncio.run(manager.read_screen())
        assert driver.page_source_fetches == 1  # cached
        asyncio.run(manager.read_screen(refresh=True))
        assert driver.page_source_fetches == 2  # bypassed the cache
        asyncio.run(manager.read_screen())
        assert driver.page_source_fetches == 2  # and refreshed the cache
    finally:
        manager.shutdown_pool()


def test_read_mobile_screen_tool_refresh_flag(fake_manager) -> None:
    manager, driver = fake_manager
    counting = cm.CountingSourceDriver()
    manager.driver = counting
    asyncio.run(cm.mobile_tools.read_mobile_screen())
    asyncio.run(cm.mobile_tools.read_mobile_screen())
    assert counting.page_source_fetches == 1
    asyncio.run(cm.mobile_tools.read_mobile_screen(refresh=True))
    assert counting.page_source_fetches == 2
    manager.driver = driver


# ── prompt mentions UI assertions only when enabled ─────────────────────────


def test_prompt_omits_ui_assertion_tools_by_default(monkeypatch) -> None:
    from testzeus_hercules.core.mobile import prompts

    monkeypatch.setitem(get_global_conf().get_config(), "ENABLE_UI_ASSERTIONS", "false")
    assert prompts.get_mobile_nav_system_prompt() == prompts.MOBILE_NAV_SYSTEM_PROMPT
    assert "assert_ui" not in prompts.MOBILE_NAV_SYSTEM_PROMPT
    assert "capture_ui_value" not in prompts.MOBILE_NAV_SYSTEM_PROMPT


def test_prompt_mentions_ui_assertion_tools_when_enabled(monkeypatch) -> None:
    from testzeus_hercules.core.mobile import prompts

    monkeypatch.setitem(get_global_conf().get_config(), "ENABLE_UI_ASSERTIONS", "true")
    prompt = prompts.get_mobile_nav_system_prompt()
    assert "assert_ui" in prompt and "capture_ui_value" in prompt
    assert prompt.endswith("Available Test Data: $basic_test_information")  # placeholder still last
    assert "##TERMINATE TASK##" in prompt


def test_mobile_agent_prompt_follows_the_flag(monkeypatch) -> None:
    from testzeus_hercules.core.agents import mobile_nav_agent

    captured = {}

    def fake_init(self, model_config, llm_config_params, system_prompt, agent_name=None, agent_prompt=None):
        captured["prompt"] = agent_prompt

    monkeypatch.setattr(mobile_nav_agent.MultimodalBaseNavAgent, "__init__", fake_init)
    monkeypatch.setitem(get_global_conf().get_config(), "ENABLE_UI_ASSERTIONS", "false")
    mobile_nav_agent.MobileNavAgent({}, {}, None)
    assert "assert_ui" not in captured["prompt"]
    monkeypatch.setitem(get_global_conf().get_config(), "ENABLE_UI_ASSERTIONS", "true")
    mobile_nav_agent.MobileNavAgent({}, {}, None)
    assert "assert_ui" in captured["prompt"]
