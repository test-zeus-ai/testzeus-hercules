"""Offline tests for the probe() helper in .hercules/dev scripts.

Loads .hercules/run_online_verification.py via importlib (it is guarded by
`if __name__ == "__main__"`, so importing it never boots services) and checks
its URL liveness semantics: any HTTP answer — including 4xx — counts as "up",
while a refused connection counts as "down".
"""

import importlib.util
import pathlib
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEV_SCRIPT = REPO_ROOT / ".hercules" / "run_online_verification.py"


def _load_dev_script():
    spec = importlib.util.spec_from_file_location("hercules_dev_run_online_verification", DEV_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StaticHandler(BaseHTTPRequestHandler):
    status = 200

    def log_message(self, *args):  # keep test output quiet
        pass

    def do_GET(self) -> None:
        body = b"ok"
        self.send_response(self.status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def _http_server(status: int) -> Iterator[str]:
    handler = type("_Handler", (_StaticHandler,), {"status": status})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/docs"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _refused_url() -> str:
    # Reserve a free port, then release it: nothing is listening there.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/docs"


def test_dev_probe_200_is_true() -> None:
    probe = _load_dev_script().probe
    with _http_server(200) as url:
        assert probe(url, timeout=5.0) is True


def test_dev_probe_404_is_true() -> None:
    # HTTPError branch: a 4xx answer means the service is up even though the
    # route rejects the request — this is what makes ensure_app_up() work.
    probe = _load_dev_script().probe
    with _http_server(404) as url:
        assert probe(url, timeout=5.0) is True


def test_dev_probe_refused_connection_is_false() -> None:
    probe = _load_dev_script().probe
    assert probe(_refused_url(), timeout=5.0) is False


def test_dev_probe_bad_url_is_false() -> None:
    # Unparseable/unsupported URL → urllib raises URLError (not HTTPError) → False.
    probe = _load_dev_script().probe
    assert probe("not-a-url", timeout=1.0) is False
