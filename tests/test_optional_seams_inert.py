"""The optional mobile (Appium) and healing seams must be inert by default.

With ``DEVICE_MANAGER`` unset nothing mobile may leak into the planner prompt,
the agent set or decision validation, teardown hooks must be silent no-ops, and
the best-effort selector-recording hook must never raise or write anything into
the working directory. Offline: no browser, no LLM, no Appium.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from testzeus_hercules.core.agents.high_level_planner_agent import (
    PlannerAgent,
    get_planner_prompt,
)
from testzeus_hercules.core.browser_logger import BrowserLogger
from testzeus_hercules.core.runner import BaseRunner
from testzeus_hercules.core.simple_hercules import SimpleHercules


@pytest.fixture(autouse=True)
def _no_device_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVICE_MANAGER", raising=False)


def test_planner_prompt_is_byte_identical_without_device_manager() -> None:
    assert get_planner_prompt() == PlannerAgent.prompt
    assert "'mobile'" not in get_planner_prompt()


def test_mobile_gate_is_closed_and_no_mobile_agent_is_created() -> None:
    assert SimpleHercules._mobile_gate_open() is False
    hercules = SimpleHercules(stake_id="inert")
    assert hercules._create_mobile_nav_agent({}, {}, None) is None


def test_planner_cannot_route_to_mobile_when_disabled() -> None:
    with pytest.raises(ValueError, match="mobile"):
        SimpleHercules._validate_planner_decision({"terminate": "no", "next_step": "tap login", "target_helper": "mobile"})
    # Every non-mobile helper keeps validating exactly as before.
    SimpleHercules._validate_planner_decision({"terminate": "no", "next_step": "click", "target_helper": "browser"})


def test_appium_teardown_is_a_silent_noop_without_sessions() -> None:
    # Must neither raise nor require the optional mobile package.
    BaseRunner._teardown_appium_instances()


def test_selector_recording_hook_never_raises_and_writes_nothing(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    logger_stub = SimpleNamespace()

    BrowserLogger._record_selectors(logger_stub, "login-button", {"role": "button", "css": "#login"})  # type: ignore[arg-type]
    BrowserLogger._record_selectors(logger_stub, "", {})  # type: ignore[arg-type]

    assert list(tmp_path.iterdir()) == []


def test_validation_and_prompt_paths_do_not_import_mobile_modules() -> None:
    import sys

    before = {name for name in sys.modules if name.startswith("appium")}
    get_planner_prompt()
    SimpleHercules._mobile_gate_open()
    SimpleHercules._validate_planner_decision({"terminate": "no", "next_step": "click", "target_helper": "browser"})
    after = {name for name in sys.modules if name.startswith("appium")}
    assert after == before, "the optional Appium client must not be imported by default"


def test_selector_recording_hook_makes_no_cdp_calls_without_healing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    # Simulate an install without the optional healing package.
    monkeypatch.setattr(BrowserLogger, "_selector_store_available", staticmethod(lambda: False))

    class _Page:
        async def evaluate(self, script: str, element: object) -> str:
            return "//button[1]"

    class _Element:
        def __init__(self) -> None:
            self.calls = 0

        async def get_attribute(self, name: str) -> None:
            self.calls += 1
            raise RuntimeError("Element is not attached to the DOM")

        async def evaluate(self, script: str) -> str:
            self.calls += 1
            return "button"

    element = _Element()
    browser_logger = BrowserLogger.__new__(BrowserLogger)
    selectors = asyncio.run(BrowserLogger.get_alternative_selectors(browser_logger, element, _Page()))

    assert selectors
    assert element.calls == 0, "no attribute round-trips may be made when the selector store is unavailable"


def test_get_element_attributes_quiet_logs_detached_element_at_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    import testzeus_hercules.core.browser_logger as browser_logger_module

    calls: dict[str, int] = {"error": 0, "debug": 0}
    monkeypatch.setattr(browser_logger_module.logger, "error", lambda *a, **k: calls.__setitem__("error", calls["error"] + 1))
    monkeypatch.setattr(browser_logger_module.logger, "debug", lambda *a, **k: calls.__setitem__("debug", calls["debug"] + 1))

    class _Detached:
        async def get_attribute(self, name: str) -> None:
            raise RuntimeError("Element is not attached to the DOM")

    browser_logger = BrowserLogger.__new__(BrowserLogger)
    assert asyncio.run(BrowserLogger.get_element_attributes(browser_logger, _Detached(), quiet=True)) == {}
    assert calls == {"error": 0, "debug": 1}
