"""SKIP_BROWSER regression tests.

When the environment flag is set, ``BaseRunner.initialize`` must run without
constructing or initializing a PlaywrightManager at all (pure MCP/API scenarios
pay otherwise a chromium launch per scenario), and the runner's downstream
paths must tolerate a ``None`` browser manager. Without the flag a manager is
constructed and initialized as before.

No real browser or LLM runs: the manager, the agent graph and the LLM config
manager are stubbed.
"""

import asyncio
from typing import Any

import pytest
from testzeus_hercules.core import runner as runner_module
from testzeus_hercules.core.runner import BaseRunner
from testzeus_hercules.utils.env_flags import should_skip_browser


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class _RecordingManager:
    """Stands in for PlaywrightManager; records construction and init calls."""

    constructed: list[str] = []
    initialized: list[str] = []

    def __init__(self, *args: Any, stake_id: str | None = None, **kwargs: Any) -> None:
        self.stake_id = stake_id
        type(self).constructed.append(str(stake_id))

    async def async_initialize(self) -> None:
        type(self).initialized.append(str(self.stake_id))


class _FakeConfigManager:
    _config: dict[str, dict[str, Any]] = {"planner_agent": {"model": "stub"}, "nav_agent": {"model": "stub"}, "helper_agent": {"model": "stub"}}

    def initialize(self) -> None:
        pass

    def get_agent_config(self, name: str) -> dict[str, Any] | None:
        return dict(self._config.get(name) or {})


class _StubHercules:
    create_calls: list[str] = []

    def __init__(self, stake_id: str) -> None:
        self.stake_id = stake_id
        self._last_graph_result = None
        self.agents_map: dict[str, Any] = {}

    @staticmethod
    async def create(stake_id: str, *_args: Any, **_kwargs: Any) -> "_StubHercules":
        _StubHercules.create_calls.append(stake_id)
        return _StubHercules(stake_id)

    async def process_command(self, command: str, current_url: Any = None) -> str:
        return f"handled: {command}"


@pytest.fixture()
def stubbed_stack(monkeypatch):
    monkeypatch.setattr(runner_module.AgentsLLMConfigManager, "get_instance", classmethod(lambda cls: _FakeConfigManager()))
    monkeypatch.setattr(runner_module.SimpleHercules, "create", _StubHercules.create)
    monkeypatch.setattr(runner_module, "PlaywrightManager", _RecordingManager)
    _RecordingManager.constructed.clear()
    _RecordingManager.initialized.clear()
    _StubHercules.create_calls.clear()
    return _RecordingManager


# ── flag parsing ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE", "Yes", " true "])
def test_should_skip_browser_true_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("SKIP_BROWSER", value)
    assert should_skip_browser() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "", "off", "2"])
def test_should_skip_browser_false_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("SKIP_BROWSER", value)
    assert should_skip_browser() is False


def test_should_skip_browser_unset(monkeypatch) -> None:
    monkeypatch.delenv("SKIP_BROWSER", raising=False)
    assert should_skip_browser() is False


# ── runner wiring ─────────────────────────────────────────────────────────────


def test_initialize_skips_browser_when_flag_set(monkeypatch, stubbed_stack) -> None:
    monkeypatch.setenv("SKIP_BROWSER", "1")
    runner = BaseRunner(stake_id="skip-browser")

    run(runner.initialize())

    assert stubbed_stack.constructed == [], "no PlaywrightManager may be constructed when SKIP_BROWSER is set"
    assert stubbed_stack.initialized == []
    assert runner.browser_manager is None
    assert runner.simple_hercules is not None, "the agent stack must still be created"


def test_initialize_constructs_browser_without_flag(monkeypatch, stubbed_stack) -> None:
    monkeypatch.delenv("SKIP_BROWSER", raising=False)
    runner = BaseRunner(stake_id="with-browser")

    run(runner.initialize())

    assert stubbed_stack.constructed == ["with-browser"]
    assert stubbed_stack.initialized == ["with-browser"]
    assert isinstance(runner.browser_manager, _RecordingManager)


def test_process_command_tolerates_missing_browser_manager(monkeypatch, stubbed_stack) -> None:
    runner = BaseRunner(stake_id="no-browser-run")
    runner.simple_hercules = _StubHercules("no-browser-run")
    runner.browser_manager = None

    result, elapsed = run(runner.process_command("run the api check"))

    assert result == "handled: run the api check"
    assert elapsed >= 0
    assert runner.is_running is False
