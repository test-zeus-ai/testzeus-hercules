"""HTTP requests for API steps: one ``http_request`` tool that never raises.

:func:`send` builds the request (method, URL with the inline query merged with
explicit parameters, headers, a body in one of :data:`BODY_MODES`), sends it with a
timeout, reads at most ``max_bytes`` of the response and returns an
:class:`HttpResponse`. Status codes are data, not errors: a 404 is a response the
step's assertions judge. Transport problems (refused connection, timeout, a body
too large) come back as ``error`` with a message whose URLs are redacted.
Redirects are not followed, so the status a step sees is the server's own.

:func:`exchange_record` is the evidence form of a request and its response:
credential headers, secret body fields and URL credentials masked, bodies capped.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx

from testzeus_hercules.act.files import PathConfinementError, confine
from testzeus_hercules.act.redact import redact_body_text, redact_headers, redact_text, redact_url

BODY_MODES = ("json", "raw", "urlencoded", "multipart", "binary")
AUTH_TYPES = ("basic", "bearer", "jwt", "api_key", "form_login")
DEFAULT_TIMEOUT_S = 30.0
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_EVIDENCE_CHARS = 64 * 1024
_NOT_JSON = object()

Pairs = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class HttpRequest:
    """A request as the steps describe it; ``body_mode`` None picks one from the body's type."""

    method: str
    url: str
    headers: Pairs = ()
    query: Pairs = ()
    body: Any = None
    body_mode: str | None = None


@dataclass(frozen=True)
class HttpResponse:
    """What came back (or why nothing did: ``error`` and ``error_kind``)."""

    status: int | None
    url: str  # as sent, with every query parameter
    duration_ms: int
    headers: Pairs = ()
    content: bytes = b""
    reason: str = ""
    error: str | None = None
    error_kind: str | None = None  # invalid_request, timeout, transport, too_large
    sent_headers: Pairs = ()
    sent_body: str | None = None
    _json: Any = field(default=None, repr=False, compare=False)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def content_type(self) -> str:
        return self.header("content-type") or ""

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def header(self, name: str) -> str | None:
        """The first header named ``name`` (case-insensitive), or None."""
        lowered = name.lower()
        return next((value for key, value in self.headers if key.lower() == lowered), None)

    def json(self) -> Any:
        """The parsed JSON body; ValueError when the body is not JSON."""
        if self._json is None:
            try:
                parsed: Any = json.loads(self.content) if self.content.strip() else _NOT_JSON
            except ValueError:
                parsed = _NOT_JSON
            object.__setattr__(self, "_json", parsed)
        if self._json is _NOT_JSON:
            raise ValueError("the response body is not JSON" if self.content.strip() else "the response body is empty")
        return self._json


def auth_headers(kind: str | None, value: Any) -> tuple[dict[str, str], str | None]:
    """Headers for an authentication type and value, or an error (unknown type, missing or malformed value)."""
    if not kind or str(kind).strip().lower() in ("", "none"):
        return {}, None
    name = str(kind).strip().lower()
    if name not in AUTH_TYPES:
        return {}, f"Unknown authentication type '{kind}'. Valid types: {', '.join(AUTH_TYPES)}."
    if value is None or (isinstance(value, str) and not value.strip()):
        return {}, f"authentication '{name}' needs a value, but none was given."
    if name == "basic":
        pair = value.split(":", 1) if isinstance(value, str) and ":" in value else value
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            return {}, "basic authentication needs 'username:password' (or a [username, password] pair)."
        token = base64.b64encode(f"{pair[0]}:{pair[1]}".encode()).decode()
        return {"Authorization": f"Basic {token}"}, None
    text = value if isinstance(value, str) else json.dumps(value)
    return {"bearer": {"Authorization": f"Bearer {text}"}, "jwt": {"Authorization": f"JWT {text}"}, "form_login": {"X-Form-Login": text}}.get(name, {"X-API-Key": text}), None


def resolve_body_mode(body: Any, mode: str | None) -> tuple[str | None, str | None]:
    """(mode, error): no body has no mode; an unset mode is json for structures, raw for text and binary for bytes."""
    if body is None:
        return None, None
    name = str(mode).strip().lower() if mode is not None else ""
    if not name:
        if isinstance(body, str):
            return "raw", None
        if isinstance(body, (bytes, bytearray)):
            return "binary", None
        return "json", None
    if name not in BODY_MODES:
        return None, f"Unknown body mode '{mode}'. Valid modes: {', '.join(BODY_MODES)}."
    return name, None


def text_content_type(body: str) -> str:
    """``application/json`` for a text body that is a JSON object or array, else ``text/plain``."""
    if body.lstrip()[:1] in ("{", "["):
        try:
            if isinstance(json.loads(body), (dict, list)):
                return "application/json"
        except ValueError:
            pass
    return "text/plain"


def multipart_fields(body: Any, roots: Iterable[Path]) -> dict[str, Any]:
    """httpx ``files=`` for a multipart body; ``{"file_path": ...}`` parts are read from inside ``roots`` only."""
    if not isinstance(body, dict):
        raise TypeError("a multipart body must map field names to values")
    fields: dict[str, Any] = {}
    for key, value in body.items():
        name = str(key)
        if isinstance(value, dict) and "file_path" in value:
            path = confine(str(value["file_path"]), roots)
            filename = str(value.get("filename") or path.name or name)
            content_type = value.get("content_type")
            data = path.read_bytes()
            fields[name] = (filename, data, content_type) if content_type else (filename, data)
        elif isinstance(value, bytes):
            fields[name] = (name, value)
        else:
            fields[name] = (None, value if isinstance(value, str) else json.dumps(value))
    return fields


def merged_query(url: str, explicit: Iterable[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """The URL's inline query merged with explicit parameters (explicit names win; repeated inline keys are kept)."""
    pairs = list(explicit)
    names = {name for name, _ in pairs}
    try:
        inline = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        inline = []
    expanded: list[tuple[str, Any]] = []
    for name, value in pairs:
        expanded.extend((name, item) for item in value) if isinstance(value, (list, tuple)) else expanded.append((name, value))
    return [(name, value) for name, value in inline if name not in names] + expanded


def _has_header(headers: dict[str, str], name: str) -> bool:
    return any(key.lower() == name.lower() for key in headers)


def _request_kwargs(request: HttpRequest, roots: Iterable[Path]) -> tuple[dict[str, Any], dict[str, str]]:
    """httpx keyword arguments and the final headers; ValueError/TypeError/OSError for an unusable body."""
    headers = dict(request.headers)
    query = merged_query(request.url, request.query)
    kwargs: dict[str, Any] = {"params": query or None}
    mode, problem = resolve_body_mode(request.body, request.body_mode)
    if problem:
        raise ValueError(problem)
    defaulted = request.body is not None and not (request.body_mode or "").strip()
    body = request.body
    if mode == "multipart":
        kwargs["files"] = multipart_fields(body, roots)
        headers = {key: value for key, value in headers.items() if key.lower() != "content-type"}  # httpx sets the boundary
    elif mode == "urlencoded":
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        kwargs["data" if isinstance(body, dict) else "content"] = body
    elif mode == "raw":
        if defaulted and isinstance(body, str) and not _has_header(headers, "Content-Type"):
            headers["Content-Type"] = text_content_type(body)
        kwargs["content"] = body if isinstance(body, (str, bytes)) else json.dumps(body)
    elif mode == "binary":
        headers.setdefault("Content-Type", "application/octet-stream")
        kwargs["content"] = body if isinstance(body, (bytes, bytearray)) else str(body).encode()
    elif mode == "json":
        if not _has_header(headers, "Content-Type"):
            headers["Content-Type"] = "application/json"
        kwargs["content"] = json.dumps(body).encode()
    return kwargs, headers


async def _read_capped(response: httpx.Response, max_bytes: int) -> bytes | None:
    """The body, or None when it is larger than ``max_bytes`` (announced or as read)."""
    announced = response.headers.get("content-length")
    if announced is not None and announced.isdigit() and int(announced) > max_bytes:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def send(
    request: HttpRequest,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = MAX_RESPONSE_BYTES,
    roots: Iterable[Path] = (),
    transport: httpx.AsyncBaseTransport | None = None,
) -> HttpResponse:
    """Send ``request`` and read its response; never raises (problems are in ``error``)."""
    method = request.method.strip().upper()
    started = time.perf_counter()

    def failed(kind: str, message: str, headers: Pairs = ()) -> HttpResponse:
        return HttpResponse(status=None, url=request.url, duration_ms=_ms(started), error=redact_text(message), error_kind=kind, sent_headers=headers)

    try:
        kwargs, headers = _request_kwargs(request, roots)
    except (ValueError, TypeError, OSError, PathConfinementError) as exc:
        return failed("invalid_request", f"invalid request body: {exc}")
    sent = tuple(headers.items())
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s), follow_redirects=False, transport=transport) as client:
            outgoing = client.build_request(method, request.url, headers=headers, **kwargs)
            sent_url, body_text = str(outgoing.url), _sent_body(outgoing)
            response = await client.send(outgoing, stream=True)
            try:
                content = await _read_capped(response, max_bytes)
            finally:
                await response.aclose()
            if content is None:
                return failed("too_large", f"the response body is larger than {max_bytes // (1024 * 1024)} MB", sent)
            return HttpResponse(
                status=response.status_code,
                reason=response.reason_phrase,
                headers=tuple(response.headers.multi_items()),
                content=content,
                url=sent_url,
                duration_ms=_ms(started),
                sent_headers=sent,
                sent_body=body_text,
            )
    except httpx.TimeoutException as exc:
        return failed("timeout", f"the request timed out after {timeout_s:g} s ({type(exc).__name__})", sent)
    except httpx.InvalidURL as exc:
        return failed("invalid_request", f"invalid URL: {exc}", sent)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return failed("transport", f"the request failed ({type(exc).__name__}): {exc}", sent)


def _sent_body(request: httpx.Request) -> str | None:
    try:
        content = request.read()
    except Exception:  # a streamed body that cannot be replayed is sent but not recorded
        return "<body not recorded>"
    return content.decode("utf-8", errors="replace") if content else None


def _ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _cap(text: str | None, limit: int = MAX_EVIDENCE_CHARS) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated: {len(text) - limit} more characters]"


def exchange_record(request: HttpRequest, response: HttpResponse) -> dict[str, Any]:
    """The evidence form of one exchange (credentials masked by name; the run's redactor masks secret values)."""
    sent_type = next((value for key, value in response.sent_headers if key.lower() == "content-type"), "")
    record: dict[str, Any] = {
        "request": {
            "method": request.method.upper(),
            "url": redact_url(response.url),
            "headers": [list(pair) for pair in redact_headers(response.sent_headers or request.headers)],
            "body": _cap(redact_body_text(response.sent_body or "", sent_type)) or None,
        },
        "duration_ms": response.duration_ms,
    }
    if response.error is not None:
        record["error"] = response.error
        return record
    record["response"] = {
        "status": response.status,
        "reason": response.reason,
        "headers": [list(pair) for pair in redact_headers(response.headers)],
        "body": _cap(redact_body_text(response.text, response.content_type)),
        "bytes": len(response.content),
    }
    return record
