import base64
import json
import time
from typing import Annotated, Any, Dict, List, Optional, Tuple

import httpx
from testzeus_hercules.core.tools.tool_registry import api_logger as file_logger
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger

# ------------------------------------------------------------------------------
# Logging and Utility Functions
# ------------------------------------------------------------------------------


async def log_request(request: httpx.Request) -> None:
    """
    Log details of the outgoing HTTP request.
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    log_data = {
        "timestamp": timestamp,
        "method": request.method,
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": (request.content.decode("utf-8", errors="ignore") if request.content else None),
    }
    file_logger(f"Request Data: {log_data}")


async def log_response(response: httpx.Response) -> None:
    """
    Log details of the incoming HTTP response.
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    try:
        body_bytes = await response.aread()
        body = body_bytes.decode("utf-8", errors="ignore")
    except Exception as e:
        body = f"Failed to read response: {e}"
    log_data = {
        "timestamp": timestamp,
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "body": body,
    }
    file_logger(f"Response Data: {log_data}")


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


async def handle_error_response(e: httpx.HTTPStatusError) -> dict:
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


async def _send_request(
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
    req_kwargs = {"params": query_params}

    if body_mode == "multipart" and body:
        form = httpx.FormData()
        for key, value in body.items():
            form.add_field(key, value)
        req_kwargs["data"] = form

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
    try:
        async with httpx.AsyncClient(
            event_hooks={"request": [log_request], "response": [log_response]},
            timeout=httpx.Timeout(5.0),
        ) as client:
            response = await client.request(method, url, headers=headers, **req_kwargs)
            response.raise_for_status()
            duration = time.perf_counter() - start_time

            try:
                parsed_body = response.json()
            except Exception:
                parsed_body = response.text or ""
            result = {
                "status_code": response.status_code,
                "status_type": determine_status_type(response.status_code),
                "body": parsed_body,
            }
            # Minify the JSON response and replace double quotes with single quotes.
            result_str = json.dumps(result, separators=(",", ":")).replace('"', "'")
            return result_str, duration

    except httpx.HTTPStatusError as e:
        duration = time.perf_counter() - start_time
        logger.error(f"HTTP error: {e}")
        error_data = await handle_error_response(e)
        return json.dumps(error_data, separators=(",", ":")).replace('"', "'"), duration

    except Exception as e:
        duration = time.perf_counter() - start_time
        logger.error(f"Unexpected error: {e}")
        error_data = {"error": str(e), "status_code": None, "status_type": "failure"}
        return json.dumps(error_data, separators=(",", ":")).replace('"', "'"), duration


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
async def generic_http_api(
    method: Annotated[str, "HTTP method (e.g. GET, POST, PUT, PATCH, DELETE, etc.)."],
    url: Annotated[str, "The API endpoint URL."],
    auth_type: Annotated[
        str,
        "Authentication type. Options: basic, jwt, form_login, bearer, api_key. (Optional)",
    ] = None,
    auth_value: Annotated[
        Any,
        "Authentication value: for 'basic' provide [username, password]; for others, provide a string. (Optional)",
    ] = None,
    query_params: Annotated[Dict[str, Any], "URL query parameters."] = {},
    body: Annotated[Any, "Request payload."] = None,
    body_mode: Annotated[
        str,
        "Body mode: multipart, urlencoded, raw, binary, or json. (Optional)",
    ] = None,
    headers: Annotated[Dict[str, str], "Additional HTTP headers."] = {},
) -> Annotated[Tuple[str, float], "Minified JSON response and call duration (in seconds)."]:
    # Set authentication headers based on auth_type.
    if auth_type:
        auth_type = auth_type.lower()
        if auth_type == "basic" and isinstance(auth_value, list) and len(auth_value) == 2:
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

    return await _send_request(
        method,
        url,
        query_params=query_params,
        body=body,
        body_mode=body_mode,
        headers=headers,
    )
