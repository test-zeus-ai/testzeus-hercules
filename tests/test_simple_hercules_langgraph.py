import asyncio
import importlib
import importlib.machinery
import importlib.util
import json
import logging
import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from testzeus_hercules.core.simple_hercules import SimpleHercules
from testzeus_hercules.reporting.bundle import parse_junit_testcases
from testzeus_hercules.utils.junit_helper import build_junit_xml
from testzeus_hercules.utils.llm_helper import GraphChatResult, parse_agent_response


class FakeLLM:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = responses
        self.calls: list[list[Any]] = []

    def bind_tools(self, tools: list[Any]) -> "FakeLLM":
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.calls.append(messages)
        if self.responses:
            return self.responses.pop(0)
        return AIMessage(content="current_output: done\n##TERMINATE TASK##")


class FakeAgent:
    def __init__(self, name: str, responses: list[AIMessage], tools: list[Any] | None = None) -> None:
        self.agent_name = name
        self.system_message = f"{name} system"
        self.llm = FakeLLM(responses)
        self.tools = tools or []


def _hercules() -> SimpleHercules:
    return SimpleHercules(stake_id="test", browser_nav_max_chat_round=5)


def test_create_normalizes_all_agent_configs_for_chatopenai(monkeypatch) -> None:
    async def initialize_agents(self: SimpleHercules) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(SimpleHercules, "_initialize_agents", initialize_agents)
    monkeypatch.setattr(SimpleHercules, "_build_graph", lambda self: object())

    model_cfg = {
        "model_config_params": {"model": "claude-haiku-5-5"},
        "llm_config_params": {"max_tokens_to_sample": 900, "temperature": 0.1},
    }
    configs = [
        {
            "model_config_params": dict(model_cfg["model_config_params"]),
            "llm_config_params": dict(model_cfg["llm_config_params"]),
        }
        for _ in range(3)
    ]

    async def run() -> list[dict[str, Any]]:
        await SimpleHercules.create("test", *configs)
        return [config["llm_config_params"] for config in configs]

    params = asyncio.run(run())

    assert all(item["max_tokens"] == 900 for item in params)
    assert all("max_tokens_to_sample" not in item for item in params)


def test_executor_routes_every_target_helper_to_expected_agent() -> None:
    async def run() -> None:
        hercules = _hercules()
        helper_targets = {
            "browser": "browser_nav_agent",
            "agent": "browser_nav_agent",
            "api": "api_nav_agent",
            "sec": "sec_nav_agent",
            "sql": "sql_nav_agent",
            "time_keeper": "time_keeper_nav_agent",
            "mcp": "mcp_nav_agent",
            "executor": "executor_nav_agent",
        }
        hercules.agents_map = {
            agent_name: FakeAgent(
                agent_name,
                [AIMessage(content=f"{agent_name} completed ##TERMINATE TASK##")],
            )
            for agent_name in set(helper_targets.values())
        }
        for target_helper, expected_agent in helper_targets.items():
            state = {
                "messages": [HumanMessage(content="root task")],
                "next_step": f"run {target_helper}",
                "target_helper": target_helper,
                "step_token_log": [],
                "step_timings": [],
                "completed_step_signatures": [],
                "current_url": "https://example.test/page",
            }

            result = await hercules._executor_node(state)

            assert f"[{expected_agent}]" in result["messages"][-1].content
            assert result["total_steps"] == 1

    asyncio.run(run())


def test_nav_agent_executes_multiple_non_stale_tool_calls_in_order() -> None:
    async def run() -> None:
        calls: list[str] = []

        def first_tool() -> str:
            calls.append("first")
            return "first result"

        def second_tool() -> str:
            calls.append("second")
            return "second result"

        tools = [
            StructuredTool.from_function(func=first_tool, name="first", description="first"),
            StructuredTool.from_function(func=second_tool, name="second", description="second"),
        ]
        agent = FakeAgent(
            "api_nav_agent",
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "first", "args": {}, "id": "call_1"},
                        {"name": "second", "args": {}, "id": "call_2"},
                    ],
                ),
                AIMessage(content="current_output: done\n##TERMINATE TASK##"),
            ],
            tools,
        )

        result = await _hercules()._run_nav_agent(agent, "do both", "api_nav_agent")

        assert calls == ["first", "second"]
        assert "##TERMINATE TASK##" in result

    asyncio.run(run())


def test_nav_agent_reports_invalid_tool_arguments() -> None:
    async def run() -> None:
        def first_tool(value: str = "") -> str:
            return f"first {value}"

        agent = FakeAgent(
            "api_nav_agent",
            [
                SimpleNamespace(
                    content="",
                    tool_calls=[
                        {"name": "first", "args": "not-a-dict", "id": "call_1"},
                    ],
                ),
                AIMessage(content="current_output: handled\n##TERMINATE TASK##"),
            ],
            [StructuredTool.from_function(func=first_tool, name="first", description="first")],
        )

        result = await _hercules()._run_nav_agent(agent, "call malformed", "api_nav_agent")

        assert "##TERMINATE TASK##" in result
        tool_messages = [message for message in agent.llm.calls[1] if isinstance(message, ToolMessage)]
        assert tool_messages
        assert "expected tool arguments to be a dict" in tool_messages[-1].content

    asyncio.run(run())


def test_browser_stale_dom_guard_skips_later_tool_calls() -> None:
    async def run() -> None:
        calls: list[str] = []

        def click() -> str:
            calls.append("click")
            return "Success. As a consequence of this action, new elements have appeared " "in view. Get all_fields DOM to complete the interaction."

        def get_page_text() -> str:
            calls.append("get_page_text")
            return "page text"

        tools = [
            StructuredTool.from_function(func=click, name="click", description="click"),
            StructuredTool.from_function(func=get_page_text, name="get_page_text", description="read"),
        ]
        agent = FakeAgent(
            "browser_nav_agent",
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "click", "args": {}, "id": "call_1"},
                        {"name": "get_page_text", "args": {}, "id": "call_2"},
                    ],
                ),
                AIMessage(content="current_output: refreshed\n##TERMINATE TASK##"),
            ],
            tools,
        )

        result = await _hercules()._run_nav_agent(agent, "click then read", "browser_nav_agent")

        assert calls == ["click"]
        assert "##TERMINATE TASK##" in result
        second_call_messages = agent.llm.calls[1]
        assert any(isinstance(message, HumanMessage) and "browser state changed" in message.content for message in second_call_messages)

    asyncio.run(run())


def test_helper_task_includes_current_url() -> None:
    async def run() -> None:
        hercules = _hercules()
        agent = FakeAgent(
            "browser_nav_agent",
            [AIMessage(content="current_output: done\n##TERMINATE TASK##")],
        )
        hercules.agents_map = {"browser_nav_agent": agent}

        await hercules._executor_node(
            {
                "messages": [HumanMessage(content="root task")],
                "next_step": "click the login button",
                "target_helper": "browser",
                "step_token_log": [],
                "step_timings": [],
                "completed_step_signatures": [],
                "current_url": "https://example.test/start",
            }
        )

        helper_task = agent.llm.calls[0][1].content
        assert "Current Page: https://example.test/start" in helper_task

    asyncio.run(run())


def test_helper_task_prefers_live_current_url(monkeypatch) -> None:
    async def run() -> None:
        hercules = _hercules()

        async def fake_live_url() -> str:
            return "https://example.test/live"

        monkeypatch.setattr(hercules, "_get_live_current_url", fake_live_url)

        helper_task = await hercules._build_helper_task(
            "click the login button",
            "browser",
            {"current_url": "https://example.test/stale"},
        )

        assert "Current Page: https://example.test/live" in helper_task

    asyncio.run(run())


def test_cost_metrics_include_langgraph_token_totals() -> None:
    metrics = _hercules()._build_cost_metrics({"total_prompt_tokens": 7, "total_completion_tokens": 11})

    usage = metrics["usage_including_cached_inference"]["langgraph"]
    assert usage["prompt_tokens"] == 7
    assert usage["completion_tokens"] == 11
    assert usage["total_tokens"] == 18
    assert metrics["usage_including_cached_inference"]["cost_unavailable"] is True
    assert "total_cost" not in metrics["usage_including_cached_inference"]


def test_cost_metrics_include_provider_cost_when_available() -> None:
    metrics = _hercules()._build_cost_metrics(
        {
            "total_prompt_tokens": 7,
            "total_completion_tokens": 11,
            "total_cost": 0.42,
            "cost_available": True,
        }
    )

    usage = metrics["usage_including_cached_inference"]
    assert usage["total_cost"] == 0.42
    assert usage["langgraph"]["cost"] == 0.42


def test_executor_accumulates_nav_agent_token_usage() -> None:
    async def run() -> None:
        hercules = _hercules()
        agent = FakeAgent(
            "api_nav_agent",
            [
                AIMessage(
                    content="current_output: done\n##TERMINATE TASK##",
                    response_metadata={
                        "token_usage": {
                            "prompt_tokens": 13,
                            "completion_tokens": 7,
                        },
                        "response_cost": 0.03,
                    },
                )
            ],
            [StructuredTool.from_function(func=lambda: "unused", name="available", description="available")],
        )
        hercules.agents_map = {"api_nav_agent": agent}
        result = await hercules._executor_node(
            {
                "messages": [HumanMessage(content="root task")],
                "next_step": "call the API",
                "target_helper": "api",
                "step_token_log": [],
                "step_timings": [],
                "completed_step_signatures": [],
                "current_url": "",
                "total_prompt_tokens": 100,
                "total_completion_tokens": 50,
            }
        )

        executor_entry = result["step_token_log"][-1]
        assert executor_entry["prompt_tokens"] == 13
        assert executor_entry["completion_tokens"] == 7
        assert executor_entry["total_tokens"] == 20
        assert executor_entry["cost"] == 0.03
        assert result["total_prompt_tokens"] == 113
        assert result["total_completion_tokens"] == 57
        assert result["total_cost"] == 0.03
        assert result["cost_available"] is True

    asyncio.run(run())


def test_repeated_completed_planner_step_does_not_force_success() -> None:
    async def run() -> None:
        next_step = "Click the Continue button"
        planner_payload = {
            "plan": "Continue through a multi-page form.",
            "next_step": next_step,
            "target_helper": "browser",
            "terminate": "no",
            "is_assert": False,
            "is_passed": False,
        }
        hercules = _hercules()
        hercules.agents_map = {
            "planner_agent": SimpleNamespace(
                system_message="planner system",
                llm=FakeLLM([AIMessage(content=json.dumps(planner_payload))]),
                on_planner_message=lambda _content: None,
            )
        }

        result = await hercules._planner_node(
            {
                "messages": [HumanMessage(content="root task")],
                "planner_turn": 0,
                "completed_step_signatures": [hercules._step_signature(next_step)],
                "step_token_log": [],
                "step_timings": [],
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_cost": 0.0,
                "cost_available": False,
            }
        )

        assert result["next_step"] == next_step
        assert result["target_helper"] == "browser"
        assert result["terminate"] == "no"
        assert result["is_passed"] is False

    asyncio.run(run())


def test_nav_agent_max_rounds_returns_explicit_error() -> None:
    async def run() -> None:
        agent = FakeAgent(
            "api_nav_agent",
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "missing", "args": {}, "id": "call_1"}],
                ),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "missing", "args": {}, "id": "call_2"}],
                ),
            ],
            [StructuredTool.from_function(func=lambda: "unused", name="available", description="available")],
        )
        hercules = SimpleHercules(stake_id="test", browser_nav_max_chat_round=2)

        result = await hercules._run_nav_agent(agent, "do impossible thing", "api_nav_agent")

        assert result.startswith("[ERROR] api_nav_agent max nav rounds")
        assert "[empty or tool-calls-only assistant response]" in result

    asyncio.run(run())


def test_graph_chat_result_summary_uses_terminal_planner_json() -> None:
    terminal = {
        "terminate": "yes",
        "final_response": "done",
        "is_assert": True,
        "is_passed": True,
        "assert_summary": "ok",
    }
    result = GraphChatResult(
        messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "tool", "args": {}, "id": "call_1"}],
            ),
            AIMessage(content=json.dumps({"terminate": "no", "next_step": "keep going"})),
            AIMessage(content=json.dumps(terminal)),
        ],
        terminate="yes",
    )

    assert json.loads(result.summary)["final_response"] == "done"


# ── terminal helper results are preserved in the exported summary ─────────────


def test_summary_prefers_terminal_pass_helper_text_over_nonterminal_planner() -> None:
    result = GraphChatResult(
        messages=[AIMessage(content=json.dumps({"terminate": "no", "next_step": "keep going", "target_helper": "browser"}))],
        terminate="yes",
        last_helper_response="PASS: checkout completed successfully",
    )

    summary = json.loads(result.summary)
    assert summary["terminate"] == "yes"
    assert summary["is_assert"] is True
    assert summary["is_passed"] is True
    assert "checkout completed" in summary["final_response"]


def test_summary_prefers_terminal_fail_helper_text_over_nonterminal_planner() -> None:
    result = GraphChatResult(
        messages=[AIMessage(content=json.dumps({"terminate": "no", "next_step": "keep going", "target_helper": "browser"}))],
        terminate="yes",
        last_helper_response="FAIL: checkout could not complete",
    )

    summary = json.loads(result.summary)
    assert summary["terminate"] == "yes"
    assert summary["is_assert"] is True
    assert summary["is_passed"] is False


def test_summary_normalizes_terminate_marker_helper_text() -> None:
    result = GraphChatResult(
        messages=[AIMessage(content=json.dumps({"terminate": "no", "next_step": "keep going", "target_helper": "browser"}))],
        terminate="yes",
        last_helper_response="Task completed\n##TERMINATE TASK##",
    )

    summary = json.loads(result.summary)
    assert summary["terminate"] == "yes"
    assert summary["final_response"] == "Task completed"


def test_summary_terminal_planner_beats_terminal_helper() -> None:
    terminal_planner = json.dumps({"terminate": "yes", "final_response": "planner verdict", "is_assert": True, "is_passed": True, "assert_summary": "ok"})
    result = GraphChatResult(
        messages=[AIMessage(content=json.dumps({"terminate": "no", "next_step": "keep going"})), AIMessage(content=terminal_planner)],
        terminate="yes",
        last_helper_response="PASS: helper verdict",
    )

    assert json.loads(result.summary)["final_response"] == "planner verdict"


def test_summary_uses_helper_text_when_nothing_terminal_was_parsed() -> None:
    result = GraphChatResult(
        messages=[],
        terminate="yes",
        last_helper_response="plain helper narration without a verdict",
    )

    summary = json.loads(result.summary)
    assert summary == {"terminate": "yes", "final_response": "plain helper narration without a verdict"}


def test_summary_generic_message_when_no_helper_text_exists() -> None:
    result = GraphChatResult(messages=[], terminate="yes", last_helper_response="")

    assert json.loads(result.summary) == {"terminate": "yes", "final_response": "Test ended without planner JSON."}


# ── executor None-coercion: a blank helper run must not clobber history ───────


def test_executor_node_keeps_previous_helper_response_when_helper_returns_none(monkeypatch) -> None:
    async def run() -> None:
        hercules = _hercules()
        agent = FakeAgent("browser_nav_agent", [AIMessage(content="unused")])
        hercules.agents_map = {"browser_nav_agent": agent}

        async def returning_none(nav_agent: Any, task: str, agent_name: str) -> None:
            return None

        monkeypatch.setattr(hercules, "_run_nav_agent", returning_none)

        result = await hercules._executor_node(
            {
                "messages": [HumanMessage(content="root task")],
                "next_step": "check something",
                "target_helper": "browser",
                "step_token_log": [],
                "step_timings": [],
                "completed_step_signatures": [],
                "last_helper_response": "PASS: previous step verified",
                "current_url": "",
            }
        )

        assert result["last_helper_response"] == "PASS: previous step verified"
        assert result["messages"][-1].content == "[browser_nav_agent]: "

    asyncio.run(run())


def test_executor_node_replaces_previous_helper_response_with_fresh_result(monkeypatch) -> None:
    async def run() -> None:
        hercules = _hercules()
        agent = FakeAgent("browser_nav_agent", [AIMessage(content="unused")])
        hercules.agents_map = {"browser_nav_agent": agent}

        async def fresh_result(nav_agent: Any, task: str, agent_name: str) -> str:
            return "PASS: fresh outcome"

        monkeypatch.setattr(hercules, "_run_nav_agent", fresh_result)

        result = await hercules._executor_node(
            {
                "messages": [HumanMessage(content="root task")],
                "next_step": "check something else",
                "target_helper": "browser",
                "step_token_log": [],
                "step_timings": [],
                "completed_step_signatures": [],
                "last_helper_response": "PASS: previous step verified",
                "current_url": "",
            }
        )

        assert result["last_helper_response"] == "PASS: fresh outcome"

    asyncio.run(run())


# ── tool invocation goes through the Runnable seam with a direct fallback ─────


class _FakeAsyncSeamTool:
    """Tool object exposing the async Runnable seam plus its raw coroutine."""

    def __init__(self, result: str, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.seam_used = False

    async def _run(self) -> str:
        if self.error is not None:
            raise self.error
        return self.result

    @property
    def coroutine(self):
        return self._run

    async def ainvoke(self, tool_input: dict[str, Any]) -> str:
        self.seam_used = True
        return await self._run()


class _FakeSyncSeamTool:
    """Tool object exposing the sync Runnable seam plus its raw function."""

    def __init__(self, result: str) -> None:
        self.result = result
        self.seam_used = False

    def func(self, **_kwargs: Any) -> str:
        return self.result

    def invoke(self, tool_input: dict[str, Any]) -> str:
        self.seam_used = True
        return self.result


class _FallbackOnlyTool:
    """Tool object with no Runnable seam — direct function call fallback."""

    def __init__(self) -> None:
        self.calls = 0

    def func(self, **_kwargs: Any) -> str:
        self.calls += 1
        return "fallback result"


def test_execute_tool_call_prefers_async_runnable_seam() -> None:
    async def run() -> None:
        tool = _FakeAsyncSeamTool(result="seam result")
        result = await _hercules()._execute_tool_call(tool, "async_tool", {})

        assert result == "seam result"
        assert tool.seam_used is True

    asyncio.run(run())


def test_execute_tool_call_prefers_sync_runnable_seam() -> None:
    async def run() -> None:
        tool = _FakeSyncSeamTool(result="sync seam result")
        result = await _hercules()._execute_tool_call(tool, "sync_tool", {})

        assert result == "sync seam result"
        assert tool.seam_used is True

    asyncio.run(run())


def test_execute_tool_call_falls_back_without_runnable_seam() -> None:
    async def run() -> None:
        tool = _FallbackOnlyTool()
        result = await _hercules()._execute_tool_call(tool, "plain_tool", {})

        assert result == "fallback result"
        assert tool.calls == 1

    asyncio.run(run())


def test_execute_tool_call_reports_seam_errors_as_tool_errors() -> None:
    async def run() -> None:
        tool = _FakeAsyncSeamTool(result="unused", error=RuntimeError("boom"))
        result = await _hercules()._execute_tool_call(tool, "broken_tool", {})

        assert result == "[TOOL ERROR] broken_tool: boom"

    asyncio.run(run())


class _AttributeErrorAfterSideEffectTool:
    """Tool whose ainvoke runs the side effect and then raises AttributeError."""

    def __init__(self) -> None:
        self.calls = 0

    async def _run(self) -> str:
        self.calls += 1
        return "side effect done"

    @property
    def coroutine(self):
        return self._run

    async def ainvoke(self, tool_input: dict[str, Any]) -> str:
        await self._run()
        raise AttributeError("post-side-effect failure inside the tool")


def test_execute_tool_call_does_not_rerun_tool_when_seam_raises_attribute_error() -> None:
    # A post-side-effect AttributeError from the seam must be contained as a
    # [TOOL ERROR], never re-executed through the raw coroutine fallback.
    async def run() -> None:
        tool = _AttributeErrorAfterSideEffectTool()
        result = await _hercules()._execute_tool_call(tool, "flaky_tool", {})

        assert tool.calls == 1
        assert result.startswith("[TOOL ERROR]")

    asyncio.run(run())


def test_execute_tool_call_runs_bare_async_function_tool() -> None:
    # A bare `async def` tool object has no .coroutine/.func attributes: the
    # sync fallback calls it and must await the returned coroutine instead of
    # stringifying it into "<coroutine object ...>".
    async def run() -> None:
        calls = 0

        async def bare_async_tool() -> str:
            nonlocal calls
            calls += 1
            return "bare async result"

        result = await _hercules()._execute_tool_call(bare_async_tool, "bare_async_tool", {})

        assert result == "bare async result"
        assert calls == 1

    asyncio.run(run())


# ── mobile (Appium) gate: instantiation, validation and executor fallback ─────


def _force_appium_unavailable(monkeypatch) -> None:
    """DEVICE_MANAGER=appium while the Appium client cannot be found."""
    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "appium":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)


def _install_stub_appium_module(monkeypatch) -> None:
    """Minimal appium stub (technique from tests/conftest_mobile.py, not imported):
    a real ModuleSpec makes importlib.util.find_spec("appium") succeed."""
    module = types.ModuleType("appium")
    module.__spec__ = importlib.machinery.ModuleSpec("appium", None)
    monkeypatch.setitem(sys.modules, "appium", module)
    monkeypatch.setenv("DEVICE_MANAGER", "appium")


def test_mobile_agent_not_created_when_appium_client_missing(monkeypatch, caplog) -> None:
    async def run() -> None:
        _force_appium_unavailable(monkeypatch)
        hercules = _hercules()

        with caplog.at_level(logging.WARNING, logger="testzeus_hercules.utils.logger"):
            agent = hercules._create_mobile_nav_agent({"model": "gpt-4o", "api_key": "test-key"}, {}, None)

        assert agent is None, "a closed Appium gate must not create a half-constructed mobile agent"
        assert any("mobile agent not created" in record.getMessage() for record in caplog.records)

    asyncio.run(run())


def test_mobile_agent_created_and_mobile_helper_accepted_when_gate_open(monkeypatch) -> None:
    async def run() -> None:
        # Import the mobile package while the gate is closed so the gated tools
        # registration is not part of this test's behavior.
        importlib.import_module("testzeus_hercules.core.mobile")
        _install_stub_appium_module(monkeypatch)
        hercules = _hercules()

        agent = hercules._create_mobile_nav_agent({"model": "gpt-4o", "api_key": "test-key"}, {}, None)

        assert agent is not None
        assert getattr(agent, "agent_name", "") == "mobile_nav_agent"
        # The planner may route to "mobile" once the gate is open.
        SimpleHercules._validate_planner_decision({"terminate": "no", "next_step": "tap the button", "target_helper": "mobile"})

    asyncio.run(run())


def test_mobile_target_helper_is_contained_planner_failure_when_gate_closed(monkeypatch) -> None:
    async def run() -> None:
        _force_appium_unavailable(monkeypatch)
        # The planner's "mobile" decision must be rejected up front so no rounds
        # are spun against a missing agent.
        with pytest.raises(ValueError, match="mobile"):
            SimpleHercules._validate_planner_decision({"terminate": "no", "next_step": "tap the button", "target_helper": "mobile"})

        payload = json.dumps({"next_step": "tap the button", "target_helper": "mobile", "terminate": "no"})
        planner_llm = FakeLLM([AIMessage(content=payload), AIMessage(content=payload)])  # the corrective re-prompt gets the same invalid answer
        hercules = SimpleHercules(stake_id="mobile-gate", planner_max_chat_round=3)
        hercules.agents_map = {
            "planner_agent": SimpleNamespace(
                system_message="planner system",
                llm=planner_llm,
                on_planner_message=lambda _content: None,
            ),
            "browser_nav_agent": FakeAgent("browser_nav_agent", [AIMessage(content="unused ##TERMINATE TASK##")]),
        }
        hercules._graph = hercules._build_graph()

        result = await hercules.process_command("tap the mobile button")

        assert result is not None
        assert result.terminate == "yes"
        assert len(planner_llm.calls) == 2, "the invalid mobile decision gets one corrective re-prompt, then ends the graph"
        parsed = parse_agent_response(result.summary)
        assert parsed["is_passed"] is False
        assert "Invalid planner decision" in (parsed["final_response"] or "")

    asyncio.run(run())


def test_executor_warns_when_rerouting_missing_mobile_agent_to_browser(caplog) -> None:
    async def run() -> None:
        hercules = _hercules()
        hercules.agents_map = {
            "browser_nav_agent": FakeAgent("browser_nav_agent", [AIMessage(content="current_output: done\n##TERMINATE TASK##")]),
        }
        state = {
            "messages": [HumanMessage(content="root task")],
            "next_step": "tap the start button",
            "target_helper": "mobile",
            "step_token_log": [],
            "step_timings": [],
            "completed_step_signatures": [],
            "current_url": "",
        }

        with caplog.at_level(logging.WARNING, logger="testzeus_hercules.utils.logger"):
            result = await hercules._executor_node(state)

        assert "[browser_nav_agent]" in result["messages"][-1].content
        assert any("rerouting" in record.getMessage().lower() and "mobile" in record.getMessage().lower() for record in caplog.records)

    asyncio.run(run())


# ── terminal planner verdicts survive the summary extraction (junit e2e) ──────


class _JUnitFakeConf:
    """Minimal config stand-in for build_junit_xml (mirrors test_assertion_ledger_junit)."""

    def __init__(self, tmp_path) -> None:
        self.junit_base = tmp_path / "junit"
        self.junit_base.mkdir(parents=True, exist_ok=True)
        self.test_id = "default"

    def get_junit_xml_base_path(self) -> str:
        return str(self.junit_base)

    def get_mode(self) -> str:
        return "prod"

    def set_default_test_id(self, test_id: str = "default") -> None:
        self.test_id = test_id

    def get_default_test_id(self) -> str:
        return self.test_id


def test_terminal_planner_json_mentioning_sync_playwright_reports_green_junit(tmp_path, monkeypatch) -> None:
    """A terminal planner JSON mentioning sync_playwright was dropped by the
    non-planner substring heuristic, so the run reported
    "Test ended without planner JSON." (false RED). The verdict must survive
    summary extraction and render a GREEN junit testcase."""

    async def run() -> None:
        terminal = json.dumps(
            {
                "plan": "Verify the sync_playwright integration.",
                "next_step": "",
                "terminate": "yes",
                "final_response": "verified the sync_playwright integration",
                "is_assert": True,
                "assert_summary": "EXPECTED RESULT: sync_playwright integration verified.\nACTUAL RESULT: verified the sync_playwright integration.",
                "is_passed": True,
                "target_helper": "Not_Applicable",
            }
        )
        hercules = SimpleHercules(stake_id="planner-verdict", planner_max_chat_round=3)
        hercules.agents_map = {
            "planner_agent": SimpleNamespace(
                system_message="planner system",
                llm=FakeLLM([AIMessage(content=terminal)]),
                on_planner_message=lambda _content: None,
            ),
            **{
                name: FakeAgent(name, [AIMessage(content="unused ##TERMINATE TASK##")])
                for name in (
                    "browser_nav_agent",
                    "api_nav_agent",
                    "sec_nav_agent",
                    "sql_nav_agent",
                    "time_keeper_nav_agent",
                    "mcp_nav_agent",
                    "executor_nav_agent",
                )
            },
        }
        hercules._graph = hercules._build_graph()

        result = await hercules.process_command("verify the sync_playwright integration")

        assert result is not None
        summary = json.loads(result.summary)
        assert summary["terminate"] == "yes"
        assert summary["is_passed"] is True
        assert summary["final_response"] == "verified the sync_playwright integration"

        # The junit report built from the exported summary is GREEN.
        conf = _JUnitFakeConf(tmp_path)
        import testzeus_hercules.utils.junit_helper as junit_helper

        monkeypatch.setattr(junit_helper, "get_global_conf", lambda: conf)
        proofs = tmp_path / "proofs"
        proofs.mkdir(parents=True, exist_ok=True)
        junit_path = await build_junit_xml(
            summary,
            execution_time=1.0,
            cost_metric={},
            feature="Feature: Planner verdict",
            scenario="Scenario: sync_playwright verdict",
            proofs_path=str(proofs),
        )
        cases = parse_junit_testcases(junit_path)
        assert len(cases) == 1
        assert cases[0]["status"] == "passed"
        assert cases[0]["failure_message"] is None

    asyncio.run(run())
