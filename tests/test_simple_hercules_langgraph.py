import asyncio
import json
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from testzeus_hercules.core.simple_hercules import SimpleHercules
from testzeus_hercules.utils.llm_helper import GraphChatResult


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


def test_executor_routes_every_target_helper_to_expected_agent(monkeypatch) -> None:
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
        monkeypatch.setattr(hercules, "_query_memory", lambda _context: _async_value(""))

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


def test_helper_task_includes_current_url_and_dynamic_memory(monkeypatch) -> None:
    async def run() -> None:
        hercules = _hercules()
        agent = FakeAgent(
            "browser_nav_agent",
            [AIMessage(content="current_output: done\n##TERMINATE TASK##")],
        )
        hercules.agents_map = {"browser_nav_agent": agent}

        async def fake_query_memory(context: str) -> str:
            assert "Current Page: https://example.test/start" in context
            return "remembered context"

        monkeypatch.setattr(hercules, "_query_memory", fake_query_memory)

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
        assert "EXTRA INFORMATION: remembered context" in helper_task

    asyncio.run(run())


def test_helper_task_prefers_live_current_url(monkeypatch) -> None:
    async def run() -> None:
        hercules = _hercules()

        async def fake_live_url() -> str:
            return "https://example.test/live"

        async def fake_query_memory(context: str) -> str:
            assert "Current Page: https://example.test/live" in context
            assert "https://example.test/stale" not in context
            return ""

        monkeypatch.setattr(hercules, "_get_live_current_url", fake_live_url)
        monkeypatch.setattr(hercules, "_query_memory", fake_query_memory)

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


def test_executor_accumulates_nav_agent_token_usage(monkeypatch) -> None:
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
        monkeypatch.setattr(hercules, "_query_memory", lambda _context: _async_value(""))

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


async def _async_value(value: str) -> str:
    return value
