"""Where explorers may go and what they may never do, enforced on the chosen move and on every request.

Two checks, both in code:

- **Moves:** a move whose element or text names a forbidden action (pay, delete an account, send a message, invite,
  sign out, ...) is skipped and logged, or waits for a person with ``ask_before_forbidden``.
- **Requests:** every request of an explorer's browser goes through :meth:`ScopeGuard.route`: going to a host outside
  ``allow_hosts`` (a page load, or any request that changes something), a path in ``deny_paths`` or a ``DELETE`` is
  aborted and logged, and requests that change something (anything but GET and HEAD) are spaced to at most
  ``rate_per_s``. A page's background writes to another host (analytics, push, ads) get an empty JSON answer
  (``{}``) instead, so nothing leaves and the page carries on. A page's own reads from other hosts (images, styles, scripts and data from a CDN) pass, as they do
  for every visitor; blocking them would explore a broken copy of the app.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from testzeus_hercules.explore.models import Move, Scope

__all__ = ["DEFAULT_FORBID", "ScopeGuard", "production_host"]

DEFAULT_FORBID: tuple[str, ...] = (
    "pay",
    "purchase",
    "buy now",
    "delete account",
    "close account",
    "deactivate",
    "send",
    "email",
    "invite",
    "share",
    "transfer",
    "withdraw",
    "unsubscribe",
    "sign out",
    "log out",
    "logout",
    "change role",
    "make admin",
    "reset password",
)
_TEST_LABELS = ("localhost", "127.0.0.1", "[::1]", "staging", "stage", "test", "qa", "dev", "sandbox", "preview", "demo", "local", "uat", "internal")


def production_host(host: str) -> bool:
    """True when nothing in the host name marks it as a test environment (a loopback or private address counts as one)."""
    lowered = re.sub(r":\d{1,5}$", "", host.lower()).strip("[]")
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        address = None
    if address is not None:
        return not (address.is_loopback or address.is_private or address.is_link_local)
    if lowered.endswith((".localhost", ".test", ".local", ".internal")) or lowered == "localhost":
        return False
    return not any(re.search(rf"(^|[.-]){re.escape(label)}([.-]|\d|$)", lowered) for label in _TEST_LABELS)


@dataclass
class Blocked:
    at: float
    what: str
    reason: str


@dataclass
class ScopeGuard:
    scope: Scope
    base_url: str
    blocked: list[Blocked] = field(default_factory=list)
    on_block: Callable[[str, str], None] | None = None
    _last_write: float = 0.0
    _lock: asyncio.Lock | None = None

    def __post_init__(self) -> None:
        host = _host(self.base_url)
        self.hosts = {item.lower() for item in self.scope.allow_hosts} or ({host} if host else set())
        allowed = {term.lower() for term in self.scope.allow}
        self.forbid = tuple(term for term in (*DEFAULT_FORBID, *(term.lower() for term in self.scope.forbid)) if term not in allowed)

    # --------------------------------------------------------------- moves

    def forbidden_move(self, move: Move) -> str | None:
        """The forbidden word naming the control a move clicks or submits, or None (typing into a field named "Email" is fine)."""
        names = (move.clicks or "").lower()
        for term in self.forbid:
            if re.search(rf"\b{re.escape(term)}\b", names):
                return term
        return None

    def note(self, what: str, reason: str) -> None:
        self.blocked.append(Blocked(time.time(), what, reason))
        if self.on_block is not None:
            self.on_block(what, reason)

    # --------------------------------------------------------------- requests

    def request_problem(self, url: str, method: str, *, navigation: bool = True) -> str | None:
        """Why a request may not leave, or None. ``navigation`` is False for a page's own subresource requests."""
        parts = urlsplit(url)
        if parts.scheme in ("data", "blob", "about"):
            return None  # these never leave the browser
        if parts.scheme not in ("http", "https", "ws", "wss"):
            return f"{parts.scheme}: requests are outside the session's scope"
        host = _host(url)
        if host not in self.hosts and not navigation and method.upper() in ("GET", "HEAD"):
            return None  # the page reads an asset from its CDN or another service, as it does for every visitor
        if host not in self.hosts:
            return f"{host} is outside the session's scope ({', '.join(sorted(self.hosts))})"
        path = parts.path or "/"
        lowered = path.lower()
        for denied in self.scope.deny_paths:
            prefix = denied.lower().rstrip("/")
            if lowered == prefix or lowered.startswith(prefix + "/") or lowered == denied.lower():
                return f"{denied} is in deny_paths"
        if method.upper() == "DELETE" or "_method=delete" in parts.query.lower():
            return "DELETE requests are forbidden while exploring"
        if re.search(r"(^|/)(log-?out|sign-?out|logoff)([/.?]|$)", lowered):
            return "the request would sign the explorer out"
        for term in self.forbid:
            slug = term.replace(" ", "[-_/]?")
            if " " in term or len(term) > 4:
                if re.search(rf"(^|/|[-_]){slug}([-_/.]|$)", lowered) and method.upper() not in ("GET", "HEAD"):
                    return f"the request's path names a forbidden action ({term})"
        return None

    async def install(self, context: Any) -> None:
        """Route every request of ``context`` through the guard (a test fake without ``route`` is left alone)."""
        route = getattr(context, "route", None)
        if not callable(route):
            return
        self._lock = asyncio.Lock()
        await route("**/*", self._handle)

    async def _handle(self, route: Any, request: Any) -> None:
        method = str(getattr(request, "method", "GET"))
        headers = getattr(request, "headers", None) or {}
        override = str(headers.get("x-http-method-override", "") or headers.get("x-http-method", "")) if hasattr(headers, "get") else ""
        if override:
            method = override
        is_navigation = getattr(request, "is_navigation_request", None)
        navigation = bool(is_navigation()) if callable(is_navigation) else True
        problem = self.request_problem(str(request.url), method, navigation=navigation)
        if problem is not None:
            self.note(f"{method} {_short(str(request.url))}", problem)
            if not navigation and _host(str(request.url)) not in self.hosts:
                # A page's background call to another host (analytics, push, ads) gets an empty JSON answer instead of
                # a failure: nothing leaves, and the page's own code neither throws "Failed to fetch" nor fails to parse
                # an empty body, so the findings hold the app's bugs, not the guard's.
                await route.fulfill(status=200, content_type="application/json", body="{}")
                return
            await route.abort("blockedbyclient")
            return
        if method.upper() not in ("GET", "HEAD") and self._lock is not None:
            async with self._lock:
                gap = 1.0 / self.scope.rate_per_s - (time.monotonic() - self._last_write)
                if gap > 0:
                    await asyncio.sleep(gap)
                self._last_write = time.monotonic()
        await route.continue_()


def _host(url: str) -> str:
    """The URL's host and port, lower case, without any user name or password."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{parts.port}" if parts.port else host


def _short(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc}{parts.path}"[:200]


def check_scope_hosts(urls: Sequence[str], guard: ScopeGuard) -> list[str]:
    """The URLs (from network.jsonl) that left the scope: the release gate's 'no request outside allow_hosts'."""
    return [url for url in urls if urlsplit(url).netloc and _host(url) not in guard.hosts]
