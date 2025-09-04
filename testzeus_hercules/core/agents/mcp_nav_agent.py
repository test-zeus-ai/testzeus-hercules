"""MCP Navigation Agent for Model Context Protocol server interactions."""

import atexit
import asyncio
from datetime import timedelta
from typing import Any, Dict

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from autogen.mcp import create_toolkit

from testzeus_hercules.core.agents.base_nav_agent import BaseNavAgent
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools import mcp_tools as _mt
from testzeus_hercules.core.tools.mcp_tools import get_mcp_toolkit
from testzeus_hercules.utils.logger import logger

class McpNavAgent(BaseNavAgent):
    """MCP Navigation Agent for executing MCP server tools and managing resources."""
    
    agent_name: str = "mcp_nav_agent"
    prompt = """# MCP Navigation Agent

You are an MCP (Model Context Protocol) Navigation Agent responsible for executing tools and accessing resources from MCP servers. Your primary focus is on communicating with MCP servers and executing their available tools.

## Core Responsibilities

- **Execute MCP Tools**: Call tools available on connected MCP servers
- **Manage Resources**: Access and retrieve resources from MCP servers  
- **Server Communication**: Maintain connections and handle server interactions
- **Result Processing**: Process tool execution results and provide clear summaries

## Available Functions

1. **get_configured_mcp_servers()**: Get list of configured MCP servers and their status
2. **check_mcp_server_status(server_name)**: Check if a specific server is connected
3. **execute_mcp_tool(server_name, tool_name, arguments)**: Execute a specific tool on an MCP server
4. **list_mcp_tools(server_name)**: List all available tools from a server
5. **get_mcp_resource(server_name, resource_uri)**: Retrieve a resource from a server

## Core Rules

1. **Connection First**: Connections are initialized automatically on agent startup; still validate status before critical calls
2. **Use Correct Server Names**: Use server names from configuration
3. **Sequential Execution**: Execute one function at a time, wait for response
4. **Proper Parameters**: Always include all required parameters in function calls
5. **Error Handling**: Handle connection errors and tool failures gracefully
6. **Result Verification**: Verify each result before proceeding to next operation

## Response Formats

**Success:**
```
previous_step: [previous step summary]
current_output: [DETAILED tool execution result with server response]
##FLAG::SAVE_IN_MEM##
##TERMINATE TASK##
```

**Error:**
```
previous_step: [previous step summary]
current_output: [Error description and attempted resolution]
##TERMINATE TASK##
```

## Error Handling

- Check server connectivity before tool execution
- Provide clear error messages for failed operations
- Suggest alternative approaches when tools fail
- Document all server interactions for debugging

Available Test Data: $basic_test_information
"""

    def __init__(self, model_config_list, llm_config_params, system_prompt, nav_executor, agent_name=None, agent_prompt=None):
        """Initialize MCP Navigation Agent and connect to configured MCP servers."""
        super().__init__(model_config_list, llm_config_params, system_prompt, nav_executor, agent_name, agent_prompt)
        

    def register_tools(self) -> None:
        """Register tools and connect to MCP servers on startup."""
        # Register built-in tools for this agent
        self.load_tools()
        # Initialize MCP connections and register MCP toolkits with this agent/executor
        self._initialize_mcp_on_startup()

    def register_shutdown(self) -> None:
        """Register MCP-specific shutdown to close all MCP connections."""
        # Attach a shutdown hook that disconnects all MCP servers
        async def _mcp_shutdown() -> None:
            try:
                await disconnect_all_mcp_servers()
            except Exception:
                pass

        try:
            setattr(self.agent, "_hercules_shutdown", _mcp_shutdown)
        except Exception:
            # Fallback to base behavior if unable to attach
            super().register_shutdown()

    def _initialize_mcp_on_startup(self) -> None:
        """Connect to configured MCP servers and register their tools with agents."""
        try:
            config = get_global_conf()
            if not config.is_mcp_enabled():
                logger.info("MCP is disabled via configuration; skipping MCP init")
                return

            mcp_servers = config.get_mcp_servers() or {}
            servers_config: Dict[str, Dict[str, Any]] = mcp_servers.get("mcpServers", {})
            if not servers_config:
                logger.warning("No MCP servers configured; skipping MCP init")
                return

            async def _connect_all():
                for server_name, server_cfg in servers_config.items():
                    transport = server_cfg.get("transport", "stdio")
                    logger.info(f"Connecting MCP server '{server_name}' via {transport}")
                    ok = await connect_mcp_server(server_name, server_cfg)
                    if not ok:
                        logger.error(f"MCP connection failed for '{server_name}'")
                        continue
                    # Register toolkit tools with this agent and its executor
                    toolkit = get_mcp_toolkit(server_name)
                    if not toolkit:
                        logger.error(f"No toolkit found after connection for '{server_name}'")
                        continue
                    try:
                        toolkit.register_for_llm(self.agent)
                        toolkit.register_for_execution(self.nav_executor)
                        logger.info(
                            f"Registered {len(toolkit.tools)} MCP tools from '{server_name}' with agents"
                        )
                    except Exception as reg_err:
                        logger.error(
                            f"Failed registering MCP toolkit tools for '{server_name}': {reg_err}"
                        )

            # Run the async connection routine synchronously (nest_asyncio is applied)
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            loop.run_until_complete(_connect_all())
        except Exception as e:
            logger.error(f"Error during MCP initialization on agent startup: {e}")


_shutdown_hook_registered: bool = False


def _shutdown_mcp_clients() -> None:
    """Atexit hook that closes MCP clients if a loop is running.

    Does not create a new event loop; relies on explicit scheduling during runtime
    or on an active loop at interpreter shutdown.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop at interpreter shutdown; skip cleanup
        return
    try:
        loop.create_task(disconnect_all_mcp_servers())
    except Exception:
        pass


def _ensure_shutdown_hook() -> None:
    global _shutdown_hook_registered
    if not _shutdown_hook_registered:
        try:
            atexit.register(_shutdown_mcp_clients)
            _shutdown_hook_registered = True
        except Exception:
            pass


async def connect_mcp_server(server_name: str, server_config: Dict[str, Any]) -> bool:
    """Connect to an MCP server and create toolkit using AutoGen MCP integration.

    Stores session/context in shared mcp_tools module for downstream tool use.
    """
    try:
        transport = server_config.get("transport", "stdio")
        timeout_seconds = get_global_conf().get_mcp_timeout()

        async def create_toolkit_and_store(session: ClientSession):
            await session.initialize()
            toolkit = await create_toolkit(
                session,
                use_mcp_resources=False,
                use_mcp_tools=True,
            )
            _mt._mcp_sessions[server_name] = session
            _mt._mcp_toolkits[server_name] = toolkit
            logger.info(
                f"Connected to MCP server '{server_name}' via {transport} with {len(toolkit.tools)} tools available"
            )
            logger.info(f"Available tools: {[tool.name for tool in toolkit.tools]}")
            return True

        if transport == "stdio":
            command = server_config.get("command", "python")
            args = server_config.get("args", [])
            server_params = StdioServerParameters(command=command, args=args)

            client_cm = stdio_client(server_params)
            read, write = await client_cm.__aenter__()
            session = ClientSession(
                read,
                write,
                read_timeout_seconds=timedelta(seconds=timeout_seconds),
            )
            await session.__aenter__()
            _mt._mcp_client_contexts[server_name] = client_cm
            _ensure_shutdown_hook()
            return await create_toolkit_and_store(session)

        elif transport == "sse":
            url = server_config.get("url")
            if not url:
                raise ValueError(
                    f"SSE transport requires 'url' field in server config for '{server_name}'"
                )
            client_cm = sse_client(url=url)
            read_stream, write_stream = await client_cm.__aenter__()
            session = ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=timeout_seconds),
            )
            await session.__aenter__()
            _mt._mcp_client_contexts[server_name] = client_cm
            _ensure_shutdown_hook()
            return await create_toolkit_and_store(session)

        elif transport == "streamable-http":
            url = server_config.get("url")
            if not url:
                raise ValueError(
                    f"Streamable HTTP transport requires 'url' field in server config for '{server_name}'"
                )
            client_cm = streamablehttp_client(url)
            read_stream, write_stream, _ = await client_cm.__aenter__()
            session = ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=timeout_seconds),
            )
            await session.__aenter__()
            _mt._mcp_client_contexts[server_name] = client_cm
            _ensure_shutdown_hook()
            return await create_toolkit_and_store(session)

        else:
            raise ValueError(
                f"Unsupported transport type '{transport}' for server '{server_name}'. Supported: stdio, sse, streamable-http"
            )

    except Exception as e:
        logger.error(
            f"Failed to connect to MCP server '{server_name}' via {server_config.get('transport','stdio')}: {e}"
        )
        return False


async def disconnect_mcp_server(server_name: str) -> bool:
    """Disconnect from an MCP server and clean up resources."""
    try:
        toolkit = _mt._mcp_toolkits.pop(server_name, None)
        session = _mt._mcp_sessions.pop(server_name, None)
        client_cm = _mt._mcp_client_contexts.pop(server_name, None)

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


async def disconnect_all_mcp_servers() -> None:
    """Disconnect all connected MCP servers while the loop is running."""
    # Iterate over a static list to avoid size change during iteration
    for server_name in list(_mt._mcp_client_contexts.keys()):
        try:
            await disconnect_mcp_server(server_name)
        except Exception:
            # Best effort; continue with others
            pass

