from typing import Any, Dict, Optional
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.mcp_helper import MCPHelper

# Tool wrappers delegating to the singleton instance
@tool(agent_names=["mcp_nav_agent"], description="Execute a tool from an MCP server", name="execute_mcp_tool")
async def execute_mcp_tool(
    server_name: str,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a named MCP tool on a configured server via the singleton manager."""
    return await MCPHelper.instance().execute_mcp_tool(server_name, tool_name, arguments)


@tool(agent_names=["mcp_nav_agent"], description="List available tools from an MCP server", name="list_mcp_tools")
async def list_mcp_tools(server_name: str) -> Dict[str, Any]:
    """List tools exposed by the specified MCP server."""
    return await MCPHelper.instance().list_mcp_tools(server_name)


@tool(agent_names=["mcp_nav_agent"], description="Get a resource from an MCP server", name="get_mcp_resource")
async def get_mcp_resource(server_name: str, resource_uri: str) -> Dict[str, Any]:
    """Fetch a resource by URI from the specified MCP server."""
    return await MCPHelper.instance().get_mcp_resource(server_name, resource_uri)


@tool(agent_names=["mcp_nav_agent"], description="Check MCP server connection status", name="check_mcp_server_status")
async def check_mcp_server_status(server_name: str) -> Dict[str, Any]:
    """Return connection status for the specified MCP server."""
    return await MCPHelper.instance().check_mcp_server_status(server_name)


@tool(agent_names=["mcp_nav_agent"], description="Get list of configured MCP servers", name="get_configured_mcp_servers")
async def get_configured_mcp_servers() -> Dict[str, Any]:
    """Return configured MCP servers and their connection status."""
    return await MCPHelper.instance().get_configured_mcp_servers()