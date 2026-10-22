"""Offline tests for screen recording artifacts and session teardown."""

import asyncio
import base64
import os
from pathlib import Path

from tests import conftest_mobile as cm
from tests.conftest_mobile import (  # noqa: F401
    FakeDriver,
    clean_manager_state,
    fake_manager,
    mobile_env,
)

pytest_plugins = ["tests.conftest_mobile"]


def test_stop_screen_recording_writes_decoded_video(fake_manager, tmp_path: Path) -> None:
    manager, driver = fake_manager
    video_path = asyncio.run(manager.stop_screen_recording())

    assert video_path is not None
    parent = Path(manager._video_dir)
    assert parent == tmp_path / "videos"
    name = os.path.basename(video_path)
    assert name.startswith("t1_") and name.endswith(".mp4")
    with open(video_path, "rb") as f:
        assert f.read() == base64.b64decode(driver._recording_b64)
    assert manager.get_latest_video_path() == video_path


def test_stop_screen_recording_respects_record_video_gate(fake_manager) -> None:
    manager, driver = fake_manager
    manager._record_video = False

    asyncio.run(manager.start_screen_recording())
    video_path = asyncio.run(manager.stop_screen_recording())

    assert video_path is None
    assert driver.record_started is False
    assert manager.get_latest_video_path() is None


def test_stop_screen_recording_without_driver_returns_none(mobile_env, tmp_path, clean_manager_state) -> None:
    manager = cm.AppiumManager(stake_id="nodriver")
    manager._video_dir = str(tmp_path / "videos")
    manager._record_video = True

    assert asyncio.run(manager.stop_screen_recording()) is None


def test_stop_screen_recording_empty_payload_returns_none(fake_manager) -> None:
    manager, driver = fake_manager
    driver._recording_b64 = ""

    assert asyncio.run(manager.stop_screen_recording()) is None


def test_stop_screen_recording_driver_error_is_logged_not_raised(fake_manager) -> None:
    manager, driver = fake_manager
    driver.stop_error = RuntimeError("recording backend exploded")

    assert asyncio.run(manager.stop_screen_recording()) is None


def test_create_session_auto_starts_recording(mobile_env, tmp_path, clean_manager_state) -> None:
    driver = FakeDriver()
    manager = cm.AppiumManager(stake_id="rec-on", _driver_factory=lambda: driver)
    manager._video_dir = str(tmp_path / "videos")
    manager._screenshots_dir = str(tmp_path / "screenshots")
    manager._record_video = True
    manager._take_screenshots = True
    try:
        result = asyncio.run(manager.create_session({"platformName": "Android", "deviceName": "emulator-5554"}))
        assert result["reused"] is False
        assert result["session_id"] == driver.session_id
        assert driver.record_started is True
        assert manager.driver is driver
    finally:
        manager.shutdown_pool()


def test_create_session_skips_recording_when_disabled(mobile_env, tmp_path, clean_manager_state) -> None:
    driver = FakeDriver()
    manager = cm.AppiumManager(stake_id="rec-off", _driver_factory=lambda: driver)
    manager._record_video = False
    manager._take_screenshots = False
    try:
        asyncio.run(manager.create_session())
        assert driver.record_started is False
    finally:
        manager.shutdown_pool()


def test_quit_session_stops_recording_best_effort_when_stop_raises(fake_manager) -> None:
    manager, driver = fake_manager
    driver.stop_error = RuntimeError("stop failed hard")

    asyncio.run(manager.quit_session())  # must not raise

    assert driver.record_stop_count == 1
    assert driver.quit_called == 1
    assert manager.driver is None
    assert manager._session_capabilities == {}


def test_quit_session_without_driver_is_noop(fake_manager) -> None:
    manager, driver = fake_manager
    manager.driver = None

    asyncio.run(manager.quit_session())

    assert driver.quit_called == 0


def test_quit_session_quits_driver(fake_manager) -> None:
    manager, driver = fake_manager

    asyncio.run(manager.quit_session())

    assert driver.quit_called == 1
    assert manager.driver is None


# ── close_instance teardown (shutdown path) ──────────────────────────────────


def test_close_instance_quits_driver_and_saves_recording(fake_manager) -> None:
    manager, driver = fake_manager

    cm.AppiumManager.close_instance("t1")  # shutdown path: must end the session, not just drop the singleton

    assert driver.record_stop_count == 1
    assert driver.quit_called == 1
    assert manager.driver is None
    video_path = manager.get_latest_video_path()
    assert video_path is not None
    assert os.path.isfile(video_path)
    assert "t1" not in cm.AppiumManager._instances


def test_close_instance_survives_driver_quit_errors(fake_manager, monkeypatch) -> None:
    manager, driver = fake_manager

    def exploding_quit() -> None:
        driver.quit_called += 1
        raise RuntimeError("driver already gone")

    monkeypatch.setattr(driver, "quit", exploding_quit)
    driver.stop_error = RuntimeError("recording backend exploded")

    cm.AppiumManager.close_instance("t1")  # must not raise

    assert driver.quit_called == 1
    assert manager.driver is None
    assert "t1" not in cm.AppiumManager._instances
    assert cm.AppiumManager._default_instance is None


def test_close_instance_without_driver_is_noop(mobile_env, clean_manager_state) -> None:
    manager = cm.AppiumManager(stake_id="nodriver")

    cm.AppiumManager.close_instance("nodriver")  # no driver session: just unregister

    assert manager.driver is None
    assert "nodriver" not in cm.AppiumManager._instances


# ── singleton identity and off-loop execution (smoke) ───────────────────────


def test_singleton_identity_per_stake_id(clean_manager_state) -> None:
    first = cm.AppiumManager.get_instance("a")
    second = cm.AppiumManager.get_instance("a")
    other = cm.AppiumManager.get_instance("b")
    default = cm.AppiumManager.get_instance(None)

    assert first is second
    assert first is not other
    assert default is first  # first created instance becomes the default


def test_first_stake_scoped_instance_is_mirrored_under_default_key(clean_manager_state) -> None:
    manager = cm.AppiumManager(stake_id="first-run")

    assert cm.AppiumManager.get_instance(None) is manager
    assert cm.AppiumManager._instances.get("0") is manager

    # Closing by stake id must also clear the mirrored default slot.
    cm.AppiumManager.close_instance("first-run")
    assert cm.AppiumManager._default_instance is None
    assert not cm.AppiumManager._instances


def test_close_instance_none_removes_default_and_its_stake_key(clean_manager_state) -> None:
    manager = cm.AppiumManager(stake_id="solo")
    assert cm.AppiumManager.get_instance(None) is manager

    cm.AppiumManager.close_instance(None)

    assert cm.AppiumManager._default_instance is None
    assert "solo" not in cm.AppiumManager._instances
    assert "0" not in cm.AppiumManager._instances
    assert cm.AppiumManager.get_instance(None) is not manager  # fresh default lookup


def test_run_in_ui_thread_does_not_block_event_loop(fake_manager) -> None:
    import time

    manager, _driver = fake_manager
    order = []

    def blocking() -> None:
        time.sleep(0.2)
        order.append("blocking")

    async def scenario() -> None:
        async def sleeper() -> None:
            await asyncio.sleep(0.05)
            order.append("sleep")

        sleep_task = asyncio.create_task(sleeper())
        await manager._run_in_ui_thread(blocking, "blocking")
        await sleep_task

    asyncio.run(scenario())
    assert order == ["sleep", "blocking"]  # the sleep finished while the pool worked
