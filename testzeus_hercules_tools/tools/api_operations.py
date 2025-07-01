"""
Dual-mode API operations tool.
"""

import base64
import json
import time
from typing import Optional, Dict, Any, Tuple
import httpx
from .base import BaseTool
from .logger import InteractionLogger
from ..config import ToolsConfig
from ..playwright_manager import ToolsPlaywrightManager


class ApiOperationsTool(BaseTool):
    """Dual-mode API operations tool."""
    
    def __init__(self, config: Optional[ToolsConfig] = None, playwright_manager: Optional[ToolsPlaywrightManager] = None):
        super().__init__(config, playwright_manager)
        self.logger = InteractionLogger(config)


async def http_request(
    method: str,
    url: str,
    auth_type: Optional[str] = None,
    auth_value: Optional[Any] = None,
    query_params: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
    body_mode: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Execute HTTP request with dual-mode support.
    
    Args:
        method: HTTP method (GET, POST, PUT, PATCH, DELETE, etc.)
        url: API endpoint URL
        auth_type: Authentication type (basic, jwt, bearer, api_key, etc.)
        auth_value: Authentication value
        query_params: URL query parameters
        body: Request payload
        body_mode: Body encoding mode (multipart, urlencoded, raw, binary, json)
        headers: Additional HTTP headers
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and response data
    """
    tool = ApiOperationsTool(config, playwright_manager)
    
    try:
        request_headers = headers.copy() if headers else {}
        query_params = query_params or {}
        
        if auth_type:
            auth_type_lower = auth_type.lower()
            if auth_type_lower == "basic" and isinstance(auth_value, list) and len(auth_value) == 2:
                creds = f"{auth_value[0]}:{auth_value[1]}"
                token = base64.b64encode(creds.encode()).decode()
                request_headers["Authorization"] = f"Basic {token}"
            elif auth_type_lower == "jwt":
                request_headers["Authorization"] = f"JWT {auth_value}"
            elif auth_type_lower == "bearer":
                request_headers["Authorization"] = f"Bearer {auth_value}"
            elif auth_type_lower == "api_key":
                request_headers["x-api-key"] = str(auth_value)
        
        req_kwargs = {"params": query_params}
        
        if body_mode and body:
            if body_mode == "multipart":
                form = httpx.FormData()
                for key, value in body.items():
                    form.add_field(key, value)
                req_kwargs["data"] = form
            elif body_mode == "urlencoded":
                request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
                req_kwargs["data"] = body
            elif body_mode == "raw":
                req_kwargs["content"] = body
            elif body_mode == "binary":
                request_headers.setdefault("Content-Type", "application/octet-stream")
                req_kwargs["content"] = body
            elif body_mode == "json":
                request_headers.setdefault("Content-Type", "application/json")
                req_kwargs["json"] = body
        
        start_time = time.perf_counter()
        
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.request(
                method, url, headers=request_headers, **req_kwargs
            )
            
            duration = time.perf_counter() - start_time
            
            try:
                response_body = response.json()
            except Exception:
                response_body = response.text
            
            if 200 <= response.status_code < 300:
                status_type = "success"
            elif 300 <= response.status_code < 400:
                status_type = "redirect"
            elif 400 <= response.status_code < 500:
                status_type = "client_error"
            elif 500 <= response.status_code < 600:
                status_type = "server_error"
            else:
                status_type = "unknown"
            
            result = {
                "success": response.status_code < 400,
                "status_code": response.status_code,
                "status_type": status_type,
                "body": response_body,
                "headers": dict(response.headers),
                "duration": duration,
                "method": method,
                "url": url,
                "mode": tool.config.mode
            }
            
            await tool.logger.log_interaction(
                tool_name="http_request",
                selector=url,
                action=f"{method.upper()}_request",
                success=result["success"],
                mode=tool.config.mode,
                additional_data={
                    "method": method,
                    "status_code": response.status_code,
                    "status_type": status_type,
                    "duration": duration,
                    "auth_type": auth_type,
                    "body_mode": body_mode
                }
            )
            
            return result
            
    except httpx.HTTPStatusError as e:
        duration = time.perf_counter() - start_time
        
        try:
            error_detail = e.response.json()
        except Exception:
            error_detail = e.response.text or "No details"
        
        result = {
            "success": False,
            "error": str(e),
            "error_detail": error_detail,
            "status_code": e.response.status_code,
            "duration": duration,
            "method": method,
            "url": url,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="http_request",
            selector=url,
            action=f"{method.upper()}_request",
            success=False,
            error_message=str(e),
            mode=tool.config.mode,
            additional_data={
                "method": method,
                "status_code": e.response.status_code,
                "duration": duration,
                "error_type": "http_status"
            }
        )
        
        return result
        
    except Exception as e:
        duration = time.perf_counter() - start_time if 'start_time' in locals() else 0
        
        result = {
            "success": False,
            "error": f"Request failed: {str(e)}",
            "duration": duration,
            "method": method,
            "url": url,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="http_request",
            selector=url,
            action=f"{method.upper()}_request",
            success=False,
            error_message=str(e),
            mode=tool.config.mode,
            additional_data={
                "method": method,
                "duration": duration,
                "error_type": "unexpected"
            }
        )
        
        return result
