"""MCP (Model Context Protocol) tools for server communication and tool execution."""

from typing import Any, Dict, List

from mcp import ClientSession
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger

# Global MCP toolkit storage
_mcp_toolkits: Dict[str, Any] = {}
_mcp_sessions: Dict[str, ClientSession] = {}
_mcp_client_contexts: Dict[str, Any] = {}


# Note: Toolkit registration with agents is handled by McpNavAgent on startup.
@tool(agent_names=["mcp_nav_agent"], description="Execute a tool from an MCP server", name="execute_mcp_tool")
async def execute_mcp_tool(server_name: str, tool_name: str, arguments: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Execute a tool from a specific MCP server using AutoGen MCP toolkit.
    
    Args:
        server_name: Name of the MCP server
        tool_name: Name of the tool to execute
        arguments: Dictionary of arguments to pass to the tool
    
    Returns:
        Dict containing execution result and status
    """
    try:
        if arguments is None:
            arguments = {}
            
        # Prefer direct session tool call to ensure correct invocation
        session = _mcp_sessions.get(server_name)
        if not session:
            logger.error(f"MCP server '{server_name}' session not available")
            return {"success": False, "error": f"Server '{server_name}' session not available"}

        # Handle nested arguments structure if present
        tool_args = arguments
        if arguments and "arguments" in arguments and isinstance(arguments["arguments"], dict):
            tool_args = arguments["arguments"]

        # Call the tool via MCP session
        call_result = await session.call_tool(tool_name, tool_args or {})

        # Extract textual content from the MCP response
        text_parts: List[str] = []
        try:
            for item in getattr(call_result, "content", []) or []:
                text = getattr(item, "text", None)
                if text is None and hasattr(item, "to_dict"):
                    # Fallback to dict repr if non-text content
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
            "tool": tool_name
        }
        
    except Exception as e:
        logger.exception(
            f"Error executing MCP tool '{tool_name}' on server '{server_name}' via session. Details: {repr(e)}"
        )
        # Fallback: try invoking via toolkit wrapper if available
        try:
            toolkit = _mcp_toolkits.get(server_name)
            if not toolkit:
                raise RuntimeError("Toolkit not available for fallback execution")
            # Find the tool
            target_tool = None
            for tool_func in toolkit.tools:
                if tool_func.name == tool_name:
                    target_tool = tool_func
                    break
            if not target_tool:
                raise RuntimeError(f"Tool '{tool_name}' not found in toolkit for fallback execution")
            tool_args = arguments
            if arguments and "arguments" in arguments and isinstance(arguments["arguments"], dict):
                tool_args = arguments["arguments"]
            result = await target_tool(**(tool_args or {}))
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
                "tool": tool_name
            }


@tool(agent_names=["mcp_nav_agent"], description="List available tools from an MCP server", name="list_mcp_tools")
async def list_mcp_tools(server_name: str) -> Dict[str, Any]:
    """
    List all available tools from a specific MCP server toolkit.
    
    Args:
        server_name: Name of the MCP server
    
    Returns:
        Dict containing list of available tools
    """
    try:
        toolkit = _mcp_toolkits.get(server_name)
        if not toolkit:
            return {"success": False, "error": f"Server '{server_name}' toolkit not available"}
        
        tool_list = [
            {
                "name": tool.name,
                "description": tool.description,
                "signature": tool.function_schema
            }
            for tool in toolkit.tools
        ]
        
        return {
            "success": True,
            "server": server_name,
            "tools": tool_list,
            "tool_count": len(tool_list)
        }
        
    except Exception as e:
        logger.error(f"Error listing tools from MCP server '{server_name}': {e}")
        return {"success": False, "error": str(e), "server": server_name}


@tool(agent_names=["mcp_nav_agent"], description="Get a resource from an MCP server", name="get_mcp_resource")
async def get_mcp_resource(server_name: str, resource_uri: str) -> Dict[str, Any]:
    """
    Get a resource from a specific MCP server.
    
    Args:
        server_name: Name of the MCP server
        resource_uri: URI of the resource to retrieve
    
    Returns:
        Dict containing resource content and metadata
    """
    try:
        session = _mcp_sessions.get(server_name)
        if not session:
            return {"success": False, "error": f"Server '{server_name}' session not available"}
        
        resource = await session.read_resource(resource_uri)
        
        return {
            "success": True,
            "server": server_name,
            "resource_uri": resource_uri,
            "content": resource.contents[0].text if resource.contents else ""
        }
        
    except Exception as e:
        logger.error(f"Error getting resource '{resource_uri}' from MCP server '{server_name}': {e}")
        return {"success": False, "error": str(e), "server": server_name}


def get_connected_servers() -> List[str]:
    """Get list of currently connected MCP servers."""
    return list(_mcp_toolkits.keys())


@tool(agent_names=["mcp_nav_agent"], description="Check MCP server connection status", name="check_mcp_server_status")
async def check_mcp_server_status(server_name: str) -> Dict[str, Any]:
    """
    Check if an MCP server is connected and responsive.
    
    Args:
        server_name: Name of the MCP server to check
    
    Returns:
        Dict containing server status information
    """
    try:
        toolkit = _mcp_toolkits.get(server_name)
        session = _mcp_sessions.get(server_name)
        
        if not toolkit or not session:
            return {
                "success": False,
                "server": server_name,
                "status": "disconnected",
                "error": "Server not found in connected toolkits"
            }
        
        return {
            "success": True,
            "server": server_name,
            "status": "connected",
            "tool_count": len(toolkit.tools),
            "tools": [tool.name for tool in toolkit.tools]
        }
        
    except Exception as e:
        logger.error(f"Error checking MCP server status '{server_name}': {e}")
        return {
            "success": False,
            "server": server_name,
            "status": "error",
            "error": str(e)
        }


def get_mcp_toolkit(server_name: str) -> Any:
    """Get MCP toolkit for a specific server."""
    return _mcp_toolkits.get(server_name)


@tool(agent_names=["mcp_nav_agent"], description="Get list of configured MCP servers", name="get_configured_mcp_servers")
async def get_configured_mcp_servers() -> Dict[str, Any]:
    """
    Get list of all configured MCP servers from configuration.
    
    Returns:
        Dict containing configured servers and their status
    """
    try:
        config = get_global_conf()
        if not config.is_mcp_enabled():
            return {"success": False, "error": "MCP is disabled in configuration"}
        
        mcp_servers = config.get_mcp_servers()
        servers_config = mcp_servers.get("mcpServers", {}) if mcp_servers else {}
        
        if not servers_config:
            return {"success": False, "error": "No MCP servers configured"}
        
        # Get status of each configured server
        server_status = {}
        connected_servers = get_connected_servers()
        
        for server_name, server_config in servers_config.items():
            transport = server_config.get("transport", "stdio")
            server_status[server_name] = {
                "configured": True,
                "connected": server_name in connected_servers,
                "transport": transport,
                "command": server_config.get("command", "") if transport == "stdio" else None,
                "args": server_config.get("args", []) if transport == "stdio" else None,
                "url": server_config.get("url") if transport in ["sse", "streamable-http"] else None
            }
        
        return {
            "success": True,
            "servers": server_status,
            "connected_count": len(connected_servers),
            "configured_count": len(servers_config)
        }
        
    except Exception as e:
        logger.error(f"Error getting configured MCP servers: {e}")
        return {"success": False, "error": str(e)}


# Note: Initialization of connections moved to McpNavAgent startup.
