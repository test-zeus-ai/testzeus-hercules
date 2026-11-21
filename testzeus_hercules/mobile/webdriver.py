"""A device session over the W3C WebDriver protocol, as Appium and device cloud hubs speak it (plain HTTP, no client library).

:class:`WebDriverClient` creates the session, sends commands in it and ends it. Every command returns a
:class:`Reply` (``ok`` and ``value``, or ``error``) instead of raising, so the mobile loop decides what a failed
command means. Credentials (a device cloud's user name and access key) go in the HTTP Basic header only; they are
never part of a URL, a log line or an error text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import httpx

logger = logging.getLogger(__name__)

ClientFactory = Callable[..., httpx.AsyncClient]
_ERROR_CLIP = 500
# Android key codes for the keys a step may press.
KEYCODES = {"enter": 66, "back": 4, "home": 3, "tab": 61, "delete": 67, "search": 84, "menu": 82, "escape": 111, "space": 62}


@dataclass(frozen=True)
class Reply:
    ok: bool
    value: Any = None
    error: str = ""


class WebDriverError(RuntimeError):
    """The session could not be created; the message is safe to show."""


def element_id(value: Any) -> str | None:
    """The element reference of a W3C (``element-6066-...``) or legacy (``ELEMENT``) answer."""
    if isinstance(value, dict):
        for key, item in value.items():
            if "element" in key.lower() and isinstance(item, str):
                return item
    return None


def _json(response: httpx.Response) -> Any:
    try:
        return response.json() if response.content else None
    except ValueError:
        return None


def error_text(response: httpx.Response, body: Any) -> str:
    """The server's reason: W3C ``{"value": {"error", "message"}}``, a flat message, or the raw body (clipped)."""
    if isinstance(body, dict):
        value = body.get("value")
        if isinstance(value, dict) and value.get("message"):
            return str(value["message"]).strip().splitlines()[0][:_ERROR_CLIP]
        if isinstance(value, str) and value:
            return value[:_ERROR_CLIP]
        if body.get("message"):
            return str(body["message"])[:_ERROR_CLIP]
    text = (response.text or "").strip()
    return text[:_ERROR_CLIP] if text else f"empty answer (HTTP {response.status_code})"


def _pointer(*actions: dict[str, Any]) -> dict[str, Any]:
    return {"actions": [{"type": "pointer", "id": "finger1", "parameters": {"pointerType": "touch"}, "actions": list(actions)}]}


def _move(x: int, y: int, duration_ms: int = 0) -> dict[str, Any]:
    return {"type": "pointerMove", "duration": int(duration_ms), "x": int(x), "y": int(y)}


_DOWN = {"type": "pointerDown", "button": 0}
_UP = {"type": "pointerUp", "button": 0}


def _pause(ms: int) -> dict[str, Any]:
    return {"type": "pause", "duration": int(ms)}


class WebDriverClient:
    """One W3C session on ``hub_url``. ``client_factory`` makes the HTTP client (tests pass a mock transport)."""

    def __init__(
        self,
        hub_url: str,
        *,
        auth: tuple[str, str] | None = None,
        client_factory: ClientFactory = httpx.AsyncClient,
        command_timeout_s: float = 120.0,
        session_timeout_s: float = 300.0,
        trust_env: bool = True,
    ) -> None:
        self.hub_url = hub_url.rstrip("/")
        self._auth = auth
        self._factory = client_factory
        self._command_timeout_s = command_timeout_s
        self._session_timeout_s = session_timeout_s
        # A loopback server is reached directly; a remote one honours the environment's proxy settings.
        self._trust_env = trust_env
        self._client: httpx.AsyncClient | None = None
        self.session_id: str | None = None
        self.capabilities: dict[str, Any] = {}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._factory(timeout=self._command_timeout_s, follow_redirects=True, auth=self._auth, trust_env=self._trust_env)
        return self._client

    async def status(self) -> Reply:
        """The server's ``/status`` (needs no session)."""
        return await self._send("GET", f"{self.hub_url}/status", None)

    async def start(self, capabilities: Mapping[str, Any]) -> None:
        """Create the session; :class:`WebDriverError` with the server's reason when it refuses."""
        payload = {"capabilities": {"alwaysMatch": dict(capabilities), "firstMatch": [{}]}}
        try:
            response = await self._http().post(f"{self.hub_url}/session", json=payload, timeout=self._session_timeout_s)
        except httpx.HTTPError as exc:
            raise WebDriverError(f"the device server could not be reached ({type(exc).__name__})") from None
        body = _json(response)
        value = body.get("value") if isinstance(body, dict) else None
        session_id = (value or {}).get("sessionId") if isinstance(value, dict) else None
        session_id = session_id or (body.get("sessionId") if isinstance(body, dict) else None)
        if response.status_code >= 300 or not session_id:
            reason = error_text(response, body)
            if response.status_code == 401:
                reason = "the device server refused the credentials (HTTP 401)"
            raise WebDriverError(f"the device session could not start: {reason}")
        self.session_id = str(session_id)
        self.capabilities = dict((value or {}).get("capabilities") or {}) if isinstance(value, dict) else {}

    async def command(self, method: str, endpoint: str, body: Mapping[str, Any] | None = None) -> Reply:
        """One command in the session (``endpoint`` after ``/session/<id>``)."""
        if self.session_id is None:
            return Reply(False, error="no device session")
        return await self._send(method, f"{self.hub_url}/session/{self.session_id}{endpoint}", body)

    async def _send(self, method: str, url: str, body: Mapping[str, Any] | None) -> Reply:
        try:
            client = self._http()
            if method == "GET":
                response = await client.get(url)
            elif method == "DELETE":
                response = await client.delete(url)
            else:
                response = await client.post(url, json=dict(body or {}))
        except httpx.HTTPError as exc:
            return Reply(False, error=f"{type(exc).__name__} talking to the device server")
        parsed = _json(response)
        if response.status_code == 200:
            return Reply(True, parsed.get("value") if isinstance(parsed, dict) else None)
        return Reply(False, error=error_text(response, parsed))

    async def stop(self) -> None:
        """End the session (never raises) and close the HTTP client."""
        if self.session_id is not None:
            reply = await self.command("DELETE", "")
            if not reply.ok:
                logger.warning("the device session did not end cleanly: %s", reply.error)
            self.session_id = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # a closed client is gone anyway
                pass
            self._client = None

    # ------------------------------------------------------------------ commands

    async def page_source(self) -> Reply:
        return await self.command("GET", "/source")

    async def screenshot(self) -> Reply:
        """The screen as base64 PNG."""
        return await self.command("GET", "/screenshot")

    async def window_size(self) -> tuple[int, int] | None:
        reply = await self.command("GET", "/window/rect")
        if reply.ok and isinstance(reply.value, dict) and "width" in reply.value:
            return int(reply.value["width"]), int(reply.value["height"])
        return None

    async def find(self, using: str, value: str) -> str | None:
        """The first element the locator finds, or None."""
        reply = await self.command("POST", "/element", {"using": using, "value": value})
        return element_id(reply.value) if reply.ok else None

    async def click(self, element: str) -> Reply:
        return await self.command("POST", f"/element/{element}/click", {})

    async def clear(self, element: str) -> Reply:
        return await self.command("POST", f"/element/{element}/clear", {})

    async def send_keys(self, element: str, text: str) -> Reply:
        return await self.command("POST", f"/element/{element}/value", {"text": text, "value": list(text)})

    async def active_element(self) -> str | None:
        reply = await self.command("GET", "/element/active")
        return element_id(reply.value) if reply.ok else None

    async def tap(self, x: int, y: int) -> Reply:
        return await self.command("POST", "/actions", _pointer(_move(x, y), _DOWN, _pause(50), _UP))

    async def long_press(self, x: int, y: int, duration_ms: int = 1000) -> Reply:
        return await self.command("POST", "/actions", _pointer(_move(x, y), _DOWN, _pause(duration_ms), _UP))

    async def swipe(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int = 400) -> Reply:
        return await self.command("POST", "/actions", _pointer(_move(*start), _DOWN, _pause(100), _move(*end, duration_ms=duration_ms), _UP))

    async def back(self) -> Reply:
        return await self.command("POST", "/back", {})

    async def press_keycode(self, code: int) -> Reply:
        return await self.command("POST", "/appium/device/press_keycode", {"keycode": code})

    async def execute(self, script: str, args: Mapping[str, Any] | None = None) -> Reply:
        return await self.command("POST", "/execute/sync", {"script": script, "args": [dict(args or {})]})

    async def hide_keyboard(self) -> Reply:
        return await self.command("POST", "/appium/device/hide_keyboard", {})
