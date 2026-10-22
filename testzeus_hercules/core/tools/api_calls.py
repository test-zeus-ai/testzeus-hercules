import base64
import json
import os
import re
import time
import urllib.parse
from collections import defaultdict
from typing import Annotated, Any, Dict, List, Optional, Tuple

import httpx
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.tool_registry import api_logger as file_logger
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.jsonpath_helper import closest_parent_keys, find_json_path
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.path_guard import resolve_in_roots
from testzeus_hercules.utils.redact import (
    API_LOG_REDACTION_MAX_DEPTH,
    _redact_post_data,
    _redact_sensitive_headers,
    is_sensitive_name,
    redact_url,
)

# Maximum response body bytes the log hook captures. Without the cap a multi-GB
# download is aread() into memory just to be logged. Bodies announced via
# content-length as bigger than this are not read at all by the hook (a
# truncation notice is stored instead); bodies without a content-length that
# turn out bigger are sliced before storage.
MAX_CAPTURE_BYTES = 1_000_000

# Maximum characters of a response payload returned as the tool result; an
# oversized body is truncated (with a marker, the result stays valid JSON)
# before it reaches the agent's context.
MAX_RESULT_CHARS = 100_000

# Maximum characters of a request body written to the api log. Response bodies
# keep their own capture cap (MAX_CAPTURE_BYTES, enforced in log_response);
# request bodies were previously uncapped.
MAX_LOG_BODY_CHARS = 64 * 1024

# ------------------------------------------------------------------------------
# Per-scenario variable store and ${name} interpolation
# ------------------------------------------------------------------------------

# Captured values are scoped per test id, exactly like
# core/memory/state_handler.py scopes its per-test state. The runner resets the
# scope when a scenario starts (reset_variables) so a re-run, or another
# scenario with the same title, never sees stale captures.
_VARIABLES: Dict[str, Dict[str, Any]] = defaultdict(dict)

# ${name} placeholders (the same syntax the agent writes in assertions,
# URLs and pre-written Hurl suites).
_PLACEHOLDER_RE = re.compile(r"\$\{([^{}]+)\}")


def _var_store() -> Dict[str, Any]:
    """Return the variable store of the current test id."""
    return _VARIABLES[get_global_conf().get_default_test_id()]


def reset_variables(test_id: Optional[str] = None) -> None:
    """Drop the captured variables of `test_id` (default: the current test); call at scenario start."""
    _VARIABLES.pop(test_id if test_id is not None else get_global_conf().get_default_test_id(), None)


def sensitive_variable_values() -> list:
    """Text forms of the current scenario's captured values whose NAME marks them secret (scrubbed from ledgers)."""
    return [stringify_interpolated_value(value) for name, value in _var_store().items() if is_sensitive_name(name)]


def set_variable_value(as_name: str, value: Any) -> None:
    """Store a captured value under `as_name` for the current test."""
    _var_store()[str(as_name)] = value


def stringify_interpolated_value(value: Any) -> str:
    """Render a value as JSON literal text (str(None) would produce "None")."""
    return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))


def interpolate_value(value: Any) -> Any:
    """
    Resolve ${name} placeholders against the per-scenario variable store.

    - "${x}" exactly => the captured value with its own type (an int stays int)
    - embedded placeholders => text substitution
    - unknown names are left as-is so the typo shows up in the request or
      assertion instead of raising
    """
    if not isinstance(value, str):
        return value
    store = _var_store()
    exact = _PLACEHOLDER_RE.fullmatch(value.strip())
    if exact:
        name = exact.group(1).strip()
        if name in store:
            return store[name]
        return value

    def _substitute(match: "re.Match[str]") -> str:
        name = match.group(1).strip()
        if name in store:
            return stringify_interpolated_value(store[name])
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_substitute, value)


def _interpolate_tree(node: Any) -> Any:
    """Apply ${name} interpolation to every string leaf of a JSON-ish tree."""
    if isinstance(node, dict):
        return {key: _interpolate_tree(item) for key, item in node.items()}
    if isinstance(node, list):
        return [_interpolate_tree(item) for item in node]
    return interpolate_value(node)


def parse_json_payload(response_json: Any) -> Any:
    """
    Accept an already-parsed document or a JSON string and return the document.

    Raises ValueError when a string payload is not valid JSON.
    """
    if isinstance(response_json, (str, bytes, bytearray)):
        return json.loads(response_json)
    return response_json


class RemoteSchemaRefError(ValueError):
    """A JSON Schema ``$ref`` pointed outside the document being validated."""


def _refuse_remote_ref(uri: str) -> Any:
    """``referencing`` retrieve hook: only in-document refs may resolve, never http(s)/file/relative URIs."""
    raise RemoteSchemaRefError(f"Remote or external $ref '{uri}' is not allowed; only local '#/...' references within the same document can be resolved.")


def local_only_schema_registry() -> Any:
    """Registry that knows the bundled metaschemas but refuses to retrieve any other URI.

    jsonschema's default registry fetches unknown ``$ref`` URIs with urllib, which
    allows SSRF (http://) and local file reads (file://). Pass this as ``registry=``
    to every validator that handles model- or spec-supplied schemas.
    """
    from jsonschema_specifications import REGISTRY
    from referencing import Registry

    return REGISTRY.combine(Registry(retrieve=_refuse_remote_ref))


def remote_ref_error(exc: BaseException) -> Optional[RemoteSchemaRefError]:
    """Return the RemoteSchemaRefError buried in a jsonschema/referencing exception chain, if any."""
    seen = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, RemoteSchemaRefError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


# ------------------------------------------------------------------------------
# Logging and Utility Functions
# ------------------------------------------------------------------------------


def truncate_for_log(body: Optional[str], limit: int = MAX_LOG_BODY_CHARS) -> Optional[str]:
    """Cap a body destined for the log file at `limit` characters."""
    if body is None or len(body) <= limit:
        return body
    return body[:limit] + f"...[truncated: logged only the first {limit} characters]"


async def log_request(request: httpx.Request) -> None:
    """
    Log details of the outgoing HTTP request (secrets redacted, body capped).
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    # Read the body unconditionally so streamed (multipart) requests are
    # logged too: aread() is cached, and swaps in a replayable stream so the
    # actual send is unaffected. A body that cannot be read is logged without
    # its content.
    try:
        await request.aread()
        request_body = request.content.decode("utf-8", errors="ignore") if request.content else None
    except Exception as e:
        request_body = f"<request body not captured: {type(e).__name__}: {e}>"
    redacted_headers = _redact_sensitive_headers(dict(request.headers))
    content_type = redacted_headers.get("content-type", "") if isinstance(redacted_headers, dict) else ""
    log_data = {
        "request_data": {
            "timestamp": timestamp,
            "method": request.method,
            "url": redact_url(str(request.url)),
            "headers": redacted_headers,
            # Redact the full body first, then cap it for the log: a secret
            # straddling the truncation boundary would otherwise leave a
            # fragment behind.
            "body": truncate_for_log(_redact_post_data(request_body, str(content_type or ""), API_LOG_REDACTION_MAX_DEPTH)),
        }
    }
    file_logger(json.dumps(log_data))


async def log_response(response: httpx.Response) -> None:
    """
    Log details of the incoming HTTP response (secrets redacted).

    Only the first MAX_CAPTURE_BYTES of the body are captured. Bodies whose
    content-length already exceeds the cap are not read at all (httpx hands the
    hook a streaming response, so aread() would buffer the whole body); a
    truncation notice is stored instead. Reading is skipped only here — the
    caller's own read of the body is unaffected either way, because aread()
    caches the content for the eventual response.json()/.text().
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    body: str
    announced_length: Optional[int] = None
    body_exceeds_capture_cap = False
    content_length_raw = response.headers.get("content-length")
    if content_length_raw is not None:
        try:
            announced_length = int(content_length_raw)
        except ValueError:
            announced_length = None
    if announced_length is not None and announced_length > MAX_CAPTURE_BYTES:
        body = f"[response body of {announced_length} bytes not captured: exceeds the {MAX_CAPTURE_BYTES}-byte log limit]"
    else:
        try:
            body_bytes = await response.aread()
            body = body_bytes.decode("utf-8", errors="ignore")
            body_exceeds_capture_cap = len(body_bytes) > MAX_CAPTURE_BYTES
        except Exception as e:
            import traceback

            traceback.print_exc()
            body = f"Failed to read response: {e}"
    redacted_headers = _redact_sensitive_headers(dict(response.headers))
    content_type = redacted_headers.get("content-type", "") if isinstance(redacted_headers, dict) else ""
    # Redact the full body first, then cap it for the log: a secret straddling
    # the truncation boundary would otherwise leave a fragment behind.
    body = _redact_post_data(body, str(content_type or ""), API_LOG_REDACTION_MAX_DEPTH)
    if body_exceeds_capture_cap:
        body = body[:MAX_CAPTURE_BYTES] + f"...[truncated: logged only the first {MAX_CAPTURE_BYTES} bytes]"
    log_data = {
        "response_data": {
            "timestamp": timestamp,
            "status_code": response.status_code,
            "headers": redacted_headers,
            "body": body,
        }
    }
    file_logger(json.dumps(log_data))


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


_URL_IN_TEXT_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s'\"<>]+")


def _redacted_error_text(error: BaseException) -> str:
    """str(error) with every URL in it passed through redact_url (httpx messages embed the full request URL)."""
    return _URL_IN_TEXT_RE.sub(lambda match: redact_url(match.group(0)), str(error))


async def handle_error_response(e: httpx.HTTPStatusError) -> dict:
    """
    Extract error details from an HTTPStatusError.
    """
    try:
        error_detail = e.response.json()
    except Exception as ex:

        logger.exception(f"Error extracting error details: {ex}")
        error_detail = e.response.text or "No details"
    return {
        "error": _redacted_error_text(e),
        "error_detail": error_detail,
        "status_code": e.response.status_code,
        "status_type": determine_status_type(e.response.status_code),
    }


# ------------------------------------------------------------------------------
# Multipart Encoding
# ------------------------------------------------------------------------------


def build_multipart_fields(body: Any) -> Dict[str, Any]:
    """
    Build the `files=` mapping httpx needs to actually send multipart/form-data.

    Text parts pass through `files` with a None filename so httpx selects
    multipart encoding for the whole request (passing them via `data=` would
    silently url-encode it). A value of the form {"file_path": ...} becomes a
    real file part (the path must resolve inside the test data / project roots,
    else PathConfinementError, a ValueError, is raised); bytes become a file
    part named after the field.
    """
    if not isinstance(body, dict):
        raise TypeError("multipart body must be a mapping of field names to values")
    fields: Dict[str, Any] = {}
    for key, value in body.items():
        field_name = str(key)
        if isinstance(value, dict) and "file_path" in value:
            # Model-supplied path: confined to the test data / project roots
            # (realpath), so an upload cannot be steered at arbitrary files.
            file_path = resolve_in_roots(str(value["file_path"]))
            filename = str(value.get("filename") or os.path.basename(file_path) or field_name)
            with open(file_path, "rb") as file_handle:
                content = file_handle.read()
            content_type = value.get("content_type")
            fields[field_name] = (filename, content, content_type) if content_type else (filename, content)
        elif isinstance(value, bytes):
            fields[field_name] = (field_name, value)
        else:
            fields[field_name] = (None, stringify_interpolated_value(value))
    return fields


# ------------------------------------------------------------------------------
# Core Request Helper
# ------------------------------------------------------------------------------


def _bounded_payload(result: Dict[str, Any], duration: float) -> str:
    """
    Serialize a result payload for the tool result: always one valid JSON
    document (no quote mangling — replacing '"' with "'" corrupts string
    values), with the call duration as the `duration_seconds` field.

    A document longer than MAX_RESULT_CHARS is shortened by truncating its
    largest text field (`body`, else `error_detail`) and flagging
    `body_truncated`/`error_detail_truncated`, so the result still parses.
    """
    result = {**result, "duration_seconds": round(duration, 2)}
    payload = json.dumps(result, separators=(",", ":"))
    if len(payload) <= MAX_RESULT_CHARS:
        return payload
    for key in ("body", "error_detail"):
        if key not in result:
            continue
        value = result[key]
        text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
        marker = f"...[truncated: response exceeded {MAX_RESULT_CHARS} characters]"
        keep = max(MAX_RESULT_CHARS - len(json.dumps({**result, key: marker, f"{key}_truncated": True}, separators=(",", ":"))), 0)
        while True:
            shortened = {**result, key: text[:keep] + marker, f"{key}_truncated": True}
            payload = json.dumps(shortened, separators=(",", ":"))
            if len(payload) <= MAX_RESULT_CHARS or keep == 0:
                break
            keep = max(keep - (len(payload) - MAX_RESULT_CHARS) - 1, 0)
        if len(payload) <= MAX_RESULT_CHARS:
            return payload
    # Nothing to shorten (or still too large): hard fallback that stays valid JSON.
    return json.dumps(
        {
            "status_code": result.get("status_code"),
            "status_type": result.get("status_type"),
            "error": f"Result exceeded {MAX_RESULT_CHARS} characters and was dropped.",
            "duration_seconds": result["duration_seconds"],
        },
        separators=(",", ":"),
    )


async def _send_request(
    method: str,
    url: str,
    *,
    query_params: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
    body_mode: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> str:
    """
    Send an HTTP request using the given method and parameters.

    The 'body_mode' parameter specifies how to process the request body:
      - "multipart": Encodes a dict as multipart/form-data (see
        build_multipart_fields for how field values map to parts).
      - "urlencoded": Encodes a dict as application/x-www-form-urlencoded.
      - "raw": Sends the body as a raw string (caller must set Content-Type).
      - "binary": Sends the body as raw bytes (defaults to application/octet-stream).
      - "json": Encodes the body as JSON.
      - None: No body is sent.

    Returns the JSON-encoded result payload as a single string (the executor
    str()s tool results, so returning a tuple would double-encode it). The
    call duration is the `duration_seconds` field of that JSON document, and
    the document is capped at MAX_RESULT_CHARS characters (see _bounded_payload).
    """
    headers = headers.copy() if headers else {}
    # httpx replaces the URL's own query string with `params`, so the inline
    # query (…?status=paid&x=1) must be extracted and merged with the explicit
    # query_params (explicit keys win) — otherwise it is silently dropped or
    # overwritten. A list of pairs keeps repeated inline keys (?tag=a&tag=b).
    # With no query at all, params=None leaves the raw URL alone.
    try:
        inline_query = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        inline_query = []
    explicit_query = query_params or {}
    merged_query: List[Tuple[str, Any]] = [(key, value) for key, value in inline_query if key not in explicit_query]
    merged_query.extend(explicit_query.items())
    req_kwargs = {"params": merged_query if merged_query else None}

    if body_mode == "multipart" and body is not None:
        try:
            req_kwargs["files"] = build_multipart_fields(body)
        except (TypeError, ValueError, OSError) as e:
            return _bounded_payload({"error": f"Invalid multipart body: {e}", "status_code": None, "status_type": "failure"}, 0.0)
        # The multipart boundary must be the one httpx generates; a
        # caller-supplied Content-Type would carry no boundary and corrupt
        # the request.
        for header_name in ("Content-Type", "content-type"):
            headers.pop(header_name, None)

    elif body_mode == "urlencoded" and body is not None:
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        req_kwargs["data"] = body

    elif body_mode == "raw" and body is not None:
        req_kwargs["content"] = body

    elif body_mode == "binary" and body is not None:
        headers.setdefault("Content-Type", "application/octet-stream")
        req_kwargs["content"] = body

    elif body_mode == "json" and body is not None:
        headers.setdefault("Content-Type", "application/json")
        req_kwargs["json"] = body

    timeout_seconds = get_global_conf().get_api_request_timeout()
    start_time = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            event_hooks={"request": [log_request], "response": [log_response]},
            timeout=httpx.Timeout(timeout_seconds),
        ) as client:
            response = await client.request(method, url, headers=headers, **req_kwargs)
            response.raise_for_status()
            duration = time.perf_counter() - start_time

            try:
                parsed_body = response.json()
            except Exception as e:

                logger.exception(f"Error parsing response body: {e}")
                parsed_body = response.text or ""
            result = {
                "status_code": response.status_code,
                "status_type": determine_status_type(response.status_code),
                "body": parsed_body,
            }
            return _bounded_payload(result, duration)

    except httpx.HTTPStatusError as e:
        duration = time.perf_counter() - start_time
        logger.error(f"HTTP error: {_redacted_error_text(e)}")
        error_data = await handle_error_response(e)
        return _bounded_payload(error_data, duration)

    except httpx.TimeoutException as e:
        # httpx timeout str() is empty, so name the timeout explicitly —
        # otherwise the agent sees a bare failure with no hint at the knob.
        duration = time.perf_counter() - start_time
        logger.error(f"Request timed out after {timeout_seconds:g}s: {type(e).__name__}")
        error_data = {
            "error": (f"Request timed out after {timeout_seconds:g}s ({type(e).__name__}). " "Raise API_REQUEST_TIMEOUT (seconds, default 5) if this endpoint legitimately needs longer."),
            "timeout_seconds": timeout_seconds,
            "status_code": None,
            "status_type": "timeout",
        }
        return _bounded_payload(error_data, duration)

    except Exception as e:
        import traceback

        traceback.print_exc()
        duration = time.perf_counter() - start_time
        logger.error(f"Unexpected error: {_redacted_error_text(e)}")
        error_data = {"error": _redacted_error_text(e), "status_code": None, "status_type": "failure"}
        return _bounded_payload(error_data, duration)


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
    query_params: Annotated[Optional[Dict[str, Any]], "URL query parameters."] = None,
    body: Annotated[Any, "Request payload."] = None,
    body_mode: Annotated[
        str,
        "Body mode: multipart, urlencoded, raw, binary, or json. (Optional)",
    ] = None,
    headers: Annotated[Optional[Dict[str, str]], "Additional HTTP headers."] = None,
) -> Annotated[str, "Minified JSON response ({status_code, status_type, body, duration_seconds}); the call duration is the duration_seconds field."]:
    # ${name} placeholders captured earlier (extract_from_response) are
    # resolved everywhere; an unknown name is left as-is so the typo shows up
    # in the request instead of raising.
    url = str(interpolate_value(url))
    request_headers = {str(key): stringify_interpolated_value(interpolate_value(value)) for key, value in (headers or {}).items()}
    request_query_params = {key: _interpolate_tree(value) for key, value in (query_params or {}).items()}
    request_body = _interpolate_tree(body) if body is not None else body

    # Set authentication headers based on auth_type.
    if auth_type:
        auth_type = auth_type.lower()
        if auth_type == "basic" and isinstance(auth_value, list) and len(auth_value) == 2:
            creds = f"{auth_value[0]}:{auth_value[1]}"
            token = base64.b64encode(creds.encode()).decode()
            request_headers["Authorization"] = f"Basic {token}"
        elif auth_type == "jwt":
            request_headers["Authorization"] = f"JWT {auth_value}"
        elif auth_type == "form_login":
            request_headers["X-Form-Login"] = auth_value
        elif auth_type == "bearer":
            request_headers["Authorization"] = f"Bearer {auth_value}"
        elif auth_type == "api_key":
            request_headers["x-api-key"] = auth_value

    return await _send_request(
        method,
        url,
        query_params=request_query_params,
        body=request_body,
        body_mode=body_mode,
        headers=request_headers,
    )


# ------------------------------------------------------------------------------
# Response Value Extraction
# ------------------------------------------------------------------------------


@tool(
    agent_names=["api_nav_agent"],
    name="extract_from_response",
    description=(
        "Capture a value from a JSON response body into a named variable for "
        "later use. Reference the captured value in subsequent calls or "
        "assertions as ${as_name}. Returns the stored value and its type."
    ),
)
async def extract_from_response(
    response_json: Annotated[Any, "The JSON response body (object or JSON string) to extract from."],
    path: Annotated[str, "JSONPath expression pointing at the value to capture, e.g. $.body.items[0].id."],
    as_name: Annotated[str, "Name to store the value under; reference it later as ${as_name}."],
) -> Annotated[str, "Minified JSON with the capture verdict and the stored value."]:
    try:
        document = parse_json_payload(response_json)
    except ValueError as e:
        return json.dumps({"assertion": "ERROR", "path": path, "as_name": as_name, "reason": f"Response is not valid JSON: {e}"}, separators=(",", ":"))

    try:
        matches = find_json_path(document, path)
    except ImportError as e:
        return json.dumps({"assertion": "ERROR", "path": path, "as_name": as_name, "reason": str(e)}, separators=(",", ":"))
    except Exception as e:
        return json.dumps({"assertion": "ERROR", "path": path, "as_name": as_name, "reason": f"Invalid JSONPath: {e}"}, separators=(",", ":"))

    if not matches:
        result: Dict[str, Any] = {"assertion": "FAILED", "path": path, "as_name": as_name, "reason": "Path matched nothing; nothing was captured."}
        parent_info = closest_parent_keys(document, path)
        if parent_info:
            result["available_at"] = parent_info
        return json.dumps(result, separators=(",", ":"))

    value = matches[0]
    set_variable_value(as_name, value)
    return json.dumps(
        {
            "assertion": "PASSED",
            "path": path,
            "as_name": as_name,
            "value": value,
            "reference": f"${{{as_name}}}",
            "match_count": len(matches),
        },
        separators=(",", ":"),
    )


# ------------------------------------------------------------------------------
# Hand-written JSON Schema Validation
# ------------------------------------------------------------------------------


@tool(
    agent_names=["api_nav_agent"],
    name="validate_response_schema",
    description=(
        "Validate a JSON response body (or a sub-value selected by JSONPath) "
        "against a JSON Schema. Use this to verify the whole shape of a "
        "response — required fields, types, enums, array constraints — in one "
        "deterministic check instead of judging fields by eye."
    ),
)
async def validate_response_schema(
    response_json: Annotated[Any, "The JSON response body (object or JSON string) to validate."],
    json_schema: Annotated[Any, "The JSON Schema (object or JSON string) to validate against."],
    path: Annotated[Optional[str], "Optional JSONPath selecting the sub-value to validate. Defaults to the whole body."] = None,
) -> Annotated[str, "Minified JSON with the verdict and a list of violations."]:
    try:
        import jsonschema
    except ImportError as e:
        return json.dumps(
            {"assertion": "ERROR", "reason": f"jsonschema is not installed ({e}). Install the API extras: pip install 'testzeus-hercules[api]'."},
            separators=(",", ":"),
        )

    try:
        document = parse_json_payload(response_json)
    except ValueError as e:
        return json.dumps({"assertion": "ERROR", "reason": f"Response is not valid JSON: {e}"}, separators=(",", ":"))
    try:
        schema = parse_json_payload(json_schema)
    except ValueError as e:
        return json.dumps({"assertion": "ERROR", "reason": f"Schema is not valid JSON: {e}"}, separators=(",", ":"))

    instance_location = "$"
    if path:
        try:
            matches = find_json_path(document, path)
        except ImportError as e:
            return json.dumps({"assertion": "ERROR", "reason": str(e)}, separators=(",", ":"))
        except Exception as e:
            return json.dumps({"assertion": "ERROR", "path": path, "reason": f"Invalid JSONPath: {e}"}, separators=(",", ":"))
        if not matches:
            return json.dumps({"assertion": "FAILED", "path": path, "reason": "Path matched nothing in the response body."}, separators=(",", ":"))
        document = matches[0]
        instance_location = path

    try:
        validator_class = jsonschema.validators.validator_for(schema)
        # jsonschema >= 4.26 no longer checks the schema while constructing a
        # validator, so an invalid schema silently fell through and produced
        # nonsense per-instance violations; check it explicitly.
        validator_class.check_schema(schema)
        validator = validator_class(schema, registry=local_only_schema_registry())
        errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    except jsonschema.SchemaError as e:
        return json.dumps({"assertion": "ERROR", "reason": f"Invalid JSON Schema: {e.message}"}, separators=(",", ":"))
    except Exception as e:
        refused = remote_ref_error(e)
        return json.dumps({"assertion": "ERROR", "reason": f"Schema validation could not run: {refused or e}"}, separators=(",", ":"))

    violations = [
        {
            "path": _instance_error_path(instance_location, error.absolute_path),
            "message": error.message,
        }
        for error in errors
    ]
    result = {
        "assertion": "FAILED" if violations else "PASSED",
        "schema_path": path or "$",
        "violations": violations,
        "violation_count": len(violations),
    }
    return json.dumps(result, separators=(",", ":"))


def _instance_error_path(instance_location: str, absolute_path) -> str:
    """Render a violation location as a readable path under the instance."""
    segments = [str(segment) for segment in absolute_path]
    if instance_location == "$":
        return "$" + "".join(f"[{segment}]" if segment.isdigit() else f".{segment}" for segment in segments)
    if not segments:
        return instance_location
    return instance_location + "." + ".".join(segments)
