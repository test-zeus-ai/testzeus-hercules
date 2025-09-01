"""
Composio integration tools for TestZeus Hercules.
Provides Gmail email fetching capabilities through Composio's API.
"""

import json
import time
from typing import Annotated, Any, Dict, Optional

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.tool_registry import api_logger as file_logger
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger

try:
    from composio import Composio
    COMPOSIO_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Composio not available. Error: {e}. Install with: pip install composio")
    COMPOSIO_AVAILABLE = False


def log_composio_operation(operation: str, data: Dict[str, Any]) -> None:
    """
    Log Composio operation details.
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    log_data = {
        "composio_operation": {
            "timestamp": timestamp,
            "operation": operation,
            "data": data,
        }
    }
    file_logger(json.dumps(log_data))


def get_composio_client() -> Optional[Composio]:
    """
    Initialize and return authenticated Composio client.
    """
    if not COMPOSIO_AVAILABLE:
        logger.error("Composio is not available. Please install it first.")
        return None
        
    config = get_global_conf()
    
    api_key = config.get_composio_api_key()
    if not api_key:
        logger.error("COMPOSIO_API_KEY not found in configuration")
        return None
        
    try:
        composio = Composio(api_key=api_key)
        logger.info("Composio client initialized successfully")
        return composio
    except Exception as e:
        logger.error(f"Failed to initialize Composio client: {e}")
        return None


def authenticate_gmail_user() -> bool:
    """
    Check if Gmail user is authenticated using environment configuration.
    Returns True if tools are available, False otherwise.
    """
    if not COMPOSIO_AVAILABLE:
        return False
        
    config = get_global_conf()
    composio = get_composio_client()
    
    if not composio:
        return False
        
    user_id = config.get_composio_user_id()
    
    if not user_id:
        logger.error("Missing COMPOSIO_USER_ID")
        return False
        
    try:
        # Check if Gmail tools are available for the user
        gmail_tools = composio.tools.get(user_id=user_id, tools=["GMAIL_FETCH_EMAILS"])
        
        if gmail_tools:
            logger.info(f"Gmail tools available for user {user_id}")
            log_composio_operation("gmail_auth_check", {
                "user_id": user_id,
                "tools_available": True,
                "status": "success"
            })
            return True
        else:
            logger.warning(f"No Gmail tools available for user {user_id}")
            return False
        
    except Exception as e:
        logger.error(f"Failed to check Gmail tools for user: {e}")
        log_composio_operation("gmail_auth_check", {
            "user_id": user_id,
            "error": str(e),
            "status": "failed"
        })
        return False


@tool(
    agent_names=["composio_nav_agent"],
    description="Fetch emails from Gmail using Composio. This tool retrieves emails from the authenticated Gmail account and returns them in a structured format.",
    name="fetch_gmail_emails"
)
def fetch_gmail_emails(
    max_results: Annotated[int, "Maximum number of emails to fetch (default: 10)"] = 10,
    query: Annotated[str, "Gmail search query (optional, e.g., 'is:unread', 'from:example@gmail.com')"] = "",
    include_body: Annotated[bool, "Whether to include email body content (default: True)"] = True,
) -> str:
    """
    Fetch emails from Gmail using Composio's GMAIL_FETCH_EMAILS action.
    
    Args:
        max_results: Maximum number of emails to fetch
        query: Gmail search query to filter emails
        include_body: Whether to include email body content
        
    Returns:
        JSON string containing the email data or error message
    """
    if not COMPOSIO_AVAILABLE:
        error_msg = "Composio is not available. Please install composio-core first."
        logger.error(error_msg)
        return json.dumps({"error": error_msg, "status": "failed"})
    
    try:
        config = get_global_conf()
        
        # Check if Composio is enabled
        if not config.is_composio_enabled():
            error_msg = "Composio integration is disabled. Set COMPOSIO_ENABLED=true to enable it."
            logger.error(error_msg)
            return json.dumps({"error": error_msg, "status": "failed"})
        
        # Check if Gmail tools are available for user
        auth_success = authenticate_gmail_user()
        if not auth_success:
            error_msg = "Gmail tools not available for user - authentication may be required"
            logger.error(error_msg)
            return json.dumps({"error": error_msg, "status": "failed"})
        
        # Initialize Composio client
        composio = get_composio_client()
        if not composio:
            error_msg = "Failed to initialize Composio client"
            logger.error(error_msg)
            return json.dumps({"error": error_msg, "status": "failed"})
        
        # Prepare arguments for Gmail fetch
        arguments = {
            "limit": max_results,
        }
        
        if query:
            arguments["query"] = query
            
        # Execute the Gmail fetch tool
        start_time = time.time()
        
        result = composio.tools.execute(
            slug="GMAIL_FETCH_EMAILS",
            user_id=config.get_composio_user_id(),
            arguments=arguments
        )
        
        execution_time = time.time() - start_time
        
        log_composio_operation("gmail_fetch_emails", {
            "arguments": arguments,
            "execution_time": execution_time,
            "status": "success",
            "result_count": len(result.get("data", {}).get("messages", [])) if isinstance(result, dict) else 0
        })
        
        logger.info(f"Successfully fetched emails in {execution_time:.2f} seconds")
        
        # Format response for LLM
        email_data = result.get("data", {}) if isinstance(result, dict) else {}
        email_count = len(email_data.get("messages", [])) if email_data else 0
        
        response_data = {
            "status": "success",
            "execution_time": execution_time,
            "email_count": email_count,
            "emails": email_data,
            "query_used": query,
            "max_results": max_results,
            "raw_result": result,  # Include raw result for debugging
        }
        
        return json.dumps(response_data, indent=2)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        
        error_msg = f"Error fetching Gmail emails: {str(e)}"
        logger.error(error_msg)
        
        log_composio_operation("gmail_fetch_emails", {
            "arguments": {"limit": max_results, "query": query},
            "error": str(e),
            "status": "failed"
        })
        
        return json.dumps({
            "error": error_msg,
            "status": "failed",
            "arguments_used": {"limit": max_results, "query": query}
        })


@tool(
    agent_names=["composio_nav_agent"],
    description="Authenticate user with Composio Gmail service using OAuth flow. Shows redirect URL and waits for user to complete authentication.",
    name="authenticate_composio_user"
)
def authenticate_composio_user() -> Annotated[str, "Authenticate user with Composio Gmail service and return connection details"]:
    """
    Authenticate user with Composio Gmail service using OAuth flow.
    This will provide a redirect URL for user authentication and wait for completion.
    
    Returns:
        str: JSON string containing authentication status and connection details
    """
    if not COMPOSIO_AVAILABLE:
        error_msg = "Composio is not available. Please install composio first."
        logger.error(error_msg)
        return json.dumps({"error": error_msg, "status": "failed"})
    
    config = get_global_conf()
    
    if not config.is_composio_enabled():
        error_msg = "Composio is not enabled in configuration"
        logger.error(error_msg)
        return json.dumps({"error": error_msg, "status": "failed"})
    
    api_key = config.get_composio_api_key()
    user_id = config.get_composio_user_id()
    auth_config_id = config.get_composio_gmail_auth_config_id()
    
    if not api_key or not user_id or not auth_config_id:
        error_msg = "Missing required Composio configuration (API_KEY, USER_ID, or GMAIL_AUTH_CONFIG_ID)"
        logger.error(error_msg)
        return json.dumps({"error": error_msg, "status": "failed"})
    
    try:
        # Initialize Composio client
        composio = Composio(api_key=api_key)
        
        # Initiate connection request
        start_time = time.time()
        logger.info(f"Initiating Gmail authentication for user: {user_id}")
        
        connection_request = composio.connected_accounts.initiate(
            user_id=user_id,
            auth_config_id=auth_config_id,
        )
        
        # Get redirect URL for OAuth flow
        redirect_url = connection_request.redirect_url
        
        log_composio_operation("authenticate_user_initiated", {
            "user_id": user_id,
            "auth_config_id": auth_config_id,
            "redirect_url": redirect_url,
            "status": "oauth_initiated"
        })
        
        # Show authentication URL to user
        auth_response = {
            "status": "authentication_required",
            "message": "Please authorize the app by visiting the provided URL",
            "redirect_url": redirect_url,
            "user_id": user_id,
            "instructions": "Visit the redirect_url in your browser to complete Gmail authentication. After authentication, the connection will be established automatically."
        }
        
        logger.info(f"OAuth flow initiated. Redirect URL: {redirect_url}")
        print(f"\\n" + "="*80)
        print(f"COMPOSIO AUTHENTICATION REQUIRED")
        print(f"="*80)
        print(f"Please authorize the app by visiting this URL:")
        print(f"{redirect_url}")
        print(f"="*80)
        print(f"Waiting for authentication to complete...")
        
        # Wait for the connection to be established
        connected_account = connection_request.wait_for_connection()
        
        execution_time = time.time() - start_time
        
        # Authentication successful
        auth_response.update({
            "status": "success",
            "message": f"Connection established successfully! Connected account id: {connected_account.id}",
            "connected_account_id": connected_account.id,
            "execution_time": execution_time
        })
        
        log_composio_operation("authenticate_user_completed", {
            "user_id": user_id,
            "connected_account_id": connected_account.id,
            "execution_time": execution_time,
            "status": "success"
        })
        
        logger.info(f"Gmail authentication successful. Connected account ID: {connected_account.id}")
        print(f"\\n" + "="*80)
        print(f"AUTHENTICATION SUCCESSFUL!")
        print(f"Connected account ID: {connected_account.id}")
        print(f"="*80)
        
        return json.dumps(auth_response, indent=2)
        
    except Exception as e:
        execution_time = time.time() - start_time if 'start_time' in locals() else 0
        error_msg = f"Failed to authenticate user: {str(e)}"
        logger.error(error_msg)
        
        log_composio_operation("authenticate_user_failed", {
            "user_id": user_id,
            "error": str(e),
            "execution_time": execution_time,
            "status": "failed"
        })
        
        return json.dumps({
            "error": error_msg,
            "status": "failed",
            "execution_time": execution_time,
            "user_id": user_id
        })


@tool(
    agent_names=["composio_nav_agent"],
    description="Check Composio Gmail connection status and available tools for the authenticated user.",
    name="check_composio_status"
)
def check_composio_status() -> str:
    """
    Check the status of Composio integration and Gmail connection.
    
    Returns:
        JSON string containing connection status and available tools
    """
    if not COMPOSIO_AVAILABLE:
        return json.dumps({
            "status": "failed",
            "error": "Composio is not available. Please install composio-core first."
        })
    
    try:
        config = get_global_conf()
        
        # Check configuration
        status_data = {
            "composio_enabled": config.is_composio_enabled(),
            "api_key_configured": bool(config.get_composio_api_key()),
            "user_id_configured": bool(config.get_composio_user_id()),
            "auth_config_id_configured": bool(config.get_composio_gmail_auth_config_id()),
        }
        
        if not all([status_data["api_key_configured"], status_data["user_id_configured"], status_data["auth_config_id_configured"]]):
            return json.dumps({
                "status": "configuration_incomplete",
                "details": status_data,
                "message": "Missing required Composio environment variables"
            })
        
        # Initialize client and check connection
        composio = get_composio_client()
        if not composio:
            return json.dumps({
                "status": "failed",
                "error": "Failed to initialize Composio client"
            })
        
        # Check if Gmail tools are available for the user
        user_id = config.get_composio_user_id()
        gmail_connected = False
        
        try:
            gmail_tools = composio.tools.get(user_id=user_id, tools=["GMAIL_FETCH_EMAILS"])
            gmail_connected = bool(gmail_tools)
        except Exception as e:
            logger.warning(f"Could not check Gmail connection: {e}")
            gmail_connected = False
        
        # Get available Gmail tools
        available_tools = []
        try:
            user_id = config.get_composio_user_id()
            gmail_tools = composio.tools.get(user_id=user_id, tools=["GMAIL_FETCH_EMAILS"])
            available_tools = ["GMAIL_FETCH_EMAILS"] if gmail_tools else []
        except Exception as e:
            logger.warning(f"Could not fetch available tools: {e}")
            # Fallback to known Gmail tools
            available_tools = ["GMAIL_FETCH_EMAILS"]
        
        status_data.update({
            "gmail_connected": gmail_connected,
            "available_tools": available_tools,
            "user_id": user_id,
        })
        
        log_composio_operation("status_check", status_data)
        
        return json.dumps({
            "status": "success",
            "details": status_data
        }, indent=2)
        
    except Exception as e:
        error_msg = f"Error checking Composio status: {str(e)}"
        logger.error(error_msg)
        return json.dumps({
            "status": "failed",
            "error": error_msg
        })
