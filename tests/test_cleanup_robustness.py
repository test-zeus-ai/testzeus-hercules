"""Cleanup-robustness regression tests.

Covers the error-aggregating teardown paths:

- ``raise_if_cancelled`` must re-raise a CancelledError found in collected
  cleanup errors (with the remaining errors attached as a cause group) so
  cancellation is never swallowed by aggregation
- ``stop_playwright`` must run both teardown steps and aggregate their errors
- ``close_instance_async`` / ``close_all_instances_async`` must await teardown,
  unregister every instance even when its teardown fails, and raise one
  ``BaseExceptionGroup`` at the end
- ``BaseRunner.clean_up`` must collect agent-shutdown and browser-cleanup
  errors instead of stopping at the first one, preserving cancellation

Everything is offline: browser teardown is stubbed, no real browser runs.
"""

import asyncio
from typing import Any

import pytest
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.runner import BaseRunner
from testzeus_hercules.utils.cleanup_helper import raise_if_cancelled


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ── raise_if_cancelled ────────────────────────────────────────────────────────


def test_raise_if_cancelled_reraises_cancelled_with_cause_group() -> None:
    cancelled = asyncio.CancelledError()
    other = ValueError("browser stop failed")

    with pytest.raises(asyncio.CancelledError) as excinfo:
        raise_if_cancelled([other, cancelled])

    assert excinfo.value is cancelled
    cause = excinfo.value.__cause__
    assert isinstance(cause, BaseExceptionGroup)
    assert [type(e) for e in cause.exceptions] == [ValueError]
    assert cause.exceptions[0] is other


def test_raise_if_cancelled_with_only_cancellation_has_no_cause_group() -> None:
    cancelled = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError) as excinfo:
        raise_if_cancelled([cancelled])

    assert excinfo.value is cancelled
    assert excinfo.value.__cause__ is None


def test_raise_if_cancelled_quiet_without_cancellation() -> None:
    assert raise_if_cancelled([]) is None
    assert raise_if_cancelled([ValueError("a"), RuntimeError("b")]) is None


def test_raise_if_cancelled_keeps_extra_cancellations_in_cause_group() -> None:
    first = asyncio.CancelledError()
    second = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError) as excinfo:
        raise_if_cancelled([first, second])

    assert excinfo.value is first
    assert isinstance(excinfo.value.__cause__, BaseExceptionGroup)
    assert excinfo.value.__cause__.exceptions == (second,)


# ── stop_playwright error aggregation ─────────────────────────────────────────


def _bare_manager() -> PlaywrightManager:
    """Manager instance without __init__ (no config/browser stack touched)."""
    manager = object.__new__(PlaywrightManager)
    manager._browser_context = None
    manager._browser = None
    manager._user_data_dir = None
    manager._playwright = None
    return manager


class _ExplodingDriver:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.stopped = 0

    async def stop(self) -> None:
        self.stopped += 1
        if self.error is not None:
            raise self.error


def test_stop_playwright_aggregates_context_and_driver_errors() -> None:
    manager = _bare_manager()
    context_error = RuntimeError("context close failed")
    driver_error = RuntimeError("driver stop failed")
    driver = _ExplodingDriver(driver_error)
    manager._playwright = driver

    async def failing_close() -> None:
        raise context_error

    manager.close_browser_context = failing_close

    with pytest.raises(BaseExceptionGroup) as excinfo:
        run(manager.stop_playwright())

    assert excinfo.value.exceptions == (context_error, driver_error)
    assert driver.stopped == 1, "driver stop must still run after a failed context close"


def test_stop_playwright_succeeds_quietly_when_both_steps_pass() -> None:
    manager = _bare_manager()
    driver = _ExplodingDriver()
    manager._playwright = driver

    run(manager.stop_playwright())

    assert driver.stopped == 1
    assert manager._playwright is None


def test_stop_playwright_reports_only_driver_error() -> None:
    manager = _bare_manager()
    driver_error = RuntimeError("driver stop failed")
    driver = _ExplodingDriver(driver_error)
    manager._playwright = driver

    with pytest.raises(BaseExceptionGroup) as excinfo:
        run(manager.stop_playwright())

    assert excinfo.value.exceptions == (driver_error,)
    # A failed stop keeps the handle so a later attempt can retry.
    assert manager._playwright is driver


# ── close_instance_async / close_all_instances_async ──────────────────────────


def _restore_class_state(saved_instances: dict, saved_default: Any) -> None:
    PlaywrightManager._instances.clear()
    PlaywrightManager._instances.update(saved_instances)
    PlaywrightManager._default_instance = saved_default


def _register(stake_id: str, stop_error: Exception | None = None) -> tuple[PlaywrightManager, list]:
    instance = object.__new__(PlaywrightManager)
    instance.stake_id = stake_id
    stops: list = []

    async def stop() -> None:
        stops.append(stake_id)
        if stop_error is not None:
            raise stop_error

    instance.stop_playwright = stop
    PlaywrightManager._instances[stake_id] = instance
    return instance, stops


def test_close_instance_async_unknown_stake_is_quiet() -> None:
    assert run(PlaywrightManager.close_instance_async("missing")) is None


def test_close_instance_async_unregisters_even_when_stop_fails() -> None:
    saved_instances = dict(PlaywrightManager._instances)
    saved_default = PlaywrightManager._default_instance
    try:
        error = RuntimeError("close failed")
        instance, stops = _register("stake-a", error)

        with pytest.raises(BaseExceptionGroup) as excinfo:
            run(PlaywrightManager.close_instance_async("stake-a"))

        assert excinfo.value.exceptions == (error,)
        assert stops == ["stake-a"]
        assert "stake-a" not in PlaywrightManager._instances, "failed teardown must still unregister the instance"
        assert PlaywrightManager._default_instance is not instance
    finally:
        _restore_class_state(saved_instances, saved_default)


def test_close_all_instances_async_empty_registry_is_quiet() -> None:
    saved_instances = dict(PlaywrightManager._instances)
    saved_default = PlaywrightManager._default_instance
    try:
        PlaywrightManager._instances.clear()
        PlaywrightManager._default_instance = None
        assert run(PlaywrightManager.close_all_instances_async()) is None
    finally:
        _restore_class_state(saved_instances, saved_default)


def test_close_all_instances_async_stops_all_and_unregisters_on_partial_failure() -> None:
    saved_instances = dict(PlaywrightManager._instances)
    saved_default = PlaywrightManager._default_instance
    try:
        failure = RuntimeError("second instance failed")
        first, first_stops = _register("stake-1")
        second, second_stops = _register("stake-2", failure)
        PlaywrightManager._default_instance = first

        with pytest.raises(BaseExceptionGroup) as excinfo:
            run(PlaywrightManager.close_all_instances_async())

        assert excinfo.value.exceptions == (failure,)
        assert first_stops == ["stake-1"], "one failing teardown must not skip the other instances"
        assert second_stops == ["stake-2"]
        assert PlaywrightManager._instances == {}, "every instance must be unregistered, failed or not"
        assert PlaywrightManager._default_instance is None
    finally:
        _restore_class_state(saved_instances, saved_default)


def test_close_all_instances_async_aggregates_every_failure() -> None:
    saved_instances = dict(PlaywrightManager._instances)
    saved_default = PlaywrightManager._default_instance
    try:
        error_a = RuntimeError("a failed")
        error_b = RuntimeError("b failed")
        _register("stake-a", error_a)
        _register("stake-b", error_b)

        with pytest.raises(BaseExceptionGroup) as excinfo:
            run(PlaywrightManager.close_all_instances_async())

        assert excinfo.value.exceptions == (error_a, error_b)
        assert PlaywrightManager._instances == {}
    finally:
        _restore_class_state(saved_instances, saved_default)


# ── BaseRunner.clean_up aggregation ───────────────────────────────────────────


class _StubAgents:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.shutdown_runs = 0

    async def shutdown(self) -> None:
        self.shutdown_runs += 1
        if self.error is not None:
            raise self.error


class _StubBrowser:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.stop_runs = 0

    async def stop_playwright(self) -> None:
        self.stop_runs += 1
        if self.error is not None:
            raise self.error


def _runner_with(agent_error: Exception | None, browser_error: Exception | None) -> tuple[BaseRunner, _StubAgents, _StubBrowser]:
    runner = BaseRunner(stake_id="cleanup-test")
    agents = _StubAgents(agent_error)
    browser = _StubBrowser(browser_error)
    runner.simple_hercules = agents
    runner.browser_manager = browser
    return runner, agents, browser


def test_clean_up_collects_agent_and_browser_errors() -> None:
    runner, agents, browser = _runner_with(RuntimeError("agent shutdown failed"), ValueError("browser stop failed"))

    with pytest.raises(BaseExceptionGroup) as excinfo:
        run(runner.clean_up())

    assert [type(e) for e in excinfo.value.exceptions] == [RuntimeError, ValueError], "both cleanup failures must be collected, not first-error-wins"
    assert agents.shutdown_runs == 1
    assert browser.stop_runs == 1, "a failing agent shutdown must not skip the browser stop"
    assert runner.simple_hercules is None
    assert runner.browser_manager is None


def test_clean_up_preserves_cancellation_over_other_errors() -> None:
    runner, agents, browser = _runner_with(asyncio.CancelledError(), ValueError("browser stop failed"))

    with pytest.raises(asyncio.CancelledError) as excinfo:
        run(runner.clean_up())

    assert isinstance(excinfo.value.__cause__, BaseExceptionGroup)
    assert [type(e) for e in excinfo.value.__cause__.exceptions] == [ValueError]
    assert browser.stop_runs == 1, "cancellation during agent shutdown must not skip the browser stop"


def test_clean_up_quiet_when_everything_stops() -> None:
    runner, _agents, _browser = _runner_with(None, None)
    assert run(runner.clean_up()) is None
