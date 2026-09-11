"""Offline tests for the mobile toolset: success strings, graceful failures, validation."""

import asyncio
import os

import pytest
from tests import conftest_mobile as cm
from tests.conftest_mobile import (  # noqa: F401
    FakeDriver,
    clean_manager_state,
    fake_manager,
    mobile_env,
)

pytest_plugins = ["tests.conftest_mobile"]

mobile_tools = cm.mobile_tools


def _run(coro):
    return asyncio.run(coro)


# ── helpers ─────────────────────────────────────────────────────────────────


def test_every_tool_name_is_an_async_function() -> None:
    assert len(mobile_tools.MOBILE_TOOLS) == 20
    for name in mobile_tools.MOBILE_TOOLS:
        assert asyncio.iscoroutinefunction(getattr(mobile_tools, name)), name


# ── session lifecycle ───────────────────────────────────────────────────────


def test_start_mobile_session_reuses_injected_driver(fake_manager) -> None:
    manager, driver = fake_manager
    result = _run(mobile_tools.start_mobile_session())

    assert result.startswith("Successfully started mobile session")
    assert driver.session_id in result
    assert manager.driver is driver


def test_start_mobile_session_without_factory_uses_stub_remote(monkeypatch, tmp_path, clean_manager_state) -> None:
    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    monkeypatch.setenv("DEVICE_OS", "android")
    from testzeus_hercules.config import get_global_conf

    monkeypatch.setattr(get_global_conf(), "_default_test_id", "remote-run")

    created = {}

    def remote(*args, **kwargs):
        created["options"] = kwargs.get("options") or (args[1] if len(args) > 1 else None)
        return FakeDriver(session_id="sess-remote-1")

    cm.install_stub_appium(monkeypatch, remote=remote)
    manager = cm.AppiumManager(stake_id="remote-run")  # bound to the current test id, no factory
    manager._video_dir = str(tmp_path / "videos")
    manager._screenshots_dir = str(tmp_path / "screenshots")
    manager._record_video = False

    try:
        result = _run(mobile_tools.start_mobile_session())
        assert result.startswith("Successfully started mobile session")
        assert "sess-remote-1" in result
        assert created["options"].capabilities["platformName"] == "Android"
    finally:
        manager.shutdown_pool()


def test_close_mobile_session(fake_manager) -> None:
    manager, driver = fake_manager
    result = _run(mobile_tools.close_mobile_session())

    assert result.startswith("Successfully closed the mobile session")
    assert driver.quit_called == 1
    assert manager.driver is None


def test_close_mobile_session_without_session(mobile_env, clean_manager_state) -> None:
    result = _run(mobile_tools.close_mobile_session())
    assert result == mobile_tools.NO_SESSION_MESSAGE


# ── element / gesture success paths ─────────────────────────────────────────


def test_tap_within_viewport(fake_manager) -> None:
    _manager, driver = fake_manager
    result = _run(mobile_tools.tap(500, 900))

    assert result.startswith("Successfully performed tap")
    assert driver.touch_actions and driver.touch_actions[0][0] == ("press", 500, 900)


def test_tap_outside_viewport_is_rejected(fake_manager) -> None:
    _manager, driver = fake_manager
    result = _run(mobile_tools.tap(5000, 10))

    assert result.startswith("Error:")
    assert "outside the device viewport" in result
    assert driver.touch_actions == []


def test_tap_negative_coordinate_is_rejected(fake_manager) -> None:
    result = _run(mobile_tools.tap(-1, 10))
    assert result.startswith("Error:")
    assert "outside the device viewport" in result


def test_swipe_validates_all_coordinates(fake_manager) -> None:
    _manager, driver = fake_manager
    result = _run(mobile_tools.swipe(100, 1000, 100, 300, 500))

    assert result.startswith("Successfully performed swipe")
    assert driver.swipes == [(100, 1000, 100, 300, 500)]

    rejected = _run(mobile_tools.swipe(100, 1000, 99999, 300))
    assert rejected.startswith("Error:")
    assert "end_x" in rejected


def test_scroll_up_and_down_use_swipe(fake_manager) -> None:
    _manager, driver = fake_manager
    up = _run(mobile_tools.scroll_up())
    down = _run(mobile_tools.scroll_down())

    assert up.startswith("Successfully scrolled up")
    assert down.startswith("Successfully scrolled down")
    assert len(driver.swipes) == 2
    up_swipe, down_swipe = driver.swipes
    assert up_swipe[1] > up_swipe[3]  # finger moves upward
    assert down_swipe[1] < down_swipe[3]  # finger moves downward
    assert up_swipe[0] == down_swipe[0] == 540  # horizontal center of 1080


def test_long_press_element(fake_manager) -> None:
    _manager, driver = fake_manager
    result = _run(mobile_tools.long_press_element("ok_button", duration_ms=1500))

    assert result.startswith("Successfully long pressed element")
    assert driver.finds == [("accessibility id", "ok_button")]
    assert driver.touch_actions[0][0] == ("long_press", driver.element, 1500)


def test_click_element(fake_manager) -> None:
    _manager, driver = fake_manager
    result = _run(mobile_tools.click_element("ok_button"))

    assert result.startswith("Successfully clicked element")
    assert driver.element.clicked == 1


def test_enter_and_clear_text(fake_manager) -> None:
    _manager, driver = fake_manager
    entered = _run(mobile_tools.enter_text("name_field", "hello"))
    cleared = _run(mobile_tools.clear_text("name_field"))

    assert entered.startswith("Successfully entered text")
    assert cleared.startswith("Successfully cleared text")
    assert driver.element.sent_keys == ["hello"]
    assert driver.element.cleared == 1


# ── key tools ───────────────────────────────────────────────────────────────


def test_key_tools_record_keycodes(fake_manager) -> None:
    _manager, driver = fake_manager
    assert _run(mobile_tools.press_key("enter")).startswith("Successfully pressed key")
    assert _run(mobile_tools.press_hardware_key("home")).startswith("Successfully pressed hardware key")
    assert _run(mobile_tools.press_key_sequence("Shift+Tab,Enter")).startswith("Successfully pressed key sequence")
    assert _run(mobile_tools.press_back_button()).startswith("Successfully pressed the back button")
    assert _run(mobile_tools.press_home_button()).startswith("Successfully pressed the home button")
    assert driver.keycodes == [66, 3, 59, 61, 59, 66, 4, 3]


def test_unknown_key_names_return_error_strings(fake_manager) -> None:
    assert _run(mobile_tools.press_key("warp_drive")).startswith("Error: press_key failed")
    assert _run(mobile_tools.press_hardware_key("warp_drive")).startswith("Error: press_hardware_key failed")
    assert _run(mobile_tools.press_key_sequence("Control+Nope")).startswith("Error: press_key_sequence failed")


# ── reading tools ───────────────────────────────────────────────────────────


def test_read_mobile_screen_renders_legend(fake_manager) -> None:
    result = _run(mobile_tools.read_mobile_screen())

    assert result.startswith("Device screen (android)")
    assert '"OK"' in result
    assert "[input]" in result
    assert "[clickable]" in result


def test_take_mobile_screenshot_writes_file(fake_manager) -> None:
    result = _run(mobile_tools.take_mobile_screenshot("my shot"))

    assert result.startswith("Successfully captured screenshot saved to:")
    screenshot_path = result.rsplit(": ", 1)[1]
    assert os.path.exists(screenshot_path)
    assert os.path.basename(screenshot_path).startswith("my_shot_")


def test_viewport_size(fake_manager) -> None:
    result = _run(mobile_tools.get_mobile_viewport_size())
    assert result == "Device viewport size: 1080x1920 pixels."


def test_recording_tools(fake_manager) -> None:
    _manager, driver = fake_manager
    started = _run(mobile_tools.start_screen_recording())
    assert started == "Successfully started screen recording."
    assert driver.record_started is True

    stopped = _run(mobile_tools.stop_screen_recording())
    assert stopped.startswith("Successfully stopped screen recording. Video saved to: ")
    assert os.path.exists(stopped.rsplit(": ", 1)[1])


# ── graceful no-session behavior (never raises) ─────────────────────────────


@pytest.mark.parametrize(
    "tool_call",
    [
        lambda: mobile_tools.tap(1, 1),
        lambda: mobile_tools.swipe(0, 0, 10, 10),
        lambda: mobile_tools.scroll_up(),
        lambda: mobile_tools.scroll_down(),
        lambda: mobile_tools.long_press_element("x"),
        lambda: mobile_tools.click_element("x"),
        lambda: mobile_tools.enter_text("x", "y"),
        lambda: mobile_tools.clear_text("x"),
        lambda: mobile_tools.press_key("enter"),
        lambda: mobile_tools.press_hardware_key("home"),
        lambda: mobile_tools.press_key_sequence("Control+A"),
        lambda: mobile_tools.press_back_button(),
        lambda: mobile_tools.press_home_button(),
        lambda: mobile_tools.read_mobile_screen(),
        lambda: mobile_tools.take_mobile_screenshot(),
        lambda: mobile_tools.start_screen_recording(),
        lambda: mobile_tools.stop_screen_recording(),
        lambda: mobile_tools.get_mobile_viewport_size(),
    ],
)
def test_tools_without_session_return_graceful_string(mobile_env, clean_manager_state, tool_call) -> None:
    result = _run(tool_call())
    assert result == mobile_tools.NO_SESSION_MESSAGE


def test_tools_without_appium_client_return_install_hint(monkeypatch, clean_manager_state) -> None:
    # appium absent (no stub), DEVICE_MANAGER may be anything
    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    result = _run(mobile_tools.tap(1, 1))
    assert result == mobile_tools.INSTALL_HINT_MESSAGE
    assert "pip install 'testzeus-hercules[mobile]'" in result


# ── proof artifacts are keyed by the current test id ────────────────────────


def test_mobile_artifacts_land_under_current_test_proof_path(mobile_env, clean_manager_state, monkeypatch, tmp_path) -> None:
    from pathlib import Path

    from testzeus_hercules.config import get_global_conf

    conf = get_global_conf()
    monkeypatch.setitem(conf._config, "PROJECT_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setattr(conf, "_default_test_id", "proof-run-9")
    # An unrelated stake exists first, so stake-less lookups must NOT bind to
    # the proof-run-9 manager by accident of creation order.
    bystander = cm.AppiumManager(stake_id="bystander-stake")
    driver = FakeDriver()
    manager = cm.AppiumManager(stake_id="proof-run-9", _driver_factory=lambda: driver)
    manager._record_video = True
    manager._take_screenshots = True
    manager.driver = driver
    try:
        proof_root = Path(conf.get_proof_path(test_id="proof-run-9"))
        assert cm.AppiumManager.get_instance("proof-run-9") is manager  # same stake -> same manager

        shot_result = _run(mobile_tools.take_mobile_screenshot("stake_shot"))
        shot_path = Path(shot_result.rsplit(": ", 1)[1])
        assert shot_path.parent.parent == proof_root
        assert shot_path.parent.name == "screenshots"

        video_result = _run(mobile_tools.stop_screen_recording())
        video_path = Path(video_result.rsplit(": ", 1)[1])
        assert video_path.parent.parent == proof_root
        assert video_path.parent.name == "videos"
    finally:
        manager.shutdown_pool()


# ── key sequences never report false success ────────────────────────────────


def test_press_key_sequence_reports_unmapped_keys_on_ios(fake_manager, monkeypatch) -> None:
    _manager, driver = fake_manager
    monkeypatch.setenv("DEVICE_OS", "ios")

    result = _run(mobile_tools.press_key_sequence("Tab"))

    assert result.startswith("Error:")
    assert "tab" in result.lower()
    assert "Successfully" not in result
    assert driver.keycodes == []  # nothing was pressed


def test_press_key_sequence_android_tab_still_succeeds(fake_manager) -> None:
    _manager, driver = fake_manager

    result = _run(mobile_tools.press_key_sequence("Tab"))

    assert result.startswith("Successfully pressed key sequence")
    assert driver.keycodes == [61]
