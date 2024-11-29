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


@tool(
    agent_names=["api_nav_agent"],
    name="create_resource_http_api",
    description="Only when instruction says call an API to create an entity in the remote system, then use this tool. Should be used for POST requests.",
)
async def create_resource_http_api(
    url: Annotated[str, "The API endpoint URL for creating the resource."],
    headers: Annotated[Optional[Dict[str, str]], "Optional HTTP headers to include in the request."] = None,
    auth: Annotated[
        Optional[Dict[str, str]],
        "Optional basic authentication credentials (e.g., {'username': 'user', 'password': 'pass'}).",
    ] = None,
    token: Annotated[
        Optional[str],
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = None,
    data: Annotated[Optional[Dict[str, Any]], "Form data to send in the request body."] = None,
    json_data: Annotated[Optional[Dict[str, Any]], "JSON data to send in the request body."] = None,
) -> Annotated[
    Tuple[Any, float],
    "A tuple containing the response data or error message, and the time taken for the API call in seconds.",
]:
    """
    Create a new resource by sending a POST request to the specified URL.

    url: str - The API endpoint URL for creating the resource.
    headers: Optional[Dict[str, str]] - Optional HTTP headers to include in the request.
    auth: Optional[Dict[str, str]] - Optional basic authentication credentials.
    token: Optional[str] - Optional bearer or JWT token for authentication.
    data: Optional[Dict[str, Any]] - Form data to send in the request body.
    json_data: Optional[Dict[str, Any]] - JSON data to send in the request body.

    returns: Tuple[Any, float] - A tuple containing the response data or error message, and the time taken for the API call in seconds.
    """
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

            try:
                response_data = response.json()
            except:
                response_data = response.text or {}

            return (response_data, duration)
    except httpx.HTTPStatusError as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"HTTP error occurred while creating resource: {e}")
        return ({"error": str(e)}, duration)
    except Exception as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"An unexpected error occurred: {e}")
        return ({"error": str(e)}, duration)


@tool(
    agent_names=["api_nav_agent"],
    name="read_resource_http_api",
    description="Only when instruction says call an API to read entities from the remote system, then use this tool, Should be used for GET requests.",
)
async def read_resource_http_api(
    url: Annotated[str, "The API endpoint URL for reading the resource."],
    headers: Annotated[Optional[Dict[str, str]], "Optional HTTP headers to include in the request."] = None,
    auth: Annotated[
        Optional[Dict[str, str]],
        "Optional basic authentication credentials.",
    ] = None,
    token: Annotated[
        Optional[str],
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = None,
    params: Annotated[Optional[Dict[str, Any]], "Query parameters to include in the GET request."] = None,
) -> Annotated[
    Tuple[Any, float],
    "A tuple containing the response data or error message, and the time taken for the API call in seconds.",
]:
    """
    Read a resource by sending a GET request to the specified URL.

    url: str - The API endpoint URL for reading the resource.
    headers: Optional[Dict[str, str]] - Optional HTTP headers to include in the request.
    auth: Optional[Dict[str, str]] - Optional basic authentication credentials.
    token: Optional[str] - Optional bearer or JWT token for authentication.
    params: Optional[Dict[str, Any]] - Query parameters to include in the GET request.

    returns: Tuple[Any, float] - A tuple containing the response data or error message, and the time taken for the API call in seconds.
    """
    start_time = time.perf_counter()
    try:
        logger.info(f"Sending GET request to {url}")

        # Prepare headers
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

            try:
                response_data = response.json()
            except:
                response_data = response.text or {}

            return (response_data, duration)
    except httpx.HTTPStatusError as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"HTTP error occurred while reading resource: {e}")
        return ({"error": str(e)}, duration)
    except Exception as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"An unexpected error occurred: {e}")
        return ({"error": str(e)}, duration)


@tool(
    agent_names=["api_nav_agent"],
    name="update_resource_http_api",
    description="Only when instruction says call an API to update an entity in the remote system, then use this tool. Should be used for PUT requests.",
)
async def update_resource_http_api(
    url: Annotated[str, "The API endpoint URL for updating the resource."],
    headers: Annotated[Optional[Dict[str, str]], "Optional HTTP headers to include in the request."] = None,
    auth: Annotated[
        Optional[Dict[str, str]],
        "Optional basic authentication credentials.",
    ] = None,
    token: Annotated[
        Optional[str],
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = None,
    data: Annotated[Optional[Dict[str, Any]], "Form data to send in the request body."] = None,
    json_data: Annotated[Optional[Dict[str, Any]], "JSON data to send in the request body."] = None,
) -> Annotated[
    Tuple[Any, float],
    "A tuple containing the response data or error message, and the time taken for the API call in seconds.",
]:
    """
    Update an existing resource by sending a PUT request to the specified URL.

    url: str - The API endpoint URL for updating the resource.
    headers: Optional[Dict[str, str]] - Optional HTTP headers to include in the request.
    auth: Optional[Dict[str, str]] - Optional basic authentication credentials.
    token: Optional[str] - Optional bearer or JWT token for authentication.
    data: Optional[Dict[str, Any]] - Form data to send in the request body.
    json_data: Optional[Dict[str, Any]] - JSON data to send in the request body.

    returns: Tuple[Any, float] - A tuple containing the response data or error message, and the time taken for the API call in seconds.
    """
    start_time = time.perf_counter()
    try:
        logger.info(f"Sending PUT request to {url}")

        # Prepare headers
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

            try:
                response_data = response.json()
            except:
                response_data = response.text or {}

            return (response_data, duration)
    except httpx.HTTPStatusError as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"HTTP error occurred while updating resource: {e}")
        return ({"error": str(e)}, duration)
    except Exception as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"An unexpected error occurred: {e}")
        return ({"error": str(e)}, duration)


@tool(
    agent_names=["api_nav_agent"],
    name="patch_resource_http_api",
    description="Only when instruction says call an API to patch an entity in the remote system, then use this tool. Should be used for PATCH requests.",
)
async def patch_resource_http_api(
    url: Annotated[str, "The API endpoint URL for patching the resource."],
    headers: Annotated[Optional[Dict[str, str]], "Optional HTTP headers to include in the request."] = None,
    auth: Annotated[
        Optional[Dict[str, str]],
        "Optional basic authentication credentials.",
    ] = None,
    token: Annotated[
        Optional[str],
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = None,
    data: Annotated[Optional[Dict[str, Any]], "Form data to send in the request body."] = None,
    json_data: Annotated[Optional[Dict[str, Any]], "JSON data to send in the request body."] = None,
) -> Annotated[
    Tuple[Any, float],
    "A tuple containing the response data or error message, and the time taken for the API call in seconds.",
]:
    """
    Patch an existing resource by sending a PATCH request to the specified URL.

    url: str - The API endpoint URL for patching the resource.
    headers: Optional[Dict[str, str]] - Optional HTTP headers to include in the request.
    auth: Optional[Dict[str, str]] - Optional basic authentication credentials.
    token: Optional[str] - Optional bearer or JWT token for authentication.
    data: Optional[Dict[str, Any]] - Form data to send in the request body.
    json_data: Optional[Dict[str, Any]] - JSON data to send in the request body.

    returns: Tuple[Any, float] - A tuple containing the response data or error message, and the time taken for the API call in seconds.
    """
    start_time = time.perf_counter()
    try:
        logger.info(f"Sending PATCH request to {url}")

        # Prepare headers
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

            try:
                response_data = response.json()
            except:
                response_data = response.text or {}

            return (response_data, duration)
    except httpx.HTTPStatusError as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"HTTP error occurred while patching resource: {e}")
        return ({"error": str(e)}, duration)
    except Exception as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"An unexpected error occurred: {e}")
        return ({"error": str(e)}, duration)


@tool(
    agent_names=["api_nav_agent"],
    name="delete_resource_http_api",
    description="Only when instruction says call an API to delete an entity in the remote system, then use this tool. Should be used for DELETE requests.",
)
async def delete_resource_http_api(
    url: Annotated[str, "The API endpoint URL for deleting the resource."],
    headers: Annotated[Optional[Dict[str, str]], "Optional HTTP headers to include in the request."] = None,
    auth: Annotated[
        Optional[Dict[str, str]],
        "Optional basic authentication credentials.",
    ] = None,
    token: Annotated[
        Optional[str],
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = None,
) -> Annotated[
    Tuple[Any, float],
    "A tuple containing the response data or error message, and the time taken for the API call in seconds.",
]:
    """
    Delete a resource by sending a DELETE request to the specified URL.

    url: str - The API endpoint URL for deleting the resource.
    headers: Optional[Dict[str, str]] - Optional HTTP headers to include in the request.
    auth: Optional[Dict[str, str]] - Optional basic authentication credentials.
    token: Optional[str] - Optional bearer or JWT token for authentication.

    returns: Tuple[Any, float] - A tuple containing the response data or error message, and the time taken for the API call in seconds.
    """
    start_time = time.perf_counter()
    try:
        logger.info(f"Sending DELETE request to {url}")

        # Prepare headers
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

            try:
                response_data = response.json()
            except:
                response_data = response.text or {}

            return (response_data, duration)
    except httpx.HTTPStatusError as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"HTTP error occurred while deleting resource: {e}")
        return ({"error": str(e)}, duration)
    except Exception as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(f"An unexpected error occurred: {e}")
        return ({"error": str(e)}, duration)
