import json
import time
from typing import Annotated, Any, Dict, Optional, Tuple

import httpx
from testzeus_hercules.core.tools.tool_registry import api_logger as file_logger
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger


async def log_request(request: httpx.Request) -> None:
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
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    if response.is_stream_consumed:
        body = "Stream already consumed."
    else:
        try:
            body = await response.aread()
            body = body.decode("utf-8", errors="ignore")
        except Exception as e:
            body = f"Failed to read streaming response: {e}"

    log_data = {
        "timestamp": timestamp,
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "body": body,
    }
    file_logger(f"Response Data: {log_data}")


def determine_status_type(status_code: int) -> str:
    """
    A helper function to determine the status type (success, redirect, client_error, server_error, unknown)
    based on the status code.
    """
    if 200 <= status_code < 300:
        return "success"
    elif 300 <= status_code < 400:
        return "redirect"
    elif 400 <= status_code < 500:
        return "client_error"
    elif 500 <= status_code < 600:
        return "server_error"
    else:
        return "unknown"


@tool(
    agent_names=["api_nav_agent"],
    name="create_resource_http_api",
    description="Only when instruction says call an API to create an entity in the remote system, then use this tool. Should be used for POST requests. ALL TOOL ARGUMENTS ARE MANDATORY",
)
async def create_resource_http_api(
    url: Annotated[str, "The API endpoint URL for creating the resource."],
    headers: Annotated[dict, "Optional HTTP headers to include in the request."] = {},
    auth: Annotated[
        dict,
        "Optional basic authentication credentials (e.g., {'username': 'user', 'password': 'pass'}).",
    ] = {},
    token: Annotated[
        str,
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = "",
    data: Annotated[dict, "Form data to send in the request body."] = {},
    json_data: Annotated[dict, "JSON data to send in the request body."] = {},
) -> Annotated[
    Tuple[str, float],
    "A tuple containing a minified JSON string and the time taken for the API call in seconds.",
]:
    start_time = time.perf_counter()
    try:
        logger.info(f"Sending POST request to {url}")

        # Prepare headers
        request_headers = headers.copy() if headers else {}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(event_hooks={"request": [log_request], "response": [log_response]}) as client:
            response = await client.post(
                url,
                headers=request_headers,
                auth=(httpx.BasicAuth(auth["username"], auth["password"]) if auth else None),
                data=data,
                json=json_data,
            )
            response.raise_for_status()

            end_time = time.perf_counter()
            duration = end_time - start_time

            status_code = response.status_code
            status_type = determine_status_type(status_code)

            # Attempt to parse the response as JSON, else fallback to raw text
            try:
                parsed_body = response.json()
            except json.JSONDecodeError:
                parsed_body = response.text or ""

            response_data = {
                "status_code": status_code,
                "status_type": status_type,
                "body": parsed_body,
            }

            # Convert the response_data dict to a minified JSON string
            response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
            return (response_str, duration)

    except httpx.HTTPStatusError as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"HTTP error occurred while creating resource: {e}")

        status_code = e.response.status_code
        status_type = determine_status_type(status_code)

        response_data = {
            "error": str(e),
            "status_code": status_code,
            "status_type": status_type,
        }
        response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
        return (response_str, duration)

    except Exception as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"An unexpected error occurred: {e}")

        response_data = {
            "error": str(e),
            "status_code": None,
            "status_type": "failure",
        }
        response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
        return (response_str, duration)


@tool(
    agent_names=["api_nav_agent"],
    name="read_resource_http_api",
    description="Only when instruction says call an API to read entities from the remote system, then use this tool, Should be used for GET requests. ALL TOOL ARGUMENTS ARE MANDATORY",
)
async def read_resource_http_api(
    url: Annotated[str, "The API endpoint URL for reading the resource."],
    headers: Annotated[dict, "Optional HTTP headers to include in the request."] = {},
    auth: Annotated[
        dict,
        "Optional basic authentication credentials.",
    ] = {},
    token: Annotated[
        str,
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = "",
    params: Annotated[dict, "Query parameters to include in the GET request."] = {},
) -> Annotated[
    Tuple[str, float],
    "A tuple containing a minified JSON string and the time taken for the API call in seconds.",
]:
    start_time = time.perf_counter()
    try:
        logger.info(f"Sending GET request to {url}")

        request_headers = headers.copy() if headers else {}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(event_hooks={"request": [log_request], "response": [log_response]}) as client:
            response = await client.get(
                url,
                headers=request_headers,
                auth=(httpx.BasicAuth(auth["username"], auth["password"]) if auth else None),
                params=params,
            )
            response.raise_for_status()

            end_time = time.perf_counter()
            duration = end_time - start_time

            status_code = response.status_code
            status_type = determine_status_type(status_code)

            try:
                parsed_body = response.json()
            except json.JSONDecodeError:
                parsed_body = response.text or ""

            response_data = {
                "status_code": status_code,
                "status_type": status_type,
                "body": parsed_body,
            }

            response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
            return (response_str, duration)

    except httpx.HTTPStatusError as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"HTTP error occurred while reading resource: {e}")

        status_code = e.response.status_code
        status_type = determine_status_type(status_code)

        response_data = {
            "error": str(e),
            "status_code": status_code,
            "status_type": status_type,
        }
        response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
        return (response_str, duration)

    except Exception as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"An unexpected error occurred: {e}")

        response_data = {
            "error": str(e),
            "status_code": None,
            "status_type": "failure",
        }
        response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
        return (response_str, duration)


@tool(
    agent_names=["api_nav_agent"],
    name="update_resource_http_api",
    description="Only when instruction says call an API to update an entity in the remote system, then use this tool. Should be used for PUT requests. ALL TOOL ARGUMENTS ARE MANDATORY",
)
async def update_resource_http_api(
    url: Annotated[str, "The API endpoint URL for updating the resource."],
    headers: Annotated[dict, "Optional HTTP headers to include in the request."] = {},
    auth: Annotated[
        dict,
        "Optional basic authentication credentials.",
    ] = {},
    token: Annotated[
        str,
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = "",
    data: Annotated[dict, "Form data to send in the request body."] = {},
    json_data: Annotated[dict, "JSON data to send in the request body."] = {},
) -> Annotated[
    Tuple[str, float],
    "A tuple containing a minified JSON string and the time taken for the API call in seconds.",
]:
    start_time = time.perf_counter()
    try:
        logger.info(f"Sending PUT request to {url}")

        request_headers = headers.copy() if headers else {}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(event_hooks={"request": [log_request], "response": [log_response]}) as client:
            response = await client.put(
                url,
                headers=request_headers,
                auth=(httpx.BasicAuth(auth["username"], auth["password"]) if auth else None),
                data=data,
                json=json_data,
            )
            response.raise_for_status()

            end_time = time.perf_counter()
            duration = end_time - start_time

            status_code = response.status_code
            status_type = determine_status_type(status_code)

            try:
                parsed_body = response.json()
            except json.JSONDecodeError:
                parsed_body = response.text or ""

            response_data = {
                "status_code": status_code,
                "status_type": status_type,
                "body": parsed_body,
            }

            response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
            return (response_str, duration)

    except httpx.HTTPStatusError as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"HTTP error occurred while updating resource: {e}")

        status_code = e.response.status_code
        status_type = determine_status_type(status_code)

        response_data = {
            "error": str(e),
            "status_code": status_code,
            "status_type": status_type,
        }
        response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
        return (response_str, duration)

    except Exception as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"An unexpected error occurred: {e}")

        response_data = {
            "error": str(e),
            "status_code": None,
            "status_type": "failure",
        }
        response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
        return (response_str, duration)


@tool(
    agent_names=["api_nav_agent"],
    name="patch_resource_http_api",
    description="Only when instruction says call an API to patch an entity in the remote system, then use this tool. Should be used for PATCH requests. ALL TOOL ARGUMENTS ARE MANDATORY",
)
async def patch_resource_http_api(
    url: Annotated[str, "The API endpoint URL for patching the resource."],
    headers: Annotated[dict, "Optional HTTP headers to include in the request."] = {},
    auth: Annotated[
        dict,
        "Optional basic authentication credentials.",
    ] = {},
    token: Annotated[
        str,
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = "",
    data: Annotated[dict, "Form data to send in the request body."] = {},
    json_data: Annotated[dict, "JSON data to send in the request body."] = {},
) -> Annotated[
    Tuple[str, float],
    "A tuple containing a minified JSON string and the time taken for the API call in seconds.",
]:
    start_time = time.perf_counter()
    try:
        logger.info(f"Sending PATCH request to {url}")

        request_headers = headers.copy() if headers else {}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(event_hooks={"request": [log_request], "response": [log_response]}) as client:
            response = await client.patch(
                url,
                headers=request_headers,
                auth=(httpx.BasicAuth(auth["username"], auth["password"]) if auth else None),
                data=data,
                json=json_data,
            )
            response.raise_for_status()

            end_time = time.perf_counter()
            duration = end_time - start_time

            status_code = response.status_code
            status_type = determine_status_type(status_code)

            try:
                parsed_body = response.json()
            except json.JSONDecodeError:
                parsed_body = response.text or ""

            response_data = {
                "status_code": status_code,
                "status_type": status_type,
                "body": parsed_body,
            }

            response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
            return (response_str, duration)

    except httpx.HTTPStatusError as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"HTTP error occurred while patching resource: {e}")

        status_code = e.response.status_code
        status_type = determine_status_type(status_code)

        response_data = {
            "error": str(e),
            "status_code": status_code,
            "status_type": status_type,
        }
        response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
        return (response_str, duration)

    except Exception as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"An unexpected error occurred: {e}")

        response_data = {
            "error": str(e),
            "status_code": None,
            "status_type": "failure",
        }
        response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
        return (response_str, duration)


@tool(
    agent_names=["api_nav_agent"],
    name="delete_resource_http_api",
    description="Only when instruction says call an API to delete an entity in the remote system, then use this tool. Should be used for DELETE requests. ALL TOOL ARGUMENTS ARE MANDATORY",
)
async def delete_resource_http_api(
    url: Annotated[str, "The API endpoint URL for deleting the resource."],
    headers: Annotated[dict, "Optional HTTP headers to include in the request."] = {},
    auth: Annotated[
        dict,
        "Optional basic authentication credentials.",
    ] = {},
    token: Annotated[
        str,
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = "",
) -> Annotated[
    Tuple[str, float],
    "A tuple containing a minified JSON string and the time taken for the API call in seconds.",
]:
    start_time = time.perf_counter()
    try:
        logger.info(f"Sending DELETE request to {url}")

        request_headers = headers.copy() if headers else {}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(event_hooks={"request": [log_request], "response": [log_response]}) as client:
            response = await client.delete(
                url,
                headers=request_headers,
                auth=(httpx.BasicAuth(auth["username"], auth["password"]) if auth else None),
            )
            response.raise_for_status()

            end_time = time.perf_counter()
            duration = end_time - start_time

            status_code = response.status_code
            status_type = determine_status_type(status_code)

            try:
                parsed_body = response.json()
            except json.JSONDecodeError:
                parsed_body = response.text or ""

            response_data = {
                "status_code": status_code,
                "status_type": status_type,
                "body": parsed_body,
            }

            response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
            return (response_str, duration)

    except httpx.HTTPStatusError as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"HTTP error occurred while deleting resource: {e}")

        status_code = e.response.status_code
        status_type = determine_status_type(status_code)

        response_data = {
            "error": str(e),
            "status_code": status_code,
            "status_type": status_type,
        }
        response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
        return (response_str, duration)

    except Exception as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"An unexpected error occurred: {e}")

        response_data = {
            "error": str(e),
            "status_code": None,
            "status_type": "failure",
        }
        response_str = json.dumps(response_data, separators=(",", ":")).replace('"', "'")
        return (response_str, duration)
