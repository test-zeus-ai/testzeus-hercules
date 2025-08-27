"""MCP Navigation Agent for Model Context Protocol server interactions."""

import asyncio
from testzeus_hercules.core.agents.base_nav_agent import BaseNavAgent
from testzeus_hercules.core.tools.mcp_tools import connect_mcp_server, get_connected_servers, get_mcp_toolkit
from testzeus_hercules.config import get_global_conf
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
2. **initialize_mcp_connections()**: Initialize connections to configured MCP servers
3. **check_mcp_server_status(server_name)**: Check if a specific server is connected
4. **execute_mcp_tool(server_name, tool_name, arguments)**: Execute a specific tool on an MCP server
5. **list_mcp_tools(server_name)**: List all available tools from a server
6. **get_mcp_resource(server_name, resource_uri)**: Retrieve a resource from a server

## Core Rules

1. **Connection First**: Always check configured servers and initialize connections before using MCP tools
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
        # Note: MCP connections will be initialized when first needed

    def register_tools(self) -> None:
        """Register MCP-specific tools and MCP server toolkits for the agent."""
        self.load_tools()
        
        # Register MCP toolkits from connected servers
        self._register_mcp_toolkits()
        
        logger.info(f"Registered MCP tools for {self.agent_name}")

    def _register_mcp_toolkits(self) -> None:
        """Register MCP toolkits from connected servers."""
        connected_servers = get_connected_servers()
        
        for server_name in connected_servers:
            toolkit = get_mcp_toolkit(server_name)
            if toolkit:
                try:
                    # Register toolkit tools with the agent
                    toolkit.register_for_llm(self.agent)
                    logger.info(f"Registered MCP toolkit for server '{server_name}' with {len(toolkit.tools)} tools")
                except Exception as e:
                    logger.error(f"Failed to register toolkit for server '{server_name}': {e}")

    def get_configured_servers(self) -> dict:
        """Get list of configured MCP servers from config."""
        config = get_global_conf()
        
        if not config.is_mcp_enabled():
            return {}
            
        mcp_servers = config.get_mcp_servers()
        return mcp_servers.get("mcpServers", {}) if mcp_servers else {}

    async def ensure_connections(self) -> None:
        """Ensure connections to all configured MCP servers."""
        servers_config = self.get_configured_servers()
        
        if not servers_config:
            logger.warning("No MCP servers configured")
            return
        
        # Connect to any servers that aren't already connected
        for server_name, server_config in servers_config.items():
            if server_name not in get_connected_servers():
                try:
                    transport = server_config.get("transport", "stdio")
                    logger.info(f"Connecting to MCP server '{server_name}' via {transport} transport")
                    
                    success = await connect_mcp_server(server_name, server_config)
                    if success:
                        logger.info(f"Successfully connected to MCP server: {server_name}")
                        # Re-register toolkits after new connection
                        self._register_mcp_toolkits()
                    else:
                        logger.error(f"Failed to connect to MCP server: {server_name}")
                        
                except Exception as e:
                    logger.error(f"Error connecting to MCP server {server_name}: {e}")

    def get_server_status(self) -> dict:
        """Get status of all connected MCP servers."""
        connected_servers = get_connected_servers()
        config = get_global_conf()
        configured_servers = config.get_mcp_servers().get("mcpServers", {})
        
        return {
            "connected": connected_servers,
            "configured": list(configured_servers.keys()),
            "enabled": config.is_mcp_enabled()
        }

