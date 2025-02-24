import base64
import json
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger

# ------------------------------------------------------------------------------
# Logging and Utility Functions
# ------------------------------------------------------------------------------


def log_request(request: httpx.Request) -> None:
    """Log request details."""
    log_data: Dict[str, Any] = {
        "method": request.method,
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": (
            request.content.decode("utf-8", errors="ignore")
            if request.content
            else None
        ),
    }
    logger.debug(f"Request Data: {log_data}")


def log_response(response: httpx.Response) -> None:
    """Log response details."""
    log_data: Dict[str, Any] = {
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "body": response.text,
    }
    logger.debug(f"Response Data: {log_data}")


def determine_status_type(status_code: int) -> str:
    """
    Categorize the HTTP status code.
    """
    if 200 <= status_code < 300:
        return "success"
    elif 300 <= status_code < 400:
        return "redirect"
    elif 400 <= status_code < 500:
        return "client_error"
    elif 500 <= status_code < 600:
        return "server_error"
    return "unknown"


def handle_error_response(e: httpx.HTTPStatusError) -> dict:
    """
    Extract error details from an HTTPStatusError.
    """
    try:
        error_detail = e.response.json()
    except Exception:
        error_detail = e.response.text or "No details"
    return {
        "error": str(e),
        "error_detail": error_detail,
        "status_code": e.response.status_code,
        "status_type": determine_status_type(e.response.status_code),
    }


# ------------------------------------------------------------------------------
# Core Request Helper
# ------------------------------------------------------------------------------


def _send_request(
    method: str,
    url: str,
    *,
    query_params: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
    body_mode: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[str, float]:
    """
    Send an HTTP request using the given method and parameters.

    The 'body_mode' parameter specifies how to process the request body:
      - "multipart": Encodes a dict as multipart/form-data.
      - "urlencoded": Encodes a dict as application/x-www-form-urlencoded.
      - "raw": Sends the body as a raw string (caller must set Content-Type).
      - "binary": Sends the body as raw bytes (defaults to application/octet-stream).
      - "json": Encodes the body as JSON.
      - None: No body is sent.
    """
    query_params = query_params or {}
    headers = headers.copy() if headers else {}
    req_kwargs: Dict[str, Any] = {"params": query_params}

    if body_mode == "multipart" and isinstance(body, dict):
        files = {k: str(v) for k, v in body.items()}
        req_kwargs["files"] = files

    elif body_mode == "urlencoded" and body:
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        req_kwargs["data"] = body

    elif body_mode == "raw" and body:
        req_kwargs["content"] = body

    elif body_mode == "binary" and body:
        headers.setdefault("Content-Type", "application/octet-stream")
        req_kwargs["content"] = body

    elif body_mode == "json" and body:
        headers.setdefault("Content-Type", "application/json")
        req_kwargs["json"] = body

    start_time = time.perf_counter()

    with httpx.Client(follow_redirects=True) as client:
        response = client.request(method, url, headers=headers, **req_kwargs)
        response.raise_for_status()
        elapsed_time = time.perf_counter() - start_time
        return response.text, elapsed_time


# ------------------------------------------------------------------------------
# Generic HTTP API Function Covering All Combinations
# ------------------------------------------------------------------------------


@tool(
    agent_names=["api_nav_agent"],
    name="generic_http_api",
    description=(
        "Generic HTTP API call that supports any combination of HTTP method, "
        "authentication, query parameters, and request body encoding. "
        "Parameters:\n"
        "  - method: HTTP method (GET, POST, PUT, PATCH, DELETE, etc.).\n"
        "  - url: The API endpoint URL.\n"
        "  - auth_type: Authentication type. Options: basic, jwt, form_login, bearer, api_key.\n"
        "  - auth_value: For 'basic', pass [username, password]; for others, a string.\n"
        "  - query_params: URL query parameters (dict).\n"
        "  - body: Request payload.\n"
        "  - body_mode: How to encode the body. Options: multipart, urlencoded, raw, binary, json.\n"
        "  - headers: Additional HTTP headers (dict).\n"
        "This single function can generate any combination supported by _send_request."
    ),
)
def generic_http_api(
    method: str,
    url: str,
    auth_type: Optional[str] = None,
    auth_value: Optional[Any] = None,
    query_params: Dict[str, Any] = {},
    body: Optional[Any] = None,
    body_mode: Optional[str] = None,
    headers: Dict[str, str] = {},
) -> Tuple[str, float]:
    # Set authentication headers based on auth_type.
    if auth_type:
        auth_type = auth_type.lower()
        if (
            auth_type == "basic"
            and isinstance(auth_value, list)
            and len(auth_value) == 2
        ):
            creds = f"{auth_value[0]}:{auth_value[1]}"
            token = base64.b64encode(creds.encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        elif auth_type == "jwt":
            headers["Authorization"] = f"JWT {auth_value}"
        elif auth_type == "form_login":
            headers["X-Form-Login"] = auth_value
        elif auth_type == "bearer":
            headers["Authorization"] = f"Bearer {auth_value}"
        elif auth_type == "api_key":
            headers["x-api-key"] = auth_value

    return _send_request(
        method,
        url,
        query_params=query_params,
        body=body,
        body_mode=body_mode,
        headers=headers,
    )


@tool(
    agent_names=["api_nav_agent"],
    description="Send an HTTP request with the specified method and parameters.",
    name="send_http_request",
)
def send_http_request(
    method: str,
    url: str,
    query_params: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
    body_mode: Optional[str] = None,
    auth_type: Optional[str] = None,
    auth_value: Optional[Union[str, List[str]]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[str, float]:
    """
    Send an HTTP request with the specified method and parameters.

    Returns:
        Tuple[str, float]: Response text and call duration in seconds
    """
    # Set authentication headers based on auth_type.
    headers = headers or {}
    if auth_type:
        auth_type_lower = auth_type.lower()
        if (
            auth_type_lower == "basic"
            and isinstance(auth_value, list)
            and len(auth_value) == 2
            and all(isinstance(v, str) for v in auth_value)
        ):
            creds = f"{auth_value[0]}:{auth_value[1]}"
            token = base64.b64encode(creds.encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        elif auth_type_lower == "bearer" and isinstance(auth_value, str):
            headers["Authorization"] = f"Bearer {auth_value}"
        elif auth_type_lower == "api_key" and isinstance(auth_value, str):
            headers["X-API-Key"] = auth_value

    return _send_request(
        method=method,
        url=url,
        query_params=query_params,
        body=body,
        body_mode=body_mode,
        headers=headers,
    )
