"""A fake Appium server (and device cloud REST API) on a free loopback port, for the mobile tests.

It speaks the W3C WebDriver endpoints Hercules uses and simulates a tiny Android settings app:

* ``home``: a list row "Network & internet" (a clickable container whose text is a child, as Android draws
  settings rows) and a search field;
* ``network``: "Internet", "SIMs" and a "Navigate up" button that goes back home.

Typed text shows in the search field (and is kept in ``typed``). It also answers BrowserStack's and Sauce Labs'
app upload and session status calls. ``auth`` (user, key) makes every call need those Basic credentials.
Everything a client sent is recorded in ``requests``.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

PNG_SIZE = (108, 240)


def _png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", PNG_SIZE, (40, 90, 200)).save(out, format="PNG")
    return out.getvalue()


def _node(cls: str, bounds: tuple[int, int, int, int], *, text: str = "", desc: str = "", rid: str = "", clickable: bool = False, children: str = "", password: bool = False) -> str:
    x1, y1, x2, y2 = bounds
    attrs = (
        f'class="{cls}" package="com.example.settings" text="{text}" content-desc="{desc}" resource-id="{rid}" clickable="{str(clickable).lower()}" '
        f'enabled="true" displayed="true" checkable="false" checked="false" focused="false" password="{str(password).lower()}" bounds="[{x1},{y1}][{x2},{y2}]"'
    )
    return f"<{cls} {attrs}>{children}</{cls}>" if children else f"<{cls} {attrs} />"


@dataclass
class FakeDevice:
    screen: str = "home"
    typed: str = ""
    auth: tuple[str, str] | None = None
    # A screen that changes nothing when tapped (to test "no effect" handling).
    frozen: bool = False
    refuse_session: str | None = None
    # The search field cannot be located again (it moved to a new screen) and is focused only after
    # this many requests for the focused element (a screen still opening).
    search_unlocatable: bool = False
    focus_after: int = 0
    requests: list[tuple[str, str, Any, str | None]] = field(default_factory=list)
    sessions: list[dict[str, Any]] = field(default_factory=list)
    statuses: list[tuple[str, Any]] = field(default_factory=list)
    uploads: list[tuple[str, int]] = field(default_factory=list)
    ended: int = 0

    def source(self) -> str:
        if self.screen == "home":
            row = _node("android.widget.LinearLayout", (0, 300, 1080, 500), clickable=True, children=_node("android.widget.TextView", (180, 340, 700, 400), text="Network &amp; internet"))
            search = _node("android.widget.EditText", (0, 120, 1080, 260), text=self.typed, rid="com.example.settings:id/search", clickable=True)
            body = row + search
        else:
            body = (
                _node("android.widget.ImageButton", (0, 120, 140, 260), desc="Navigate up", clickable=True)
                + _node("android.widget.TextView", (180, 340, 700, 400), text="Internet")
                + _node("android.widget.TextView", (180, 540, 700, 600), text="SIMs")
            )
        root = _node("android.widget.FrameLayout", (0, 0, 1080, 2400), children=body)
        return f"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">{root}</hierarchy>"

    def find(self, using: str, value: str) -> str | None:
        if self.screen == "home":
            known = {("xpath", "//*[@text='Network & internet']"): "row-text"}
            if not self.search_unlocatable:
                known[("id", "com.example.settings:id/search")] = "search"
        else:
            known = {("accessibility id", "Navigate up"): "up", ("xpath", "//*[@text='Internet']"): "internet"}
        return known.get((using, value))

    def at(self, x: int, y: int) -> str | None:
        if self.screen == "home":
            if 300 <= y <= 500:
                return "row"
            if 120 <= y <= 260:
                return "search"
        elif x <= 140 and 120 <= y <= 260:
            return "up"
        return None

    def press(self, element: str | None) -> None:
        if self.frozen or element is None:
            return
        if element in ("row", "row-text"):
            self.screen = "network"
        elif element == "up":
            self.screen = "home"


def _handler(device: FakeDevice) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return None

        def _reply(self, status: int, body: Any) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if "multipart/form-data" in (self.headers.get("Content-Type") or ""):
                return {"multipart": len(raw)}
            try:
                return json.loads(raw) if raw else None
            except ValueError:
                return None

        def _authorised(self) -> bool:
            if device.auth is None:
                return True
            expected = "Basic " + base64.b64encode(f"{device.auth[0]}:{device.auth[1]}".encode()).decode()
            return self.headers.get("Authorization") == expected

        def _handle(self, method: str) -> None:
            body = self._body()
            device.requests.append((method, self.path, body, self.headers.get("Authorization")))
            if not self._authorised():
                self._reply(401, {"value": {"error": "unauthorized", "message": "Authorization required"}})
                return
            path = self.path.split("?")[0]
            parts = [part for part in path.split("/") if part]
            if parts[:1] == ["wd"] and parts[1:2] == ["hub"]:
                parts = parts[2:]
            if method == "GET" and parts == ["status"]:
                self._reply(200, {"value": {"ready": True, "build": {"version": "fake-2.0"}}})
            elif method == "POST" and parts == ["session"]:
                if device.refuse_session:
                    self._reply(500, {"value": {"error": "session not created", "message": device.refuse_session}})
                    return
                device.sessions.append(body["capabilities"]["alwaysMatch"])
                self._reply(200, {"value": {"sessionId": f"s{len(device.sessions)}", "capabilities": {"platformName": "Android"}}})
            elif method == "POST" and parts in (["upload"], ["v1", "storage", "upload"]):
                device.uploads.append((path, int((body or {}).get("multipart", 0))))
                self._reply(200, {"app_url": "bs://abc123"} if parts == ["upload"] else {"item": {"id": "f00d"}})
            elif method == "PUT" and (parts[:1] == ["sessions"] or parts[:2] == ["rest", "v1"]):
                device.statuses.append((path, body))
                self._reply(200, {})
            elif parts[:1] == ["session"] and len(parts) >= 2:
                self._session(method, parts[2:], body)
            else:
                self._reply(404, {"value": {"error": "unknown command", "message": f"{method} {path}"}})

        def _session(self, method: str, rest: list[str], body: Any) -> None:
            ok = lambda value=None: self._reply(200, {"value": value})  # noqa: E731
            if method == "DELETE" and not rest:
                device.ended += 1
                ok()
            elif rest == ["source"]:
                ok(device.source())
            elif rest == ["screenshot"]:
                ok(base64.b64encode(_png()).decode())
            elif rest == ["window", "rect"]:
                ok({"x": 0, "y": 0, "width": 1080, "height": 2400})
            elif rest == ["element"]:
                found = device.find(body.get("using", ""), body.get("value", ""))
                if found is None:
                    self._reply(404, {"value": {"error": "no such element", "message": "An element could not be located"}})
                else:
                    ok({"element-6066-11e4-a52e-4f735466cecf": found})
            elif rest == ["element", "active"]:
                if device.focus_after > 0:
                    device.focus_after -= 1
                    self._reply(404, {"value": {"error": "no such element", "message": "No element is focused"}})
                else:
                    ok({"element-6066-11e4-a52e-4f735466cecf": "search"})
            elif len(rest) == 3 and rest[0] == "element":
                element, command = rest[1], rest[2]
                if command == "click":
                    device.press(element)
                elif command == "clear" and element == "search":
                    device.typed = ""
                elif command == "value" and element == "search":
                    device.typed += body.get("text", "")
                ok()
            elif rest == ["actions"]:
                moves = [action for action in body["actions"][0]["actions"] if action["type"] == "pointerMove"]
                if len(moves) == 1:
                    device.press(device.at(moves[0]["x"], moves[0]["y"]))
                ok()
            elif rest == ["back"]:
                device.screen = "home"
                ok()
            elif rest[:2] == ["appium", "device"]:
                ok()
            else:
                self._reply(404, {"value": {"error": "unknown command", "message": "/".join(rest)}})

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_PUT(self) -> None:
            self._handle("PUT")

        def do_DELETE(self) -> None:
            self._handle("DELETE")

    return Handler


@contextlib.contextmanager
def serve_fake_appium(device: FakeDevice | None = None) -> Iterator[tuple[str, FakeDevice]]:
    """Run the fake on 127.0.0.1 (a free port); yields its base URL and the device."""
    device = device or FakeDevice()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(device))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", device
    finally:
        server.shutdown()
        server.server_close()
