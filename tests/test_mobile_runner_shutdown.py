"""Runner shutdown must also reclaim Appium state (best-effort, never raising).

An aborted scenario can leave the device session, its recording, and the
manager's worker pool alive; ``BaseRunner.shutdown`` — the same path that
closes every Playwright instance — must close every AppiumManager instance
too. Mobile support is optional, so a missing AppiumManager module makes the
teardown a silent no-op, and teardown failures never block the shutdown.
"""

import asyncio
import sys
import types

from tests.conftest_mobile import FakeDriver
from testzeus_hercules.core.runner import BaseRunner


def _bare_runner() -> BaseRunner:
    return BaseRunner(stake_id="shutdown-test")


def _stub_appium_manager_module(monkeypatch, close_all) -> None:
    stub = types.ModuleType("testzeus_hercules.core.mobile.appium_manager")

    class StubAppiumManager:
        pass

    StubAppiumManager.close_all_instances = classmethod(lambda cls: close_all())
    stub.AppiumManager = StubAppiumManager
    monkeypatch.setitem(sys.modules, "testzeus_hercules.core.mobile.appium_manager", stub)


def test_shutdown_closes_all_appium_instances(monkeypatch) -> None:
    calls = []
    _stub_appium_manager_module(monkeypatch, lambda: calls.append("close_all"))

    runner = _bare_runner()
    asyncio.run(runner.shutdown())

    assert calls == ["close_all"]
    assert runner.shutdown_event.is_set()


def test_shutdown_survives_appium_teardown_failure(monkeypatch) -> None:
    def explode() -> None:
        raise RuntimeError("appium teardown exploded")

    _stub_appium_manager_module(monkeypatch, explode)

    runner = _bare_runner()
    asyncio.run(runner.shutdown())  # must not raise

    assert runner.shutdown_event.is_set()


def test_shutdown_without_mobile_module_is_a_noop(monkeypatch) -> None:
    # None in sys.modules makes the guarded import raise ImportError.
    monkeypatch.setitem(sys.modules, "testzeus_hercules.core.mobile.appium_manager", None)

    runner = _bare_runner()
    asyncio.run(runner.shutdown())

    assert runner.shutdown_event.is_set()


def test_shutdown_quits_live_appium_driver_session(tmp_path) -> None:
    # End-to-end through BaseRunner.shutdown: the live device session (and its
    # pending recording) must be torn down, not left until newCommandTimeout.
    # The manager class is imported the same way _teardown_appium_instances
    # imports it, so the singleton bookkeeping is guaranteed to line up even
    # if other tests re-imported the mobile modules.
    from testzeus_hercules.core.mobile.appium_manager import AppiumManager

    driver = FakeDriver()
    manager = AppiumManager(stake_id="shutdown-live", _driver_factory=lambda: driver)
    manager._video_dir = str(tmp_path / "videos")
    manager._screenshots_dir = str(tmp_path / "screenshots")
    manager._record_video = True
    manager.driver = driver
    try:
        runner = _bare_runner()
        asyncio.run(runner.shutdown())

        assert driver.record_stop_count == 1
        assert driver.quit_called == 1
        assert manager.driver is None
        assert not AppiumManager._instances
        assert runner.shutdown_event.is_set()
    finally:
        AppiumManager._instances.clear()
        AppiumManager._default_instance = None
