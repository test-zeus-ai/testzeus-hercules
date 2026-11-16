"""Request guard for the daemon: Host and Origin checks, bearer token, the UI session cookie, body type and size.

* ``Host`` must name a loopback address (blocks DNS rebinding); with remote access allowed it may also name the
  bound host or one of the hostnames listed in ``HERCULES_DAEMON_ALLOWED_HOSTS``.
* A present ``Origin`` must be the daemon's own origin: the scheme, host and port of the request's ``Host``
  (another port on localhost, such as the app under test, is a different origin and is refused).
* Every request needs ``Authorization: Bearer <token>`` or the session cookie, except
  ``/login?code=...``, which trades a one-time code for an HttpOnly, SameSite=Strict session cookie
  (12 h, at most 64 sessions) so the browser UI never sees the token.
* A cookie-authenticated request is refused when the browser says it came from another site or port
  (``Sec-Fetch-Site: cross-site`` or ``same-site``; top-level page navigations excepted), and a cookie-authenticated
  request that is not GET or HEAD must carry the same ``Origin``.
* A request body must be ``application/json`` (415 otherwise) and at most ``MAX_BODY_BYTES`` (413), counted
  from ``Content-Length`` and from the bytes actually received.

WebSocket requests (the live view) pass the same checks; a browser's WebSocket always
carries an ``Origin``, which must be the daemon's own, and authenticates with the session cookie.
A WebSocket without an ``Origin`` (not a browser) must present the bearer token. A
refused WebSocket is closed before it is accepted (the client sees HTTP 403).

Failures are answered with a generic JSON body; the token is never echoed.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import parse_qs, urlsplit

from testzeus_hercules.ui.assets import sign_in_page
from testzeus_hercules.ui.brand import PAGE_CSP

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

SESSION_COOKIE = "hercules_session"
LOGIN_PATH = "/login"
LOGIN_CODE_TTL_S = 300.0
SESSION_TTL_S = 12 * 3600.0
MAX_SESSIONS = 64
MAX_BODY_BYTES = 2 * 1024 * 1024
_LOOPBACK_NAMES = frozenset({"localhost", "localhost."})
_WILDCARD_HOSTS = frozenset({"", "0.0.0.0", "::"})
_SAFE_METHODS = frozenset({"GET", "HEAD"})
_FOREIGN_SITES = frozenset({"cross-site", "same-site"})
_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}


def is_loopback_host(host: str) -> bool:
    """True for ``localhost`` and loopback IP literals (``127.0.0.0/8``, ``::1``)."""
    name = host.strip().lower().strip("[]")
    if name in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def split_host_header(value: str) -> str:
    """The host part of a ``Host`` header (``[::1]:7373`` → ``::1``)."""
    value = value.strip()
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else ""
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def _host_and_port(value: str, scheme: str) -> tuple[str, int] | None:
    """``Host`` header → (lower-case host, port), the scheme's default port when none is given; None when malformed."""
    try:
        parts = urlsplit(f"//{value.strip()}")
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return parts.hostname.lower().rstrip("."), port if port is not None else _DEFAULT_PORTS.get(scheme, 80)


@dataclass
class SessionBook:
    """One-time login codes and the session ids they were exchanged for (memory only).

    A session lasts ``SESSION_TTL_S``; at most ``MAX_SESSIONS`` are kept (the oldest goes first)."""

    codes: dict[str, float] = field(default_factory=dict)
    sessions: dict[str, float] = field(default_factory=dict)

    def new_code(self) -> str:
        now = time.monotonic()
        self.codes = {code: expiry for code, expiry in self.codes.items() if expiry > now}
        code = secrets.token_urlsafe(24)
        self.codes[code] = now + LOGIN_CODE_TTL_S
        return code

    def redeem(self, code: str) -> str | None:
        expiry = self.codes.pop(code, None)
        now = time.monotonic()
        if expiry is None or expiry < now:
            return None
        self.sessions = {known: until for known, until in self.sessions.items() if until > now}
        while len(self.sessions) >= MAX_SESSIONS:
            self.sessions.pop(min(self.sessions, key=self.sessions.__getitem__))
        session = secrets.token_urlsafe(32)
        self.sessions[session] = now + SESSION_TTL_S
        return session

    def valid(self, session: str) -> bool:
        # Bytes, not str: compare_digest refuses non-ASCII text, and a cookie header may carry any byte.
        given = session.encode("utf-8", "surrogateescape")
        now = time.monotonic()
        return any(until > now and hmac.compare_digest(given, known.encode()) for known, until in list(self.sessions.items()))


class GuardMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        sessions: SessionBook,
        allow_remote: bool = False,
        bound_host: str = "127.0.0.1",
        allowed_hosts: Iterable[str] = (),
        max_body_bytes: int = MAX_BODY_BYTES,
    ) -> None:
        self.app = app
        self.token = token.encode()
        self.sessions = sessions
        self.allow_remote = allow_remote
        self.bound_host = bound_host.strip().strip("[]").lower()
        self.allowed_hosts = frozenset(name.strip().lower().rstrip(".") for name in allowed_hosts if name.strip())
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await self._websocket(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        if not self._host_ok(headers.get("host", "")):
            await _deny(send, 403, "forbidden host")
            return
        origin = headers.get("origin")
        if origin is not None and not self._same_origin(origin, headers, scope):
            await _deny(send, 403, "forbidden origin")
            return
        if scope.get("path") == LOGIN_PATH:
            await self._login(scope, headers, send, wants_page=_wants_page(scope, headers))
            return
        bearer = self._bearer_ok(headers)
        if not bearer and not self._session_ok(headers):
            if _wants_page(scope, headers):
                await _deny_page(send, 401, sign_in_page())
                return
            await _deny(send, 401, "unauthorized", extra=[(b"www-authenticate", b'Bearer realm="hercules"')])
            return
        method = str(scope.get("method", "GET")).upper()
        if not bearer:  # the browser UI: refuse what another site or port made the browser send
            navigation = method in _SAFE_METHODS and headers.get("sec-fetch-mode", "").lower() == "navigate"
            if headers.get("sec-fetch-site", "").lower() in _FOREIGN_SITES and not navigation:
                await _deny(send, 403, "cross-site request refused")
                return
            if method not in _SAFE_METHODS and origin is None:
                await _deny(send, 403, "a request that changes something needs the Origin header")
                return
        if method in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        body = await self._read_body(receive, headers)
        if body is None:
            await _deny(send, 413, f"the request body is larger than {self.max_body_bytes // 1024} KiB")
            return
        if body and not _is_json(headers.get("content-type", "")):
            await _deny(send, 415, "the request body must be application/json")
            return
        await self.app(scope, _replay(body, receive), send)

    async def _websocket(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = _headers(scope)
        origin = headers.get("origin")
        allowed = self._host_ok(headers.get("host", ""))
        if allowed and origin is not None:
            browser = self._session_ok(headers) and headers.get("sec-fetch-site", "").lower() not in _FOREIGN_SITES
            allowed = self._same_origin(origin, headers, scope) and (self._bearer_ok(headers) or browser)
        elif allowed:
            allowed = self._bearer_ok(headers)  # not a browser: the token, never a cookie
        if not allowed:
            message = await receive()
            if message.get("type") == "websocket.connect":
                await send({"type": "websocket.close", "code": 1008})
            return
        await self.app(scope, receive, send)

    def _bearer_ok(self, headers: dict[str, str]) -> bool:
        scheme, _, credential = headers.get("authorization", "").partition(" ")
        return scheme.lower() == "bearer" and bool(credential.strip()) and hmac.compare_digest(credential.strip().encode("utf-8", "surrogateescape"), self.token)

    def _session_ok(self, headers: dict[str, str]) -> bool:
        session = _cookie(headers.get("cookie", ""), SESSION_COOKIE)
        return session is not None and session != "" and self.sessions.valid(session)

    def _host_ok(self, value: str) -> bool:
        host = split_host_header(value).lower().rstrip(".")
        if not host:
            return False
        if is_loopback_host(host):
            return True
        if not self.allow_remote:
            return False
        return (self.bound_host not in _WILDCARD_HOSTS and host == self.bound_host) or host in self.allowed_hosts

    def _scheme(self, headers: dict[str, str], scope: Scope) -> str:
        """The scheme the client used: https behind a TLS proxy (remote access only), else the connection's own."""
        if self.allow_remote and headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https":
            return "https"
        return "https" if scope.get("scheme") in ("https", "wss") else "http"

    def _same_origin(self, origin: str, headers: dict[str, str], scope: Scope) -> bool:
        """``Origin`` names exactly this daemon: the scheme in use and the host and port of ``Host``."""
        try:
            parts = urlsplit(origin.strip())
            port = parts.port
        except ValueError:
            return False
        scheme = self._scheme(headers, scope)
        if parts.scheme != scheme or not parts.hostname or parts.path not in ("", "/"):
            return False
        own = _host_and_port(headers.get("host", ""), scheme)
        return own is not None and own == (parts.hostname.lower().rstrip("."), port if port is not None else _DEFAULT_PORTS[scheme])

    async def _read_body(self, receive: Receive, headers: dict[str, str]) -> bytes | None:
        """The whole request body, or None when it is larger than allowed (by ``Content-Length`` or by what arrived)."""
        declared = headers.get("content-length", "").strip()
        if declared.isdigit() and int(declared) > self.max_body_bytes:
            return None
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body_bytes:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        return b"".join(chunks)

    async def _login(self, scope: Scope, headers: dict[str, str], send: Send, *, wants_page: bool = False) -> None:
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        codes = query.get("code") or [""]
        session = self.sessions.redeem(codes[0]) if codes[0] else None
        if session is None:
            if wants_page:
                await _deny_page(send, 401, sign_in_page(expired=True))
                return
            await _deny(send, 401, "invalid or used login code")
            return
        cookie = f"{SESSION_COOKIE}={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age={int(SESSION_TTL_S)}"
        if self._scheme(headers, scope) == "https":
            cookie += "; Secure"
        await send({"type": "http.response.start", "status": 303, "headers": [(b"location", b"/"), (b"set-cookie", cookie.encode()), (b"cache-control", b"no-store"), (b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})


def _headers(scope: Scope) -> dict[str, str]:
    return {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}


def _is_json(content_type: str) -> bool:
    media = content_type.split(";", 1)[0].strip().lower()
    return media == "application/json" or (media.startswith("application/") and media.endswith("+json"))


def _replay(body: bytes, receive: Receive) -> Receive:
    """A ``receive`` that hands the app the body already read, then defers to the real one (for disconnects)."""
    sent = False

    async def replay() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


def _cookie(header: str, name: str) -> str | None:
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


def _wants_page(scope: Scope, headers: dict[str, str]) -> bool:
    """A browser navigating to a UI page (not an API call): answer with a readable page."""
    path = str(scope.get("path", ""))
    return scope.get("method") in ("GET", "HEAD") and not path.startswith("/api/") and "text/html" in headers.get("accept", "")


async def _deny_page(send: Send, status: int, page: str) -> None:
    body = page.encode()
    headers = [
        (b"content-type", b"text/html; charset=utf-8"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
        (b"content-security-policy", PAGE_CSP.encode()),
        (b"x-content-type-options", b"nosniff"),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _deny(send: Send, status: int, message: str, extra: list[tuple[bytes, bytes]] | None = None) -> None:
    body = json.dumps({"error": message}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store"), *(extra or [])]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
