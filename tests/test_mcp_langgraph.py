import asyncio
from types import SimpleNamespace
from typing import Any

from testzeus_hercules.core.agents.base_nav_agent import BaseNavAgent
from testzeus_hercules.core.agents.mcp_nav_agent import McpNavAgent
from testzeus_hercules.utils.mcp_help import MCPHelper


def test_mcp_nav_agent_awaits_tool_registration(monkeypatch) -> None:
    async def run() -> None:
        agent = McpNavAgent.__new__(McpNavAgent)
        agent.tools = []

        def fake_load_tools() -> None:
            agent.tools = ["builtin"]

        monkeypatch.setattr(agent, "load_tools", fake_load_tools)
        McpNavAgent.register_tools(agent)

        calls: list[Any] = []

        async def fake_register_agent_tools(nav_agent: Any) -> bool:
            calls.append(nav_agent)
            nav_agent.tools.append("external")
            return True

        helper = MCPHelper()
        monkeypatch.setattr(MCPHelper, "instance", classmethod(lambda cls: helper))
        monkeypatch.setattr(helper, "register_agent_tools", fake_register_agent_tools)

        assert await agent.ensure_tools_ready() is True
        assert calls == [agent]
        assert agent.tools == ["builtin", "external"]

    asyncio.run(run())


def test_mcp_dynamic_tool_returns_explicit_failure(monkeypatch) -> None:
    async def run() -> None:
        helper = MCPHelper()
        helper._mcp_toolkits["local"] = SimpleNamespace(tools=[SimpleNamespace(name="explode", description="boom")])
        nav_agent = SimpleNamespace(tools=[])

        async def fake_execute_mcp_tool(server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return {"success": False, "error": "kaboom"}

        monkeypatch.setattr(helper, "execute_mcp_tool", fake_execute_mcp_tool)
        helper._attach_tools_to_agent(nav_agent)

        result = await nav_agent.tools[0].coroutine()

        assert result == "[MCP TOOL ERROR] local.explode: kaboom"

    asyncio.run(run())


def test_mcp_dynamic_tool_preserves_input_schema(monkeypatch) -> None:
    async def run() -> None:
        helper = MCPHelper()
        helper._mcp_toolkits["local"] = SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="search",
                    description="search docs",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query"},
                            "limit": {
                                "type": "integer",
                                "description": "Maximum results",
                            },
                        },
                        "required": ["query"],
                    },
                )
            ]
        )
        nav_agent = SimpleNamespace(tools=[])
        seen_args: list[dict[str, Any]] = []

        async def fake_execute_mcp_tool(server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            seen_args.append(arguments)
            return {"success": True, "result": "done"}

        monkeypatch.setattr(helper, "execute_mcp_tool", fake_execute_mcp_tool)
        helper._attach_tools_to_agent(nav_agent)

        tool = nav_agent.tools[0]
        assert tool.args_schema["properties"]["query"]["type"] == "string"
        assert tool.args_schema["properties"]["limit"]["type"] == "integer"
        assert tool.args_schema["required"] == ["query"]

        result = await tool.ainvoke({"query": "accounts", "limit": 3})

        assert result == "done"
        assert seen_args == [{"query": "accounts", "limit": 3}]

    asyncio.run(run())


def test_mcp_destroy_swallows_disconnect_errors() -> None:
    async def run() -> None:
        helper = MCPHelper()

        async def broken_disconnect() -> None:
            raise RuntimeError("shutdown failed")

        helper._disconnect_all_servers = broken_disconnect  # type: ignore[method-assign]
        MCPHelper._instance = helper

        assert await MCPHelper.destroy() is False
        assert MCPHelper._instance is None

    asyncio.run(run())


def test_mcp_shutdown_awaits_base_shutdown(monkeypatch) -> None:
    async def run() -> None:
        agent = McpNavAgent.__new__(McpNavAgent)
        calls: list[str] = []

        async def fake_base_shutdown(self: BaseNavAgent) -> None:
            calls.append("base")

        async def fake_destroy() -> bool:
            calls.append("mcp")
            return True

        monkeypatch.setattr(BaseNavAgent, "shutdown", fake_base_shutdown)
        monkeypatch.setattr(MCPHelper, "destroy", fake_destroy)

        await McpNavAgent.shutdown(agent)

        assert calls == ["mcp", "base"]

    asyncio.run(run())


# ── explicit isError failure semantics ────────────────────────────────────────


class _FakeSession:
    """Session stub whose call_tool returns a fixed result object."""

    def __init__(self, call_result: Any) -> None:
        self._call_result = call_result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool_name, arguments))
        return self._call_result


def test_mcp_is_error_result_returns_unknown_execution_status() -> None:
    async def run() -> None:
        helper = MCPHelper()
        error_content = SimpleNamespace(text="the operation blew up")
        helper._mcp_sessions["srv"] = _FakeSession(SimpleNamespace(content=[error_content], isError=True))

        result = await helper.execute_mcp_tool("srv", "mutate_thing", {"id": "1"})

        assert result["success"] is False
        assert result["error"] == "the operation blew up"
        assert result["server"] == "srv"
        assert result["tool"] == "mutate_thing"
        assert result["execution_status"] == "unknown", "a tool error must never claim the effect is known"
        guidance = result["recovery_guidance"]
        assert "UNKNOWN" in guidance
        assert "verify" in guidance.lower()
        assert "retry" in guidance.lower()

    asyncio.run(run())


def test_mcp_success_result_has_no_unknown_status() -> None:
    async def run() -> None:
        helper = MCPHelper()
        helper._mcp_sessions["srv"] = _FakeSession(SimpleNamespace(content=[SimpleNamespace(text="all good")], isError=False))

        result = await helper.execute_mcp_tool("srv", "query_thing", {})

        assert result["success"] is True
        assert result["result"] == "all good"
        assert "execution_status" not in result
        assert "recovery_guidance" not in result

    asyncio.run(run())


def test_mcp_dynamic_tool_surfaces_unknown_outcome_guidance(monkeypatch) -> None:
    async def run() -> None:
        helper = MCPHelper()
        helper._mcp_toolkits["local"] = SimpleNamespace(tools=[SimpleNamespace(name="mutate", description="changes state")])
        nav_agent = SimpleNamespace(tools=[])

        async def fake_execute_mcp_tool(server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return {
                "success": False,
                "error": "connection reset",
                "execution_status": "unknown",
                "recovery_guidance": "verify whether the operation took effect before retrying",
            }

        monkeypatch.setattr(helper, "execute_mcp_tool", fake_execute_mcp_tool)
        helper._attach_tools_to_agent(nav_agent)

        result = await nav_agent.tools[0].coroutine()

        assert result.startswith("[MCP TOOL ERROR] local.mutate: connection reset")
        assert "verify whether the operation took effect" in result, "the observation must carry the recovery guidance"

    asyncio.run(run())


def test_mcp_nav_agent_prompt_warns_about_unknown_outcomes() -> None:
    prompt = McpNavAgent.prompt

    assert "UNKNOWN" in prompt, "the prompt must state that a failed operation's effect is unknown"
    assert "INCOMPLETE" in prompt, "the prompt must require reporting failures as INCOMPLETE/FAILED"
    assert "never present a failed helper task as success" in prompt.lower()


# ── transport exceptions carry the same unknown-outcome semantics ─────────────


class _ExplodingSession:
    """Session stub whose call_tool raises, simulating a mid-call transport failure."""

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        raise ConnectionError("connection reset mid-call")


def test_mcp_transport_exception_returns_unknown_execution_status() -> None:
    async def run() -> None:
        helper = MCPHelper()

        # Baseline: the isError path's guidance, which the exception path must reuse.
        error_content = SimpleNamespace(text="the operation blew up")
        helper._mcp_sessions["srv"] = _FakeSession(SimpleNamespace(content=[error_content], isError=True))
        error_result = await helper.execute_mcp_tool("srv", "mutate_thing", {"id": "1"})

        helper._mcp_sessions["srv"] = _ExplodingSession()
        result = await helper.execute_mcp_tool("srv", "mutate_thing", {"id": "1"})

        assert result["success"] is False
        assert "connection reset mid-call" in result["error"]
        assert result["server"] == "srv"
        assert result["tool"] == "mutate_thing"
        assert result["execution_status"] == "unknown", "a mid-call transport failure must never claim the effect is known"
        assert result["recovery_guidance"] == error_result["recovery_guidance"], "the exception path must reuse the tool-failure guidance"
        guidance = result["recovery_guidance"]
        assert "UNKNOWN" in guidance
        assert "verify" in guidance.lower()
        assert "retry" in guidance.lower()

    asyncio.run(run())


# ── stdio args must reach the SDK as strings ──────────────────────────────────


def test_mcp_stdio_args_are_stringified(monkeypatch) -> None:
    captured: list[Any] = []

    def fake_stdio_client(server_params: Any) -> Any:
        captured.append(server_params)

        class _AbortBeforeLaunch:
            async def __aenter__(self) -> Any:
                raise RuntimeError("abort before any process is launched")

            async def __aexit__(self, *exc_info: Any) -> bool:
                return False

        return _AbortBeforeLaunch()

    monkeypatch.setattr("testzeus_hercules.utils.mcp_help.stdio_client", fake_stdio_client)

    async def run() -> None:
        helper = MCPHelper()
        connected = await helper.connect_mcp_server(
            "srv",
            {"transport": "stdio", "command": "python", "args": ["--count", 42, "--mode=fast"]},
        )
        assert connected is False  # the fake transport aborts before initialize by design

    asyncio.run(run())

    assert len(captured) == 1, "the server params must be constructed before the transport aborts"
    args = list(captured[0].args)
    assert args == ["--count", "42", "--mode=fast"], "every stdio arg must be a string, like env values"
    assert all(isinstance(arg, str) for arg in args)
