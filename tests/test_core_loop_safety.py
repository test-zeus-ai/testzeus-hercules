"""Core loop-safety regression tests.

Covers five hardening fixes:
1. a crashing planner LLM (RuntimeError — provider outage class) terminates the
   graph with a failed result instead of aborting the whole run
   (simple_hercules), SingleCommandInputRunner.start always tears down (runner),
   and the __main__ scenario loop isolates a crashing scenario while still
   producing the merged junit/report artifacts;
2. tool_calls with id=None (OpenAI-compatible gateways) no longer crash the nav
   loop with a pydantic ValidationError;
3. context-limit detection matches Anthropic/Gemini phrasings without
   false-positives on bare "max_tokens" configuration errors;
4. notification hub: one raising listener never breaks delivery to the others
   and unregistering an unknown listener is a no-op;
5. a planner producing no executable progress terminates after a few turns
   instead of cycling to the round limit (default 500).

Turn-12 protocol-conformance regressions (offline, scripted fake LLMs):
6. the planner max-rounds branch appends a canonical terminal AIMessage so the
   exported summary matches the actual termination;
7. chat_history roles map system/user/assistant instead of collapsing
   SystemMessage into "assistant";
8. a JSON boolean terminate (true) terminates instead of reading as "no";
9. assertion-shaped terminal payloads pass through the assertion node (plain
   terminate-yes still ends immediately);
10. a refresh triggered by the LAST tool call no longer appends a
    "skipped 0 remaining tool call(s)" nag;
11. huge tool results are capped (_MAX_TOOL_RESULT_CHARS) before entering
    LLM history.

Everything runs offline: no network, no LLM, no browser.
"""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from typing import Any

import pytest
import testzeus_hercules.__main__ as main_module
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from testzeus_hercules.__main__ import sequential_process
from testzeus_hercules.core.notification_manager import NotificationManager
from testzeus_hercules.core.runner import SingleCommandInputRunner
from testzeus_hercules.core.simple_hercules import SimpleHercules
from testzeus_hercules.reporting.bundle import parse_junit_testcases
from testzeus_hercules.utils.llm_helper import parse_agent_response

FEATURE_TEXT = """Feature: Loop Safety

  Scenario: Placeholder
    Given the user is on the home page
"""


# ── stubs ─────────────────────────────────────────────────────────────────────


class _PlannerLLMStub:
    """Planner LLM stub: raises when given an exception, else plays payloads."""

    def __init__(self, payloads: list[str] | None = None, exc: Exception | None = None) -> None:
        self.payloads = list(payloads or [])
        self.exc = exc
        self.calls = 0

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        if self.payloads:
            return AIMessage(content=self.payloads.pop(0))
        return AIMessage(content=json.dumps({"terminate": "yes", "final_response": "done", "is_assert": True, "is_passed": True, "assert_summary": "ok"}))


class _NavLLM:
    """Nav-agent LLM stub with bind_tools support and scripted responses."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[list[Any]] = []

    def bind_tools(self, tools: list[Any]) -> "_NavLLM":
        return self

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.calls.append(messages)
        if self.responses:
            return self.responses.pop(0)
        return AIMessage(content="current_output: done\n##TERMINATE TASK##")


def _stub_nav_agents() -> dict[str, Any]:
    """Nav agents whose bare LLM call immediately terminates the task."""
    agents: dict[str, Any] = {}
    for name in (
        "browser_nav_agent",
        "api_nav_agent",
        "sec_nav_agent",
        "sql_nav_agent",
        "time_keeper_nav_agent",
        "mcp_nav_agent",
        "executor_nav_agent",
    ):
        agents[name] = SimpleNamespace(
            agent_name=name,
            system_message=f"{name} system",
            llm=_NavLLM(),
            tools=[],
        )
    return agents


def _make_hercules(planner_llm: Any, planner_rounds: int = 500) -> SimpleHercules:
    """SimpleHercules with a real compiled graph over stubbed agents."""
    hercules = SimpleHercules(stake_id="loop-safety", planner_max_chat_round=planner_rounds, browser_nav_max_chat_round=2)
    hercules.agents_map = {
        "planner_agent": SimpleNamespace(
            system_message="planner system",
            llm=planner_llm,
            on_planner_message=lambda _content: None,
        ),
        **_stub_nav_agents(),
    }
    hercules._graph = hercules._build_graph()
    return hercules


class _StubBrowserManager:
    """Browser manager stub recording the teardown calls the runner must make."""

    def __init__(self, stake_id: str) -> None:
        self.stake_id = stake_id
        self._screenshots_dir = ""
        self.request_response_log_file = ""
        self.stop_calls = 0

    async def get_current_url(self) -> str:
        return "https://example.test/page"

    async def update_processing_state(self, _state: str) -> None:
        return None

    async def command_completed(self, _command: str, _elapsed: float) -> None:
        return None

    async def stop_playwright(self) -> None:
        self.stop_calls += 1

    def get_latest_video_path(self) -> str:
        return ""


class _FakeConf:
    """Minimal stand-in for the global config used by __main__ and junit_helper."""

    def __init__(self, tmp_path) -> None:
        self.junit_base = tmp_path / "output"
        self.junit_base.mkdir(parents=True, exist_ok=True)
        input_dir = tmp_path / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        self.input_path = input_dir / "test.feature"
        self.input_path.write_text(FEATURE_TEXT, encoding="utf-8")
        self.tmp_dir = str(tmp_path)

    def get_dont_close_browser(self) -> bool:
        return False

    def get_input_gherkin_file_path(self) -> str:
        return str(self.input_path)

    def get_junit_xml_base_path(self) -> str:
        return str(self.junit_base)

    def get_proof_path(self, test_id=None) -> str:
        return os.path.join(self.tmp_dir, "proofs", test_id or "default")

    def get_source_log_folder_path(self, test_id=None) -> str:
        path = os.path.join(self.tmp_dir, "log_files", test_id or "default")
        os.makedirs(path, exist_ok=True)
        return path

    def get_token_verbose(self) -> bool:
        return False

    def get_mode(self) -> str:
        return "prod"

    def set_default_test_id(self, test_id: str = "default") -> None:
        return None


def _install_two_scenario_run(monkeypatch, tmp_path, runner_class) -> _FakeConf:
    """Point sequential_process at two scenarios running through runner_class."""

    async def fake_process_feature_file(dont_append_header: bool = False):
        return [
            {
                "output_file": str(tmp_path / "gherkin_files" / "scenario_one.feature"),
                "feature": "Feature: Loop Safety",
                "scenario": "Scenario: First scenario",
            },
            {
                "output_file": str(tmp_path / "gherkin_files" / "scenario_two.feature"),
                "feature": "Feature: Loop Safety",
                "scenario": "Scenario: Second scenario",
            },
        ]

    async def fake_serialize_feature_file(file_path: str) -> str:
        return "command text"

    conf = _FakeConf(tmp_path)
    monkeypatch.setattr(main_module, "get_global_conf", lambda: conf)
    monkeypatch.setattr(main_module, "process_feature_file", fake_process_feature_file)
    monkeypatch.setattr(main_module, "serialize_feature_file", fake_serialize_feature_file)
    monkeypatch.setattr(main_module, "SingleCommandInputRunner", runner_class)
    # build_junit_xml / merge_junit_xml resolve the junit base path through this
    # module-level name, so patch it to the same fake conf.
    import testzeus_hercules.utils.junit_helper as junit_helper

    monkeypatch.setattr(junit_helper, "get_global_conf", lambda: conf)
    return conf


# ── finding 1a: planner LLM crash cannot abort the run ────────────────────────


def test_process_command_survives_planner_llm_crash() -> None:
    async def run() -> None:
        planner_llm = _PlannerLLMStub(exc=RuntimeError("provider connection dropped"))
        hercules = _make_hercules(planner_llm)

        result = await hercules.process_command("check the login page")

        assert result is not None, "a planner crash must return a failed result, not raise"
        assert result.terminate == "yes"
        parsed = parse_agent_response(result.summary)
        assert parsed["terminate"] == "yes"
        assert parsed["is_passed"] is False
        assert "provider connection dropped" in (parsed["final_response"] or "")

    asyncio.run(run())


# ── finding 1b: runner start() tears down even when the body raises ───────────


def test_runner_start_cleans_up_when_body_raises(monkeypatch) -> None:
    async def run() -> None:
        runner = SingleCommandInputRunner(stake_id="s1", command="do something")
        cleaned: list[str] = []

        async def failing_initialize() -> None:
            raise RuntimeError("config missing")

        async def recording_clean_up() -> None:
            cleaned.append("cleaned")

        monkeypatch.setattr(runner, "initialize", failing_initialize)
        monkeypatch.setattr(runner, "clean_up", recording_clean_up)

        with pytest.raises(RuntimeError, match="config missing"):
            await runner.start()

        assert cleaned == ["cleaned"], "start() must run clean_up even when the body raises"
        assert runner.shutdown_event.is_set(), "shutdown_event must be set so callers cannot hang"

    asyncio.run(run())


def test_runner_start_terminates_even_when_exit_path_fails(monkeypatch) -> None:
    async def run() -> None:
        runner = SingleCommandInputRunner(stake_id="s2", command="do something")
        real_process_command = runner.process_command

        async def failing_initialize() -> None:
            raise RuntimeError("boom")

        async def flaky_process_command(command: str):
            if command.lower() == "exit":
                raise RuntimeError("exit path exploded")
            return await real_process_command(command)

        monkeypatch.setattr(runner, "initialize", failing_initialize)
        monkeypatch.setattr(runner, "process_command", flaky_process_command)

        with pytest.raises(RuntimeError, match="boom"):
            await runner.start()

        assert runner.shutdown_event.is_set(), "teardown must complete even when the exit path raises"

    asyncio.run(run())


# ── finding 1c: the scenario loop isolates a crashing scenario ────────────────


def test_scenario_loop_isolates_crashing_scenario(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENABLE_RUN_REPORTING", "true")
    calls = {"clean_up": 0}

    class _ExplodingRunner:
        def __init__(self, stake_id: str, command: str, dont_terminate_browser_after_run: bool = False) -> None:
            self.stake_id = stake_id
            self.command = command
            self.result = None
            self.execution_time = 0.0
            self.browser_manager = None  # never got to initialize

        async def clean_up(self) -> None:
            calls["clean_up"] += 1

        async def start(self) -> None:
            try:
                raise RuntimeError(f"runner exploded for {self.stake_id}")
            except Exception:
                await self.clean_up()
                raise

    _install_two_scenario_run(monkeypatch, tmp_path, _ExplodingRunner)

    summary = asyncio.run(sequential_process())

    # The loop ran both scenarios instead of aborting at the first crash...
    assert calls["clean_up"] == 2
    # ...recorded both as failed junit entries...
    assert summary["total"] == 2
    assert summary["failed"] == 2
    assert summary["passed"] == 0
    # ...and still produced the merged artifacts.
    assert os.path.isfile(summary["junit_xml"])
    assert summary["report_md"] is not None
    assert os.path.isfile(summary["report_md"])

    cases = parse_junit_testcases(summary["junit_xml"])
    assert [case["name"] for case in cases] == ["Scenario: First scenario", "Scenario: Second scenario"]
    assert all(case["status"] == "failed" for case in cases)
    assert any("runner exploded" in (case["failure_message"] or "") for case in cases)


# ── finding 1 (end to end): crashing planner LLM through the real runner + loop ──


def test_scenario_loop_completes_all_scenarios_when_planner_llm_crashes(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENABLE_RUN_REPORTING", "true")
    created: list[SingleCommandInputRunner] = []

    class _CrashingPlannerRunner(SingleCommandInputRunner):
        """Real runner whose initialize() installs stubbed agents with a crashing planner LLM."""

        async def initialize(self) -> None:
            planner_llm = _PlannerLLMStub(exc=RuntimeError("provider connection dropped"))
            hercules = SimpleHercules(stake_id=self.stake_id, planner_max_chat_round=6, browser_nav_max_chat_round=2)
            hercules.agents_map = {
                "planner_agent": SimpleNamespace(
                    system_message="planner system",
                    llm=planner_llm,
                    on_planner_message=lambda _content: None,
                ),
                **_stub_nav_agents(),
            }
            hercules._graph = hercules._build_graph()
            self.simple_hercules = hercules
            self.browser_manager = _StubBrowserManager(self.stake_id)
            created.append(self)

    _install_two_scenario_run(monkeypatch, tmp_path, _CrashingPlannerRunner)

    summary = asyncio.run(sequential_process())

    assert len(created) == 2, "every scenario must be attempted"
    assert summary["total"] == 2
    assert summary["failed"] == 2
    assert os.path.isfile(summary["junit_xml"])
    assert summary["report_md"] is not None
    assert os.path.isfile(summary["report_md"])

    cases = parse_junit_testcases(summary["junit_xml"])
    assert [case["status"] for case in cases] == ["failed", "failed"]
    assert any("provider connection dropped" in (case["failure_message"] or "") for case in cases)

    # No browser-stop missing: the runner shutdown path stopped each browser.
    assert [runner.browser_manager.stop_calls for runner in created] == [1, 1]


# ── finding 2: tool_call id=None must not crash the nav loop ──────────────────


def test_tool_call_helpers_tolerate_none_id() -> None:
    hercules = SimpleHercules(stake_id="loop-safety")
    tool_call = {"id": None, "name": "click", "args": {}}

    assert hercules._tool_call_id(tool_call, "click") == "click"
    history = hercules._tool_call_for_history(tool_call)
    assert history["id"] == "click"


def test_nav_loop_survives_tool_calls_with_none_id() -> None:
    async def run() -> None:
        def click() -> str:
            return "clicked"

        llm = _NavLLM(
            [
                AIMessage(content="", tool_calls=[{"id": None, "name": "click", "args": {}}]),
                AIMessage(content="current_output: done\n##TERMINATE TASK##"),
            ]
        )
        agent = SimpleNamespace(
            agent_name="browser_nav_agent",
            system_message="nav system",
            llm=llm,
            tools=[StructuredTool.from_function(func=click, name="click", description="click")],
        )
        hercules = SimpleHercules(stake_id="loop-safety", browser_nav_max_chat_round=3)

        result = await hercules._run_nav_agent(agent, "click things", "browser_nav_agent")

        assert "##TERMINATE TASK##" in result
        tool_messages = [message for message in llm.calls[1] if isinstance(message, ToolMessage)]
        assert tool_messages, "the tool result must be fed back to the LLM"
        assert tool_messages[0].tool_call_id == "click"

    asyncio.run(run())


# ── finding 3: context-limit detection table ──────────────────────────────────


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Error code: 409 - context_length_exceeded", True),
        ("This model's maximum context length is 8192 tokens", True),
        ("Input token count 1200000 exceeds the maximum number of tokens allowed", True),  # Gemini phrasing
        ("litellm.BadRequestError: AnthropicError - your prompt is too long: 250000 tokens > 200000 maximum", True),  # litellm-wrapped Anthropic
        ("Request too large: please reduce the length of the messages", True),
        ("max_tokens: you requested 5000 tokens which exceeds the model's context limit", True),  # qualified max_tokens
        ("max_tokens is too large: 5000 > 4096", False),  # config error, NOT context overflow
        ("Invalid API key provided", False),
        ("Connection error while calling the provider", False),
    ],
)
def test_is_context_limit_error_table(message: str, expected: bool) -> None:
    hercules = SimpleHercules(stake_id="loop-safety")
    assert hercules._is_context_limit_error(RuntimeError(message)) is expected


# ── finding 4: notification hub isolation ─────────────────────────────────────


def test_notify_isolates_raising_listener() -> None:
    manager = NotificationManager()
    received: list[dict[str, str]] = []

    def bad_listener(_notification: dict[str, str]) -> None:
        raise ValueError("listener exploded")

    manager.register_listener(bad_listener)
    manager.register_listener(received.append)

    manager.notify("hello", "step_completed")

    assert received == [{"message": "hello", "type": "step_completed"}]


def test_unregister_unknown_listener_is_noop() -> None:
    manager = NotificationManager()

    def listener(_notification: dict[str, str]) -> None:
        pass

    manager.register_listener(listener)
    manager.unregister_listener(listener)
    manager.unregister_listener(listener)  # unknown this time — must not raise

    assert manager.listeners == []


# ── finding 5: zombie planner loop terminates after stalled turns ─────────────


def test_planner_zombie_loop_terminates_after_three_stalled_turns() -> None:
    async def run() -> None:
        planner_llm = _PlannerLLMStub(payloads=["this is not valid planner json"] * 5)
        hercules = _make_hercules(planner_llm, planner_rounds=500)

        result = await hercules.process_command("impossible task")

        assert result is not None
        assert result.terminate == "yes"
        assert planner_llm.calls == 3, "planner must stop after 3 stalled turns, not cycle to the round limit"
        parsed = parse_agent_response(result.summary)
        assert parsed["terminate"] == "yes"
        assert parsed["is_passed"] is False
        assert "no executable progress" in (parsed["final_response"] or "")

    asyncio.run(run())


def test_planner_stall_counter_resets_on_progress() -> None:
    async def run() -> None:
        productive_step = json.dumps({"next_step": "open the site", "target_helper": "browser", "terminate": "no"})
        termination = json.dumps({"terminate": "yes", "final_response": "done", "is_assert": True, "is_passed": True, "assert_summary": "ok"})
        planner_llm = _PlannerLLMStub(payloads=["garbage", productive_step, "garbage", "garbage", termination])
        hercules = _make_hercules(planner_llm, planner_rounds=500)

        result = await hercules.process_command("mixed planner behaviour")

        assert result is not None
        assert planner_llm.calls == 5, "productive planner turns must reset the stall counter"
        parsed = parse_agent_response(result.summary)
        assert parsed["terminate"] == "yes"
        assert parsed["is_passed"] is True

    asyncio.run(run())


# ── turn 12: planner max-rounds exports a summary matching the termination ────


def test_planner_max_rounds_terminates_with_matching_summary() -> None:
    async def run() -> None:
        payloads = [json.dumps({"next_step": f"open page {n}", "target_helper": "browser", "terminate": "no"}) for n in range(1, 4)]
        planner_llm = _PlannerLLMStub(payloads=payloads)
        hercules = _make_hercules(planner_llm, planner_rounds=2)

        result = await hercules.process_command("very long task")

        assert result is not None
        assert result.terminate == "yes"
        assert planner_llm.calls == 2, "the third turn must be refused by the round limit"
        parsed = parse_agent_response(result.summary)
        assert parsed["terminate"] == "yes", "exported summary must match the actual termination, not a stale payload"
        assert parsed["final_response"] == "Max planner rounds exceeded."
        assert parsed["is_passed"] is False
        assert "rounds" in (parsed["assert_summary"] or "")

    asyncio.run(run())


# ── turn 12: chat_history roles map system/user/assistant ─────────────────────


def test_chat_history_roles_map_system_user_and_assistant() -> None:
    async def run() -> None:
        terminal = json.dumps({"terminate": "yes", "final_response": "done", "is_assert": True, "is_passed": True, "assert_summary": "ok"})
        planner_llm = _PlannerLLMStub(payloads=[terminal])
        hercules = _make_hercules(planner_llm)

        result = await hercules.process_command("simple task")

        assert result is not None
        assert result.chat_history[0]["role"] == "system"
        assert result.chat_history[1]["role"] == "user"
        assert result.chat_history[-1]["role"] == "assistant"
        assert all(entry["role"] in {"system", "user", "assistant"} for entry in result.chat_history)

    asyncio.run(run())


# ── turn 12: a JSON boolean terminate (true) terminates immediately ───────────


def test_planner_boolean_true_terminate_terminates_immediately() -> None:
    async def run() -> None:
        planner_llm = _PlannerLLMStub(payloads=[json.dumps({"terminate": True, "final_response": "bool terminate", "is_assert": True, "is_passed": True, "assert_summary": "ok"})])
        hercules = _make_hercules(planner_llm)

        result = await hercules.process_command("bool terminate task")

        assert result is not None
        assert result.terminate == "yes"
        assert planner_llm.calls == 1, "terminate: true must end the run on the first planner turn"
        parsed = parse_agent_response(result.summary)
        assert parsed["terminate"] == "yes"
        assert parsed["final_response"] == "bool terminate"

    asyncio.run(run())


# ── turn 12: assertion-shaped terminal payloads pass through the assertion node ─


def test_assertion_shaped_terminate_passes_through_assertion_node() -> None:
    async def run() -> None:
        planner_llm = _PlannerLLMStub(payloads=[json.dumps({"terminate": "yes", "final_response": "login works", "is_assert": True, "is_passed": True, "assert_summary": "login verified"})])
        hercules = _make_hercules(planner_llm)

        result = await hercules.process_command("verify login")

        assert result is not None
        assert result.terminate == "yes"
        parsed = parse_agent_response(result.summary)
        assert parsed["is_passed"] is True
        assert parsed["assert_summary"] == "login verified"
        # The assertion node must have appended its canonical final-result JSON.
        ai_messages = [m for m in result.messages if isinstance(m, AIMessage)]
        assert ai_messages
        final_payload = json.loads(ai_messages[-1].content)
        assert final_payload["terminate"] == "yes"
        assert final_payload["is_passed"] is True
        assert final_payload["target_helper"] == "Not_Applicable", "the last AIMessage must come from the assertion node"

    asyncio.run(run())


def test_plain_terminate_yes_still_ends_immediately_without_assertion() -> None:
    async def run() -> None:
        planner_llm = _PlannerLLMStub(payloads=[json.dumps({"terminate": "yes", "final_response": "plain end"})])
        hercules = _make_hercules(planner_llm)

        result = await hercules.process_command("plain end task")

        assert result is not None
        assert result.terminate == "yes"
        assert planner_llm.calls == 1, "plain terminate must end immediately"
        ai_messages = [m for m in result.messages if isinstance(m, AIMessage)]
        assert len(ai_messages) == 1, "no assertion-node final JSON may be appended for a plain terminate"
        payload = json.loads(ai_messages[0].content)
        assert payload["terminate"] == "yes"
        assert "target_helper" not in payload, "the planner's own JSON must be the last message"

    asyncio.run(run())


# ── turn 12: refresh on the LAST tool call appends no "skipped 0" nag ─────────


def test_refresh_on_last_tool_call_appends_no_zero_skip_notice() -> None:
    async def run() -> None:
        calls: list[str] = []

        def read_dom() -> str:
            calls.append("read_dom")
            return "plain page text"

        def click() -> str:
            calls.append("click")
            return "clicked successfully"

        tools = [
            StructuredTool.from_function(func=read_dom, name="read_dom", description="read"),
            StructuredTool.from_function(func=click, name="click", description="click"),
        ]
        llm = _NavLLM(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "read_dom", "args": {}, "id": "call_1"},
                        {"name": "click", "args": {}, "id": "call_2"},
                    ],
                ),
                AIMessage(content="current_output: refreshed\n##TERMINATE TASK##"),
            ]
        )
        agent = SimpleNamespace(agent_name="browser_nav_agent", system_message="nav system", llm=llm, tools=tools)
        hercules = SimpleHercules(stake_id="loop-safety", browser_nav_max_chat_round=3)

        result = await hercules._run_nav_agent(agent, "read then click", "browser_nav_agent")

        assert calls == ["read_dom", "click"], "both tool calls execute when the refresh is triggered by the last one"
        assert "##TERMINATE TASK##" in result
        second_call_messages = llm.calls[1]
        assert not any(
            isinstance(message, HumanMessage) and "browser state changed" in message.content for message in second_call_messages
        ), "a refresh on the final tool call must not append a skipped-0 nag"
        # The rebuilt history stays valid: one AI message with both tool calls, both answered.
        rebuilt_ai = [m for m in second_call_messages if isinstance(m, AIMessage) and m.tool_calls]
        tool_messages = [m for m in second_call_messages if isinstance(m, ToolMessage)]
        assert len(rebuilt_ai) == 1 and len(rebuilt_ai[0].tool_calls) == 2
        assert [m.tool_call_id for m in tool_messages] == ["call_1", "call_2"]

    asyncio.run(run())


# ── huge tool results are capped before entering LLM history ─────────────────


def test_huge_tool_result_is_capped_before_entering_history() -> None:
    async def run() -> None:
        def dump() -> str:
            return "x" * 100_000

        llm = _NavLLM(
            [
                AIMessage(content="", tool_calls=[{"name": "dump", "args": {}, "id": "call_1"}]),
                AIMessage(content="current_output: done\n##TERMINATE TASK##"),
            ]
        )
        agent = SimpleNamespace(
            agent_name="api_nav_agent",
            system_message="nav system",
            llm=llm,
            tools=[StructuredTool.from_function(func=dump, name="dump", description="dump")],
        )
        hercules = SimpleHercules(stake_id="loop-safety", browser_nav_max_chat_round=3)

        result = await hercules._run_nav_agent(agent, "dump a huge payload", "api_nav_agent")

        assert "##TERMINATE TASK##" in result, "the loop must continue normally after truncation"
        tool_messages = [m for m in llm.calls[1] if isinstance(m, ToolMessage)]
        assert tool_messages
        content = tool_messages[0].content
        assert len(content) <= SimpleHercules._MAX_TOOL_RESULT_CHARS + 100
        assert content.endswith("...[truncated, 100000 chars total]")

    asyncio.run(run())


# ── malformed planner decisions end in the failed-planner terminal state ──────


def test_validate_planner_decision_rejects_invalid_payloads() -> None:
    validate = SimpleHercules._validate_planner_decision

    with pytest.raises(ValueError, match="terminate"):
        validate({"terminate": "true", "next_step": "click"})  # string "true" is not yes/no
    with pytest.raises(ValueError, match="terminate"):
        validate({"terminate": 5, "next_step": "click"})
    with pytest.raises(ValueError, match="is_assert"):
        validate({"terminate": "no", "next_step": "click", "is_assert": "yes"})
    with pytest.raises(ValueError, match="is_passed"):
        validate({"terminate": "no", "next_step": "click", "is_passed": 1})
    with pytest.raises(ValueError, match="target_helper"):
        validate({"terminate": "no", "next_step": "click", "target_helper": "nonexistent_helper"})
    with pytest.raises(ValueError, match="next_step"):
        validate({"terminate": "no", "next_step": "", "plan": "do things"})
    with pytest.raises(ValueError, match="next_step"):
        validate({"plan": "plan without a step", "terminate": "no"})


def test_validate_planner_decision_accepts_valid_payloads() -> None:
    validate = SimpleHercules._validate_planner_decision

    validate({"terminate": "yes", "final_response": "done"})
    validate({"terminate": True, "final_response": "done", "is_assert": True, "is_passed": True, "assert_summary": "ok"})
    validate({"terminate": "no", "next_step": "click the button", "target_helper": "browser"})
    validate({"terminate": "no", "next_step": "run query", "target_helper": "Not_Applicable"})
    validate({"terminate": "no", "next_step": "scan", "target_helper": "sec", "is_assert": False, "is_passed": False})


def test_invalid_planner_decision_ends_in_failed_planner_state() -> None:
    async def run() -> None:
        planner_llm = _PlannerLLMStub(payloads=[json.dumps({"next_step": "open the site", "target_helper": "does_not_exist", "terminate": "no"})])
        hercules = _make_hercules(planner_llm)

        result = await hercules.process_command("misrouted task")

        assert result is not None, "an invalid decision must fail the scenario, not crash the graph"
        assert result.terminate == "yes"
        assert planner_llm.calls == 1
        parsed = parse_agent_response(result.summary)
        assert parsed["terminate"] == "yes"
        assert parsed["is_passed"] is False
        assert "Invalid planner decision" in (parsed["final_response"] or "")

    asyncio.run(run())


def test_unparseable_planner_output_keeps_existing_stall_handling() -> None:
    async def run() -> None:
        # Garbage without planner keys is NOT an invalid decision: the zombie
        # stall guard still owns that path (3 turns, then a failed terminal).
        planner_llm = _PlannerLLMStub(payloads=["this is not valid planner json"] * 5)
        hercules = _make_hercules(planner_llm)

        result = await hercules.process_command("garbage planner task")

        assert result is not None
        assert result.terminate == "yes"
        assert planner_llm.calls == 3, "stall handling must still stop the loop, not validation"
        parsed = parse_agent_response(result.summary)
        assert parsed["is_passed"] is False
        assert "no executable progress" in (parsed["final_response"] or "")

    asyncio.run(run())


# ── terminate-aware target_helper validation ──────────────────────────────────


def test_validate_planner_decision_tolerates_empty_target_helper_on_terminate_yes() -> None:
    validate = SimpleHercules._validate_planner_decision

    # Once terminate is "yes" the helper is never used for routing: an empty
    # target_helper is valid and normalized to the Not_Applicable equivalent.
    payload = {"terminate": "yes", "is_assert": True, "is_passed": True, "final_response": "verified", "target_helper": ""}
    validate(payload)
    assert payload["target_helper"] == "Not_Applicable"

    # A missing key stays missing (no mutation) and a None value is tolerated.
    missing = {"terminate": "yes", "final_response": "done"}
    validate(missing)
    assert "target_helper" not in missing
    validate({"terminate": True, "final_response": "done", "is_assert": True, "is_passed": True, "target_helper": None})

    # Strict validation is kept while terminate is "no".
    with pytest.raises(ValueError, match="target_helper"):
        validate({"terminate": "no", "next_step": "click", "target_helper": ""})
    with pytest.raises(ValueError, match="target_helper"):
        validate({"terminate": "no", "next_step": "click", "target_helper": "nonexistent_helper"})


def test_terminate_yes_with_empty_target_helper_reaches_pass_terminal() -> None:
    async def run() -> None:
        planner_llm = _PlannerLLMStub(
            payloads=[
                json.dumps(
                    {
                        "terminate": "yes",
                        "final_response": "login verified",
                        "is_assert": True,
                        "is_passed": True,
                        "assert_summary": "login verified",
                        "target_helper": "",
                    }
                )
            ]
        )
        hercules = _make_hercules(planner_llm)

        result = await hercules.process_command("verify login")

        assert result is not None
        assert result.terminate == "yes"
        parsed = parse_agent_response(result.summary)
        assert parsed["is_passed"] is True, "an empty target_helper must not void a terminal pass"
        assert parsed["final_response"] == "login verified"
        ai_messages = [m for m in result.messages if isinstance(m, AIMessage)]
        assert ai_messages
        final_payload = json.loads(ai_messages[-1].content)
        assert final_payload["target_helper"] == "Not_Applicable", "the terminal result must carry the normalized helper"

    asyncio.run(run())


# ── the invariant binding accumulator resets between commands and dedupes ─────


def test_invariant_binding_accumulator_resets_between_commands_and_dedupes(monkeypatch) -> None:
    async def run() -> None:
        import testzeus_hercules.core.simple_hercules as simple_hercules_module

        finding = {"kind": "http_status", "detail": "HTTP 500 on /api/cart", "url": "https://example.test/api/cart"}
        poll_calls = {"count": 0}

        def fake_poll_invariants(test_id: str, proof_path: Any = None) -> dict[str, list[dict[str, Any]]]:
            poll_calls["count"] += 1
            if poll_calls["count"] <= 2:
                # The same finding is re-read by a second poll within the FIRST
                # command — it must not inflate the accumulator.
                return {"binding": [dict(finding)], "notice": []}
            return {"binding": [], "notice": []}

        monkeypatch.setattr(simple_hercules_module, "poll_invariants", fake_poll_invariants)
        monkeypatch.setattr(simple_hercules_module, "record_invariant_findings", lambda *args, **kwargs: None)

        step = json.dumps({"next_step": "open the cart page", "target_helper": "browser", "terminate": "no"})
        first_termination = json.dumps({"terminate": "yes", "final_response": "first scenario done", "is_assert": True, "is_passed": True, "assert_summary": "ok"})
        second_termination = json.dumps({"terminate": "yes", "final_response": "second scenario done", "is_assert": True, "is_passed": True, "assert_summary": "ok"})
        planner_llm = _PlannerLLMStub(payloads=[step, step, first_termination, step, second_termination])
        hercules = _make_hercules(planner_llm)

        first_result = await hercules.process_command("first scenario")

        assert first_result is not None
        parsed = parse_agent_response(first_result.summary)
        assert parsed["is_passed"] is False, "a binding invariant finding marks the scenario failed"
        assert len(hercules._invariant_binding) == 1, "re-read duplicates of the same finding must not inflate counts"
        assert "Observer invariants flagged 1 binding finding" in (parsed["final_response"] or "")

        second_result = await hercules.process_command("second scenario")

        assert second_result is not None
        parsed = parse_agent_response(second_result.summary)
        assert parsed["is_passed"] is True, "a clean second command must pass — the accumulator was reset"
        assert hercules._invariant_binding == []

    asyncio.run(run())


def test_invariant_binding_dedupes_duplicates_within_one_poll(monkeypatch) -> None:
    async def run() -> None:
        import testzeus_hercules.core.simple_hercules as simple_hercules_module

        finding = {"kind": "http_status", "detail": "HTTP 500 on /api/cart", "url": "https://example.test/api/cart"}

        def fake_poll_invariants(test_id: str, proof_path: Any = None) -> dict[str, list[dict[str, Any]]]:
            # The SAME finding twice inside ONE poll payload must count once.
            return {"binding": [dict(finding), dict(finding)], "notice": []}

        monkeypatch.setattr(simple_hercules_module, "poll_invariants", fake_poll_invariants)
        monkeypatch.setattr(simple_hercules_module, "record_invariant_findings", lambda *args, **kwargs: None)

        step = json.dumps({"next_step": "open the cart page", "target_helper": "browser", "terminate": "no"})
        termination = json.dumps({"terminate": "yes", "final_response": "scenario done", "is_assert": True, "is_passed": True, "assert_summary": "ok"})
        planner_llm = _PlannerLLMStub(payloads=[step, termination])
        hercules = _make_hercules(planner_llm)

        result = await hercules.process_command("single scenario")

        assert result is not None
        parsed = parse_agent_response(result.summary)
        assert parsed["is_passed"] is False, "a binding invariant finding marks the scenario failed"
        assert len(hercules._invariant_binding) == 1, "duplicates inside one poll payload must not inflate counts"
        assert "Observer invariants flagged 1 binding finding" in (parsed["final_response"] or "")

    asyncio.run(run())
