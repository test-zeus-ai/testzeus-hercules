import os
from typing import Annotated, Any, Dict, Optional

import httpx
from testzeus_hercules.core.skills.skill_registry import skill
from testzeus_hercules.utils.logger import logger


## Uncomment this skill if you need to create a resource using an HTTP API
# @skill(name="create_resource_http_api", description="Only when instruction says call an API to create an entity in the remote system, then use this skill.")
async def create_resource_http_api(
    url: Annotated[str, "The API endpoint URL for creating the resource."],
    headers: Annotated[
        Optional[Dict[str, str]], "Optional HTTP headers to include in the request."
    ] = None,
    auth: Annotated[
        Optional[Dict[str, str]],
        "Optional basic authentication credentials (e.g., {'username': 'user', 'password': 'pass'}).",
    ] = None,
    token: Annotated[
        Optional[str],
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = None,
    data: Annotated[
        Optional[Dict[str, Any]], "Form data to send in the request body."
    ] = None,
    json_data: Annotated[
        Optional[Dict[str, Any]], "JSON data to send in the request body."
    ] = None,
) -> Annotated[Dict[str, Any], "The response data from the API call."]:
    """
    Create a new resource by sending a POST request to the specified URL.

    url: str - The API endpoint URL for creating the resource.
    headers: Optional[Dict[str, str]] - Optional HTTP headers to include in the request.
    auth: Optional[Dict[str, str]] - Optional basic authentication credentials (e.g., {'username': 'user', 'password': 'pass'}).
    token: Optional[str] - Optional bearer or JWT token for authentication.
    data: Optional[Dict[str, Any]] - Form data to send in the request body.
    json_data: Optional[Dict[str, Any]] - JSON data to send in the request body.

    returns: Dict[str, Any] - The response data from the API call.
    """
    try:
        logger.info(f"Sending POST request to {url}")

        # Prepare headers
        request_headers = headers.copy() if headers else {}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers=request_headers,
                auth=(
                    httpx.BasicAuth(auth["username"], auth["password"])
                    if auth
                    else None
                ),
                data=data,
                json=json_data,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error occurred while creating resource: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return {"error": str(e)}


@skill(
    name="read_resource_http_api",
    description="Only when instruction says call an API to read entities from the remote system, then use this skill.",
)
async def read_resource_http_api(
    url: Annotated[str, "The API endpoint URL for reading the resource."],
    headers: Annotated[
        Optional[Dict[str, str]], "Optional HTTP headers to include in the request."
    ] = None,
    auth: Annotated[
        Optional[Dict[str, str]],
        "Optional basic authentication credentials (e.g., {'username': 'user', 'password': 'pass'}).",
    ] = None,
    token: Annotated[
        Optional[str],
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = None,
    params: Annotated[
        Optional[Dict[str, Any]], "Query parameters to include in the GET request."
    ] = None,
) -> Annotated[Dict[str, Any], "The response data from the API call."]:
    """
    Read a resource by sending a GET request to the specified URL.

    url: str - The API endpoint URL for reading the resource.
    headers: Optional[Dict[str, str]] - Optional HTTP headers to include in the request.
    auth: Optional[Dict[str, str]] - Optional basic authentication credentials.
    token: Optional[str] - Optional bearer or JWT token for authentication.
    params: Optional[Dict[str, Any]] - Query parameters to include in the GET request.

    returns: Dict[str, Any] - The response data from the API call.
    """
    try:
        logger.info(f"Sending GET request to {url}")

        # Prepare headers
        request_headers = headers.copy() if headers else {}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers=request_headers,
                auth=(
                    httpx.BasicAuth(auth["username"], auth["password"])
                    if auth
                    else None
                ),
                params=params,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error occurred while reading resource: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return {"error": str(e)}


## Uncomment this skill if you need to update a resource using an HTTP API
# @skill(name="update_resource_http_api", description="Only when instruction says call an API to update an entity in the remote system, then use this skill.")
async def update_resource_http_api(
    url: Annotated[str, "The API endpoint URL for updating the resource."],
    headers: Annotated[
        Optional[Dict[str, str]], "Optional HTTP headers to include in the request."
    ] = None,
    auth: Annotated[
        Optional[Dict[str, str]],
        "Optional basic authentication credentials (e.g., {'username': 'user', 'password': 'pass'}).",
    ] = None,
    token: Annotated[
        Optional[str],
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = None,
    data: Annotated[
        Optional[Dict[str, Any]], "Form data to send in the request body."
    ] = None,
    json_data: Annotated[
        Optional[Dict[str, Any]], "JSON data to send in the request body."
    ] = None,
) -> Annotated[Dict[str, Any], "The response data from the API call."]:
    """
    Update an existing resource by sending a PUT request to the specified URL.

    url: str - The API endpoint URL for updating the resource.
    headers: Optional[Dict[str, str]] - Optional HTTP headers to include in the request.
    auth: Optional[Dict[str, str]] - Optional basic authentication credentials.
    token: Optional[str] - Optional bearer or JWT token for authentication.
    data: Optional[Dict[str, Any]] - Form data to send in the request body.
    json_data: Optional[Dict[str, Any]] - JSON data to send in the request body.

    returns: Dict[str, Any] - The response data from the API call.
    """
    try:
        logger.info(f"Sending PUT request to {url}")

        # Prepare headers
        request_headers = headers.copy() if headers else {}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient() as client:
            response = await client.put(
                url,
                headers=request_headers,
                auth=(
                    httpx.BasicAuth(auth["username"], auth["password"])
                    if auth
                    else None
                ),
                data=data,
                json=json_data,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error occurred while updating resource: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return {"error": str(e)}


## Uncomment this skill if you need to delete a resource using an HTTP API
# @skill(name="delete_resource_http_api", description="Only when instruction says call an API to delete an entity in the remote system, then use this skill.")
async def delete_resource_http_api(
    url: Annotated[str, "The API endpoint URL for deleting the resource."],
    headers: Annotated[
        Optional[Dict[str, str]], "Optional HTTP headers to include in the request."
    ] = None,
    auth: Annotated[
        Optional[Dict[str, str]],
        "Optional basic authentication credentials (e.g., {'username': 'user', 'password': 'pass'}).",
    ] = None,
    token: Annotated[
        Optional[str],
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = None,
) -> Annotated[Dict[str, Any], "The response data from the API call."]:
    """
    Delete a resource by sending a DELETE request to the specified URL.

    url: str - The API endpoint URL for deleting the resource.
    headers: Optional[Dict[str, str]] - Optional HTTP headers to include in the request.
    auth: Optional[Dict[str, str]] - Optional basic authentication credentials.
    token: Optional[str] - Optional bearer or JWT token for authentication.

    returns: Dict[str, Any] - The response data from the API call.
    """
    try:
        logger.info(f"Sending DELETE request to {url}")

        # Prepare headers
        request_headers = headers.copy() if headers else {}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                url,
                headers=request_headers,
                auth=(
                    httpx.BasicAuth(auth["username"], auth["password"])
                    if auth
                    else None
                ),
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error occurred while deleting resource: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return {"error": str(e)}
