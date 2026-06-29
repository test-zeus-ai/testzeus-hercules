"""MCP (Model Context Protocol) tools for server communication and tool execution.

This module provides a singleton class `MCPHelper` that encapsulates all MCP
connection, toolkit, and tool-execution logic. Thin tool wrappers are exposed
via the `@tool` decorator which delegate to the singleton instance.
"""

import asyncio
import copy
from datetime import timedelta
from typing import Any, Dict, List, Optional, cast
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from langchain_core.tools import StructuredTool

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.langchain_tools import merge_tools
from testzeus_hercules.utils.logger import logger

#from mcp_helper import get_mcp_config, set_mcp_config

def main():
    config = get_mcp_config()
      # Start server logic here
    print(f"Starting server with config: {config}")

    if __name__ == "__main__":
      main()

class MCPToolkit:
    """Lightweight MCP tool catalog for LangGraph agents."""

    def __init__(self, tools: list[Any]) -> None:
        self.tools = tools



class MCPHelper:
    """Singleton manager for MCP connections, toolkits, and tool execution."""

    _instance: Optional["MCPHelper"] = None

    def __init__(self) -> None:
        self._mcp_toolkits: Dict[str, Any] = {}
        self._mcp_sessions: Dict[str, ClientSession] = {}
        self._mcp_client_contexts: Dict[str, Any] = {}
        self._mcp_http_clients: Dict[str, httpx.AsyncClient] = {}

        self._nav_agent: Optional[Any] = None

    @classmethod
    def instance(cls) -> "MCPHelper":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = MCPHelper()
        return cls._instance

    @classmethod
    async def destroy(cls) -> bool:
        """Destroy the singleton instance and disconnect from all MCP servers."""
        if cls._instance is None:
            return True
        success = True
        try:
            await cls._instance._disconnect_all_servers()
        except (Exception, asyncio.CancelledError) as e:
            success = False
            logger.warning("Error while destroying MCP helper: %s", e)
        finally:
            cls._instance = None
        return success

    async def register_agent_tools(self, nav_agent: Any) -> bool:
        """Connect MCP serveres and attach tools to a navigation agent."""
        self._nav_agent = nav_agent
        connection_result = await self.initialize_mcp_connections()
        if connection_result.get("success") and nav_agent is not None:
            self._attach_tools_to_agent(nav_agent)
        return bool(connection_result.get("success"))
    
    async def set_mcp_agents(self, llm_agent: Any, executor_agent: Any) -> bool:
        """Backward-compatible entry point for MCP registration."""
        return await self.register_agent_tools(llm_agent)

    @staticmethod
    def _get_tool_input_schema(mcp_tool: Any) -> dict[str, Any]:
        schema = None
        if isinstance(mcp_tool, dict):
            schema = mcp_tool.get("inputSchema") or mcp_tool.get("input_schema")
        else:
            schema = getattr(mcp_tool, "inputSchema", None) or getattr(mcp_tool, "input_schema", None)

        if hasattr(schema, "model_dump"):
            schema = schema.model_dump()
        if not isinstance(schema, dict):
            return {"type": "object", "properties": {}}

        schema_copy = copy.deepcopy(schema)
        schema_copy.setdefault("type", "object")
        schema_copy.setdefault("properties", {})
        return schema_copy

    def _build_mcp_tool_coroutine(self, server_name: str, tool_name: str) -> Any:
        async def _call_tool(**kwargs: Any) -> str:
            result = await self.execute_mcp_tool(server_name, tool_name, kwargs)
            success = bool(result.get("success"))
            if success:
                return str(
                    result.get("result", "MCP tool executed successfully")
                )
            return (
                f"[MCP TOOL ERROR] {server_name}.{tool_name}: "
                f"{result.get('error', 'unknown MCP tool failure')}"
            )

        return _call_tool

    def _attach_tools_to_agent(self, nav_agent: Any) -> None:
        mcp_tools: list[StructuredTool] = []
        for server_name, toolkit in self._mcp_toolkits.items():
            for mcp_tool in toolkit.tools:
                tool_name = getattr(mcp_tool, "name", str(mcp_tool))
                description = getattr(mcp_tool, "description", None) or tool_name
                args_schema = self._get_tool_input_schema(mcp_tool)
                    
                mcp_tools.append(
                    StructuredTool.from_function(
                        coroutine=self._build_mcp_tool_coroutine(server_name, tool_name),
                        name=f"mmcp_{server_name}_{tool_name}",
                        description=description,
                        args_schema=args_schema,
                        infer_schema=False,
                    )
                )
        nav_agent.tools = merge_tools(getattr(nav_agent, "tools", []), mcp_tools)

    async def execute_mcp_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a tool from a specific MCP server using the active session/toolkit."""
        try:
            if arguments is None:
                arguments = {}

            session = self._mcp_sessions.get(server_name)
            if not session:
                logger.error(f"MCP server '{server_name}' session not available")
                return {"success": False, "error": f"Server '{server_name}' session not available"}

            tool_args: Dict[str, Any] = arguments or {}
            if arguments and "arguments" in arguments and isinstance(arguments["arguments"], dict):
                tool_args = cast(Dict[str, Any], arguments["arguments"]) or {}

            call_result = await session.call_tool(tool_name, tool_args or {})

            text_parts: List[str] = []
            try:
                for item in getattr(call_result, "content", []) or []:
                    text = getattr(item, "text", None)
                    if text is None and hasattr(item, "to_dict"):
                        text = str(item.to_dict())
                    if text is None:
                        text = str(item)
                    text_parts.append(text)
            except Exception as parse_err:
                logger.warning(f"Could not parse MCP tool result content: {parse_err}")
                text_parts.append(str(call_result))

            result_text = "\n".join([p for p in text_parts if p])
            logger.info(f"MCP tool '{tool_name}' executed successfully on server '{server_name}'")
            return {
                "success": True,
                "result": result_text,
                "server": server_name,
                "tool": tool_name,
            }

        except Exception as e:
            logger.exception(
                f"Error executing MCP tool '{tool_name}' on server '{server_name}' via session. Details: {repr(e)}"
            )
            return {
                "success": False,
                "error": str(e),
                "server": server_name,
                "tool": tool_name,

            }

    async def list_mcp_tools(self, server_name: str) -> Dict[str, Any]:
        """List all available tools from a specific MCP server toolkit."""
        try:
            toolkit = self._mcp_toolkits.get(server_name)
            if not toolkit:
                return {"success": False, "error": f"Server '{server_name}' toolkit not available"}

            tool_list = [
                {
                    "name": getattr(tool, "name", str(tool)),
                    "description": getattr(tool, "description", ""),
                    "signature": getattr(tool, "inputSchema", getattr(tool, "funtion_schema", {})),
                }
                for tool in toolkit.tools
            ]

            return {
                "success": True,
                "server": server_name,
                "tools": tool_list,
                "tool_count": len(tool_list),
            }

        except Exception as e:
            logger.error(f"Error listing tools from MCP server '{server_name}': {e}")
            return {"success": False, "error": str(e), "server": server_name}

    async def get_mcp_resource(self, server_name: str, resource_uri: str) -> Dict[str, Any]:
        """Get a resource from a specific MCP server."""
        try:
            session = self._mcp_sessions.get(server_name)
            if not session:
                return {"success": False, "error": f"Server '{server_name}' session not available"}

            resource = await session.read_resource(cast(Any, resource_uri))

            content_text = ""
            try:
                contents = getattr(resource, "contents", None)
                if contents:
                    first = contents[0]
                    text = getattr(first, "text", None)
                    if text is not None:
                        content_text = cast(str, text)
                    else:
                        data = getattr(first, "data", None)
                        content_text = str(data) if data is not None else ""
            except Exception as e:
                logger.warning("Failed to parse resource contents for %s: %s", resource_uri, e)

            return {
                "success": True,
                "server": server_name,
                "resource_uri": resource_uri,
                "content": content_text,
            }

        except Exception as e:
            logger.error(f"Error getting resource '{resource_uri}' from MCP server '{server_name}': {e}")
            return {"success": False, "error": str(e), "server": server_name}

    async def connect_mcp_server(self, server_name: str, server_config: Dict[str, Any]) -> bool:
        """Connect to an MCP server and register tools for LangGraph agents."""
        try:
            transport = server_config.get("transport", "stdio")
            timeout_seconds = get_global_conf().get_mcp_timeout()

            async def create_toolkit_and_store(session: ClientSession) -> bool:
                await session.initialize()
                toolkit = await session.list_tools()
                self._mcp_sessions[server_name] = session
                self._mcp_toolkits[server_name] = toolkit
                if self._nav_agent is not None:
                    self._attach_tools_to_agent(self._nav_agent)
                logger.info(
                    "Connected to MCP server '%s' via %s transport with %s tools",
                    server_name,
                    transport,
                    len(toolkit.tools),
                )
                logger.info(f"Available tools: %s", [getattr(t, "name", t) for t in toolkit.tools])
                return True

            client_cm: Any = None
            if transport == "stdio":
                command = server_config.get("command", "python")
                args = server_config.get("args", [])
                server_params = StdioServerParameters(command=command, args=args)

                client_cm = stdio_client(server_params)
                read, write = await getattr(client_cm, "__aenter__")()
                session = ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout_seconds))
                await session.__aenter__()
                self._mcp_client_contexts[server_name] = client_cm
                return await create_toolkit_and_store(session)

            elif transport == "sse":
                url = server_config.get("url")
                if not url:
                    raise ValueError(
                        f"SSE transport requires 'url' field in server config for '{server_name}'"
                    )
                headers = server_config.get("headers", None)
                client_cm = sse_client(url=url, headers=headers)
                read_stream, write_stream = await getattr(client_cm, "__aenter__")()
                session = ClientSession(
                    read_stream, write_stream, read_timeout_seconds=timedelta(seconds=timeout_seconds)
                )
                await session.__aenter__()
                self._mcp_client_contexts[server_name] = client_cm
                return await create_toolkit_and_store(session)

            elif transport == "streamable-http":
                url = server_config.get("url")
                if not url:
                    raise ValueError(
                        f"Streamable HTTP transport requires 'url' field in server config for '{server_name}'"
                    )
                headers = server_config.get("headers", None)
                http_client = httpx.AsyncClient(headers=headers) if headers else None
                if http_client is not None:
                    self._mcp_http_clients[server_name] = http_client
                client_cm = streamable_http_client(url, http_client=http_client)
                read_stream, write_stream, _ = await getattr(client_cm, "__aenter__")()
                session = ClientSession(
                    read_stream, write_stream, read_timeout_seconds=timedelta(seconds=timeout_seconds)
                )
                await session.__aenter__()
                self._mcp_client_contexts[server_name] = client_cm
                return await create_toolkit_and_store(session)

            else:
                raise ValueError(
                    f"Unsupported transport type '{transport}' for server '{server_name}'. Supported: stdio, sse, streamable-http"
                )

        except (Exception, asyncio.CancelledError) as e:
            logger.error(f"Failed to connect to MCP server '{server_name}' via {transport}: {e}")
            await self.disconnect_mcp_server(server_name)
            return False

    async def disconnect_mcp_server(self, server_name: str) -> bool:
        """Disconnect from an MCP server and clean up resources."""
        try:
            toolkit = self._mcp_toolkits.pop(server_name, None)
            session = self._mcp_sessions.pop(server_name, None)
            client_cm = self._mcp_client_contexts.pop(server_name, None)
            http_client = self._mcp_http_clients.pop(server_name, None)

            if session:
                try:
                    await session.__aexit__(None, None, None)
                except Exception:
                    pass
            if client_cm:
                try:
                    await client_cm.__aexit__(None, None, None)
                except Exception:
                    pass
            if http_client:
                try:
                    await http_client.aclose()
                except Exception:
                    pass

            if toolkit or session:
                logger.info(f"Disconnected from MCP server '{server_name}'")
                return True
            return False

        except Exception as e:
            logger.error(f"Error disconnecting from MCP server '{server_name}': {e}")
            return False

    def get_connected_servers(self) -> List[str]:
        """Get list of currently connected MCP servers."""
        return list(self._mcp_toolkits.keys())

    async def check_mcp_server_status(self, server_name: str) -> Dict[str, Any]:
        """Check if an MCP server is connected and responsive."""
        try:
            toolkit = self._mcp_toolkits.get(server_name)
            session = self._mcp_sessions.get(server_name)

            if not toolkit or not session:
                return {
                    "success": False,
                    "server": server_name,
                    "status": "disconnected",
                    "error": "Server not found in connected toolkits",
                }

            return {
                "success": True,
                "server": server_name,
                "status": "connected",
                "tool_count": len(toolkit.tools),
                "tools": [getattr(tool, "name", str(tool)) for tool in toolkit.tools],
            }

        except Exception as e:
            logger.error(f"Error checking MCP server status '{server_name}': {e}")
            return {
                "success": False,
                "server": server_name,
                "status": "error",
                "error": str(e),
            }

    def get_mcp_toolkit(self, server_name: str) -> Any:
        """Get MCP toolkit for a specific server."""
        return self._mcp_toolkits.get(server_name)

    async def get_configured_mcp_servers(self) -> Dict[str, Any]:
        """Get list of all configured MCP servers from configuration."""
        try:
            config = get_global_conf()
            if not config.is_mcp_enabled():
                return {"success": False, "error": "MCP is disabled in configuration"}

            mcp_servers = config.get_mcp_servers()
            servers_config = mcp_servers.get("mcpServers", {}) if mcp_servers else {}

            if not servers_config:
                return {"success": False, "error": "No MCP servers configured"}

            server_status: Dict[str, Any] = {}
            connected_servers = self.get_connected_servers()

            for name, srv_conf in servers_config.items():
                transport = srv_conf.get("transport", "stdio")
                server_status[name] = {
                    "configured": True,
                    "connected": name in connected_servers,
                    "transport": transport,
                    "command": srv_conf.get("command", "") if transport == "stdio" else None,
                    "args": srv_conf.get("args", []) if transport == "stdio" else None,
                    "url": srv_conf.get("url") if transport in ["sse", "streamable-http"] else None,
                }

            return {
                "success": True,
                "servers": server_status,
                "connected_count": len(connected_servers),
                "configured_count": len(servers_config),
            }

        except Exception as e:
            logger.error(f"Error getting configured MCP servers: {e}")
            return {"success": False, "error": str(e)}

    async def initialize_mcp_connections(self) -> Dict[str, Any]:
        """Initialize connections to all configured MCP servers."""
        try:
            config = get_global_conf()
            if not config.is_mcp_enabled():
                return {"success": False, "error": "MCP is disabled in configuration"}

            mcp_servers = config.get_mcp_servers()
            servers_config = mcp_servers.get("mcpServers", {}) if mcp_servers else {}

            if not servers_config:
                return {"success": False, "error": "No MCP servers configured"}

            results: Dict[str, Any] = {}
            for name, srv_conf in servers_config.items():
                try:
                    transport = srv_conf.get("transport", "stdio")
                    logger.info(f"Connecting to MCP server '{name}' via {transport} transport")
                    success = await self.connect_mcp_server(name, srv_conf)
                    results[name] = {"success": success, "error": None if success else "Connection failed"}
                except (Exception, asyncio.CancelledError) as e:
                    logger.error(f"Error connecting to MCP server {name}: {e}")
                    results[name] = {"success": False, "error": str(e)}

            successful_connections = [nm for nm, res in results.items() if res["success"]]

            return {
                "success": True,
                "results": results,
                "connected_servers": successful_connections,
                "connection_count": len(successful_connections),
            }

        except Exception as e:
            logger.error(f"Error initializing MCP connections: {e}")
            return {"success": False, "error": str(e)}

    async def _disconnect_all_servers(self) -> None:
        """Disconnect from all servers and clear state."""
        for server_name in list(self._mcp_sessions.keys()):
            try:
                await self.disconnect_mcp_server(server_name)
            except Exception as e:
                logger.warning(f"Error disconnecting server '{server_name}' during destroy: {e}")
        self._mcp_toolkits.clear()
        self._mcp_sessions.clear()
        self._mcp_client_contexts.clear()
        self._mcp_http_clients.clear()


# Compatibility function to set agents using the singleton instance
async def set_mcp_agents(llm_agent: Any, executor_agent: Any) -> bool:
    return await MCPHelper.instance().set_mcp_agents(llm_agent, executor_agent)
