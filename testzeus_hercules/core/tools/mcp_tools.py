"""MCP (Model Context Protocol) tools for server communication and tool execution."""

import asyncio
from datetime import timedelta
from typing import Any, Dict, List

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from autogen.mcp import create_toolkit

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger

# Global MCP toolkit storage
_mcp_toolkits: Dict[str, Any] = {}
_mcp_sessions: Dict[str, ClientSession] = {}


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
            
        toolkit = _mcp_toolkits.get(server_name)
        if not toolkit:
            logger.error(f"MCP server '{server_name}' toolkit not available")
            return {"success": False, "error": f"Server '{server_name}' toolkit not available"}
        
        # Find the tool in the toolkit
        target_tool = None
        for tool_func in toolkit.tools:
            if tool_func.name == tool_name:
                target_tool = tool_func
                break
        
        if not target_tool:
            logger.error(f"Tool '{tool_name}' not found in server '{server_name}'")
            return {"success": False, "error": f"Tool '{tool_name}' not found"}
        
        # Execute the tool function
        # Handle nested arguments structure if present
        tool_args = arguments
        if arguments and "arguments" in arguments and isinstance(arguments["arguments"], dict):
            tool_args = arguments["arguments"]
        
        if tool_args:
            result = await target_tool(**tool_args)
        else:
            result = await target_tool()
        
        logger.info(f"MCP tool '{tool_name}' executed successfully on server '{server_name}'")
        return {
            "success": True,
            "result": str(result),
            "server": server_name,
            "tool": tool_name
        }
        
    except Exception as e:
        logger.error(f"Error executing MCP tool '{tool_name}' on server '{server_name}': {e}")
        return {
            "success": False,
            "error": str(e),
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


async def connect_mcp_server(server_name: str, server_config: Dict[str, Any]) -> bool:
    """
    Connect to an MCP server and create toolkit using AutoGen MCP integration.
    
    Args:
        server_name: Name of the server for identification
        server_config: Server configuration containing transport type and connection details
    
    Returns:
        True if connection successful, False otherwise
    """
    try:
        transport = server_config.get("transport", "stdio")
        timeout_seconds = get_global_conf().get_mcp_timeout()
        
        async def create_toolkit_and_store(session: ClientSession):
            """Helper function to create and store toolkit."""
            await session.initialize()
            toolkit = await create_toolkit(session)
            _mcp_sessions[server_name] = session
            _mcp_toolkits[server_name] = toolkit
            logger.info(f"Connected to MCP server '{server_name}' via {transport} with {len(toolkit.tools)} tools available")
            logger.info(f"Available tools: {[tool.name for tool in toolkit.tools]}")
            return True
        
        if transport == "stdio":
            # STDIO transport
            command = server_config.get("command", "python")
            args = server_config.get("args", [])
            server_params = StdioServerParameters(command=command, args=args)
            
            async with stdio_client(server_params) as (read, write), ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout_seconds)) as session:
                return await create_toolkit_and_store(session)
                
        elif transport == "sse":
            # Server-Sent Events transport
            url = server_config.get("url")
            if not url:
                raise ValueError(f"SSE transport requires 'url' field in server config for '{server_name}'")
                
            async with sse_client(url=url) as streams, ClientSession(*streams, read_timeout_seconds=timedelta(seconds=timeout_seconds)) as session:
                return await create_toolkit_and_store(session)
                
        elif transport == "streamable-http":
            # Streamable HTTP transport
            url = server_config.get("url")
            if not url:
                raise ValueError(f"Streamable HTTP transport requires 'url' field in server config for '{server_name}'")
                
            async with (
                streamablehttp_client(url) as (read_stream, write_stream, _),
                ClientSession(read_stream, write_stream, read_timeout_seconds=timedelta(seconds=timeout_seconds)) as session,
            ):
                return await create_toolkit_and_store(session)
                
        else:
            raise ValueError(f"Unsupported transport type '{transport}' for server '{server_name}'. Supported: stdio, sse, streamable-http")
        
    except Exception as e:
        logger.error(f"Failed to connect to MCP server '{server_name}' via {transport}: {e}")
        return False


async def disconnect_mcp_server(server_name: str) -> bool:
    """
    Disconnect from an MCP server and clean up resources.
    
    Args:
        server_name: Name of the server to disconnect from
    
    Returns:
        True if disconnection successful, False otherwise
    """
    try:
        # Remove toolkit and session
        toolkit = _mcp_toolkits.pop(server_name, None)
        session = _mcp_sessions.pop(server_name, None)
        
        if session:
            try:
                # Clean up session
                await session.__aexit__(None, None, None)
            except:
                pass  # Ignore close errors
        
        if toolkit or session:
            logger.info(f"Disconnected from MCP server '{server_name}'")
            return True
        return False
        
    except Exception as e:
        logger.error(f"Error disconnecting from MCP server '{server_name}': {e}")
        return False


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
        from testzeus_hercules.config import get_global_conf
        
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


@tool(agent_names=["mcp_nav_agent"], description="Initialize connection to configured MCP servers", name="initialize_mcp_connections")
async def initialize_mcp_connections() -> Dict[str, Any]:
    """
    Initialize connections to all configured MCP servers.
    
    Returns:
        Dict containing connection results
    """
    try:
        from testzeus_hercules.config import get_global_conf
        
        config = get_global_conf()
        if not config.is_mcp_enabled():
            return {"success": False, "error": "MCP is disabled in configuration"}
        
        mcp_servers = config.get_mcp_servers()
        servers_config = mcp_servers.get("mcpServers", {}) if mcp_servers else {}
        
        if not servers_config:
            return {"success": False, "error": "No MCP servers configured"}
        
        # Connect to all configured servers
        results = {}
        for server_name, server_config in servers_config.items():
            try:
                transport = server_config.get("transport", "stdio")
                logger.info(f"Connecting to MCP server '{server_name}' via {transport} transport")
                
                success = await connect_mcp_server(server_name, server_config)
                results[server_name] = {
                    "success": success,
                    "error": None if success else "Connection failed"
                }
                
            except Exception as e:
                logger.error(f"Error connecting to MCP server {server_name}: {e}")
                results[server_name] = {
                    "success": False,
                    "error": str(e)
                }
        
        successful_connections = [name for name, result in results.items() if result["success"]]
        
        return {
            "success": True,
            "results": results,
            "connected_servers": successful_connections,
            "connection_count": len(successful_connections)
        }
        
    except Exception as e:
        logger.error(f"Error initializing MCP connections: {e}")
        return {"success": False, "error": str(e)}