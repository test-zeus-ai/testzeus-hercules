"""Serve the Demo Shop bench app (``bench/apps``) over HTTP on localhost.

Usage::

    python bench/serve.py            # a free port, printed on start
    python bench/serve.py --port 8000

Standard library only; request logging is off unless ``--verbose`` is given.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import socket
import sys
import threading
from pathlib import Path
from typing import Iterator

APP_DIR = Path(__file__).resolve().parent / "apps"
HOST = "127.0.0.1"


class _Handler(http.server.SimpleHTTPRequestHandler):
    verbose = False

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - signature from the base class
        if self.verbose:
            super().log_message(format, *args)

    def end_headers(self) -> None:
        # Always serve fresh files so app edits show up without a hard reload.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


def make_server(port: int = 0, *, directory: Path = APP_DIR, verbose: bool = False) -> http.server.ThreadingHTTPServer:
    handler_class = type("DemoShopHandler", (_Handler,), {"verbose": verbose})
    handler = functools.partial(handler_class, directory=str(directory))
    return http.server.ThreadingHTTPServer((HOST, port or free_port()), handler)


@contextlib.contextmanager
def serving(port: int = 0, *, directory: Path = APP_DIR) -> Iterator[str]:
    """Serve the app in a background thread; yields the base URL and shuts down on exit."""
    server = make_server(port, directory=directory)
    thread = threading.Thread(target=server.serve_forever, name="demo-shop-server", daemon=True)
    thread.start()
    try:
        yield f"http://{HOST}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Demo Shop bench app.")
    parser.add_argument("--port", type=int, default=0, help="port to listen on (default: a free port)")
    parser.add_argument("--verbose", action="store_true", help="log every request")
    args = parser.parse_args(argv)
    server = make_server(args.port, verbose=args.verbose)
    print(f"Demo Shop: http://{HOST}:{server.server_address[1]}/  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
