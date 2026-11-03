"""Shared HTTP hardening for the network-facing Hercules services (MCP server, dashboard).

Policy:
  * Services bind loopback by default.
  * Binding a non-loopback address is refused unless a bearer token is configured
    (``MCP_AUTH_TOKEN`` / ``DASHBOARD_AUTH_TOKEN``).
  * When a token is configured it is enforced on every request, loopback or not.
  * A ``Host`` header allowlist blocks DNS-rebinding attacks against loopback binds.

Pure ASGI middleware (no ``BaseHTTPMiddleware``) so streaming responses (SSE) are untouched.
"""

from __future__ import annotations

import base64
import hmac
import ipaddress
from collections.abc import Iterable

MIN_REMOTE_TOKEN_LENGTH = 16
LOOPBACK_NAMES = ("localhost", "127.0.0.1", "::1")
_WILDCARD_HOSTS = ("0.0.0.0", "::", "")  # nosec B104 - recognised only to classify a bind address


class InsecureBindError(RuntimeError):
    """Raised when a service would be exposed beyond loopback without authentication."""


def is_loopback_host(host: str) -> bool:
    """True for ``localhost`` and loopback IP literals (127.0.0.0/8, ::1)."""
    value = (host or "").strip().strip("[]").lower()
    if value == "localhost" or value.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def is_wildcard_host(host: str) -> bool:
    return (host or "").strip().strip("[]") in _WILDCARD_HOSTS


def normalize_token(raw: str | None) -> str | None:
    """Strip whitespace; empty means "no token"."""
    token = (raw or "").strip()
    return token or None


def enforce_bind_policy(service: str, host: str, token: str | None, token_env: str) -> None:
    """Refuse to expose ``service`` on a non-loopback host without a sufficiently long bearer token."""
    if is_loopback_host(host):
        return
    if not token:
        raise InsecureBindError(
            f"Refusing to bind {service} to non-loopback host {host!r} without authentication. Set {token_env} to a random secret (>= {MIN_REMOTE_TOKEN_LENGTH} chars), or bind 127.0.0.1."
        )
    if len(token) < MIN_REMOTE_TOKEN_LENGTH:
        raise InsecureBindError(f"{token_env} is too short for a non-loopback bind of {service} (need >= {MIN_REMOTE_TOKEN_LENGTH} chars).")


def parse_csv(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _host_without_port(host_header: str) -> str:
    value = (host_header or "").strip().lower()
    if value.startswith("["):  # [::1]:8000
        end = value.find("]")
        return value[1:end] if end != -1 else value
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def build_host_allowlist(bind_host: str, extra: Iterable[str] = ()) -> set[str] | None:
    """Allowed ``Host`` values (port-less, lowercase), or None when the check should be skipped.

    Loopback bind: loopback names only (plus ``extra``). Specific non-loopback bind: that
    host + loopback names + ``extra``. Wildcard bind with no ``extra``: None (the bearer
    token, which is mandatory for such binds, is the gate; a LAN client may use any name).
    """
    extras = {_host_without_port(item) for item in extra if item}
    if is_wildcard_host(bind_host):
        if not extras:
            return None
        return set(LOOPBACK_NAMES) | extras
    allowed = set(LOOPBACK_NAMES) | extras
    allowed.add(_host_without_port(bind_host.strip("[]")))
    return allowed


async def _send_text(send, status: int, text: str, headers: list[tuple[bytes, bytes]] | None = None) -> None:
    body = text.encode("utf-8")
    base = [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": base + (headers or [])})
    await send({"type": "http.response.body", "body": body})


def _header(scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return ""


class BearerTokenMiddleware:
    """Require ``Authorization: Bearer <token>`` (or, with ``allow_basic``, HTTP Basic with the token as password).

    Basic auth exists so a browser can open the dashboard (native credentials prompt);
    the username is ignored. Comparison is constant-time.
    """

    def __init__(self, app, token: str, allow_basic: bool = False, realm: str = "hercules") -> None:
        if not token:
            raise ValueError("BearerTokenMiddleware requires a non-empty token")
        self.app = app
        self._token = token.encode("utf-8")
        self._allow_basic = allow_basic
        self._realm = realm

    def _authorized(self, authorization: str) -> bool:
        scheme, _, credential = authorization.partition(" ")
        scheme = scheme.lower()
        credential = credential.strip()
        if scheme == "bearer":
            presented = credential.encode("utf-8")
        elif scheme == "basic" and self._allow_basic:
            try:
                decoded = base64.b64decode(credential, validate=True).decode("utf-8")
            except Exception:
                return False
            presented = decoded.partition(":")[2].encode("utf-8")
        else:
            return False
        return hmac.compare_digest(presented, self._token)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if self._authorized(_header(scope, b"authorization")):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        challenges = [(b"www-authenticate", b"Bearer")]
        if self._allow_basic:
            challenges.append((b"www-authenticate", f'Basic realm="{self._realm}"'.encode()))
        await _send_text(send, 401, "Unauthorized", challenges)


class HostAllowlistMiddleware:
    """Reject requests whose ``Host`` header is not in the allowlist (DNS-rebinding defence)."""

    def __init__(self, app, allowed_hosts: set[str]) -> None:
        self.app = app
        self._allowed = {item.lower() for item in allowed_hosts}

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if _host_without_port(_header(scope, b"host")) in self._allowed:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await _send_text(send, 421, "Invalid Host header")
