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
