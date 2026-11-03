"""Offline tests for the real reachability probe mcp_server._check_app_url.

Existing mcp-server tests patch _check_app_url; these exercise the actual
probe against a local threading HTTPServer (no patching):
  - 200 and 404 answers count as "up" (status_code < 500 logic);
  - a server rejecting HEAD but answering GET still counts as up (fallback);
  - non-http(s) schemes return None per the current contract;
  - a refused connection returns False;
  - redirects are never followed (the probe stays inert).

Deliberately asserts only public truthiness — not TLS/timeout internals.
"""

import asyncio
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator, Optional

import testzeus_hercules.mcp_server as mcp_server


def _handler_for(get_status: int, head_status: Optional[int] = None) -> type:
    """Build a handler answering GET with get_status and HEAD with head_status (defaults to get_status)."""

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep test output quiet
            pass

        def _respond(self, status: int) -> None:
            body = b"probe-response"
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._respond(get_status)

        def do_HEAD(self) -> None:
            self._respond(get_status if head_status is None else head_status)

    return _Handler


@contextmanager
def _http_server(handler: type) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/whatever"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _probe(url: str):
    return asyncio.run(mcp_server._check_app_url(url))


def test_probe_200_is_truthy() -> None:
    with _http_server(_handler_for(200)) as url:
        assert _probe(url)


def test_probe_404_counts_as_up() -> None:
    # 4xx means the service is up but the route rejects GET; the probe only
    # treats 5xx (or connection errors) as "down".
    with _http_server(_handler_for(404)) as url:
        assert _probe(url)


def test_probe_falls_back_from_head_to_get() -> None:
    # HEAD returns 500 (not < 500, so not "up"), GET returns 200 → probe True.
    with _http_server(_handler_for(get_status=200, head_status=500)) as url:
        assert _probe(url)


def test_probe_non_http_scheme_returns_none() -> None:
    # Current contract: scheme outside http(s) is "not checkable" → None.
    assert _probe("ftp://example.test/x") is None


def test_probe_empty_url_returns_none() -> None:
    assert _probe("") is None


def test_probe_connection_refused_is_false() -> None:
    # Reserve a free port, then release it: nothing is listening there.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        refused_port = sock.getsockname()[1]
    assert _probe(f"http://127.0.0.1:{refused_port}/") is False


def test_probe_does_not_follow_redirects() -> None:
    hits: list[str] = []

    class _Target(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self) -> None:
            hits.append("get")
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_HEAD = do_GET

    with _http_server(_Target) as target_url:

        class _Redirector(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self) -> None:
                self.send_response(302)
                self.send_header("Location", target_url)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_HEAD = do_GET

        with _http_server(_Redirector) as redirect_url:
            assert _probe(redirect_url) is True  # the 302 itself means "up"
    assert hits == []  # ...and the redirect target was never contacted


def test_probe_redirect_to_failing_target_is_not_followed_into_false() -> None:
    # A 302 pointing at a dead port still reports the app (the redirector) as up.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead_port = sock.getsockname()[1]

    class _Redirector(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{dead_port}/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_HEAD = do_GET

    with _http_server(_Redirector) as url:
        assert _probe(url) is True


def test_probe_hostless_or_malformed_url_returns_none() -> None:
    assert _probe("http:///no-host") is None
    assert _probe("http://[::1") is None
    assert _probe("file:///etc/passwd") is None
