"""MCP (Model Context Protocol) tools for server communication and tool execution.

This module provides a singleton class `MCPHelper` that encapsulates all MCP
connection, toolkit, and tool-execution logic. Thin tool wrappers are exposed
via the `@tool` decorator which delegate to the singleton instance.
"""

from datetime import timedelta
from typing import Any, Dict, List, Optional, cast
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from autogen.mcp import create_toolkit
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.logger import logger

class MCPHelper:
    """Singleton manager for MCP connections, toolkits, and tool execution."""

    _instance: Optional["MCPHelper"] = None

    def __init__(self) -> None:
        self._mcp_toolkits: Dict[str, Any] = {}
        self._mcp_sessions: Dict[str, ClientSession] = {}
        self._mcp_client_contexts: Dict[str, Any] = {}

        # References to Hercules agents to enable native MCP tool registration
        self._mcp_llm_agent: Optional[Any] = None
        self._mcp_executor_agent: Optional[Any] = None

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
        try:
            await cls._instance._disconnect_all_servers()
        finally:
            cls._instance = None
        return True

    async def set_mcp_agents(self, llm_agent: Any, executor_agent: Any) -> bool:
        """Set the Hercules agents used to register MCP tools for LLM and execution."""
        self._mcp_llm_agent = llm_agent
        self._mcp_executor_agent = executor_agent
        connection_result = await self.initialize_mcp_connections()
        return connection_result["success"]

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
            # Fallback via toolkit wrapper if available
            try:
                toolkit = self._mcp_toolkits.get(server_name)
                if not toolkit:
                    raise RuntimeError("Toolkit not available for fallback execution")
                target_tool = None
                for tool_func in toolkit.tools:
                    if tool_func.name == tool_name:
                        target_tool = tool_func
                        break
                if not target_tool:
                    raise RuntimeError(f"Tool '{tool_name}' not found in toolkit for fallback execution")
                tool_args = arguments or {}
                if arguments and "arguments" in arguments and isinstance(arguments["arguments"], dict):
                    tool_args = cast(Dict[str, Any], arguments["arguments"]) or {}
                result = await target_tool(**tool_args)
                return {
                    "success": True,
                    "result": str(result),
                    "server": server_name,
                    "tool": tool_name,
                    "fallback": True,
                }
            except Exception as e2:
                logger.exception(
                    f"Fallback execution failed for tool '{tool_name}' on server '{server_name}'. Details: {repr(e2)}"
                )
                return {
                    "success": False,
                    "error": str(e2) or str(e) or "Unknown error",
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
                    "name": tool.name,
                    "description": tool.description,
                    "signature": tool.function_schema,
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
        """Connect to an MCP server and create toolkit using AutoGen MCP integration."""
        try:
            transport = server_config.get("transport", "stdio")
            timeout_seconds = get_global_conf().get_mcp_timeout()

            async def create_toolkit_and_store(session: ClientSession) -> bool:
                await session.initialize()
                toolkit = await create_toolkit(
                    session,
                    use_mcp_resources=False,
                    use_mcp_tools=True,
                )
                self._mcp_sessions[server_name] = session
                self._mcp_toolkits[server_name] = toolkit
                try:
                    if self._mcp_llm_agent is not None:
                        toolkit.register_for_llm(self._mcp_llm_agent)
                    else:
                        logger.warning(
                            f"MCP LLM agent not set; skipping LLM registration for server '{server_name}'"
                        )
                    if self._mcp_executor_agent is not None:
                        toolkit.register_for_execution(self._mcp_executor_agent)
                    else:
                        logger.warning(
                            f"MCP executor agent not set; skipping execution registration for server '{server_name}'"
                        )
                except Exception as reg_err:
                    logger.error(
                        f"Error registering MCP toolkit with agents for server '{server_name}': {reg_err}"
                    )
                logger.info(
                    f"Connected to MCP server '{server_name}' via {transport} with {len(toolkit.tools)} tools available"
                )
                logger.info(f"Available tools: {[tool.name for tool in toolkit.tools]}")
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

                client_cm = sse_client(url=url)
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

                client_cm = streamablehttp_client(url)
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

        except Exception as e:
            logger.error(f"Failed to connect to MCP server '{server_name}' via {transport}: {e}")
            return False

    async def disconnect_mcp_server(self, server_name: str) -> bool:
        """Disconnect from an MCP server and clean up resources."""
        try:
            toolkit = self._mcp_toolkits.pop(server_name, None)
            session = self._mcp_sessions.pop(server_name, None)
            client_cm = self._mcp_client_contexts.pop(server_name, None)

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
                "tools": [tool.name for tool in toolkit.tools],
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
                except Exception as e:
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


# Compatibility function to set agents using the singleton instance
async def set_mcp_agents(llm_agent: Any, executor_agent: Any) -> bool:
    return await MCPHelper.instance().set_mcp_agents(llm_agent, executor_agent)