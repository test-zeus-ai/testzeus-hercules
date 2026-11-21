"""The image's entry point (secrets files, the daemon token) and health check, run outside Docker."""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"
HEALTHCHECK = ROOT / "docker" / "healthcheck.py"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")


def entrypoint(tmp_path: Path, secrets: dict[str, str], *args: str, env: dict[str, str] | None = None) -> tuple[dict[str, str], str]:
    """Run the entry point with a fake ``hercules`` that prints its arguments and environment as JSON."""
    folder = tmp_path / "secrets"
    folder.mkdir(exist_ok=True)
    for name, text in secrets.items():
        (folder / name).write_bytes(text.encode())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "hercules"
    fake.write_text(f"#!{sys.executable}\nimport json, os, sys\nprint(json.dumps({{'args': sys.argv[1:], 'env': dict(os.environ)}}))\n")
    fake.chmod(0o755)
    python = bin_dir / "python"
    if not python.exists():
        python.symlink_to(sys.executable)
    base = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "HERCULES_SECRETS_DIR": str(folder), **(env or {})}
    done = subprocess.run(["sh", str(ENTRYPOINT), *args], env=base, capture_output=True, text=True, timeout=30, check=True)
    printed = json.loads(done.stdout)
    assert printed["args"] == list(args)
    return printed["env"], done.stderr


def test_secrets_files_are_read_with_quotes_and_windows_line_ends(tmp_path: Path) -> None:
    keys = "OPENROUTER_API_KEY=\"sk-1\"\r\nexport CEREBRAS_API_KEY='ck-2'\r\n# a comment\r\n\r\nnot a line\r\nB=x=y\r\n9BAD=1\r\nBAD-NAME=1\r\nLAST=z"
    env, _ = entrypoint(tmp_path, {"keys.env": keys, "ignored.txt": "OTHER=1\n"}, "run", "case.json")
    assert env["OPENROUTER_API_KEY"] == "sk-1" and env["CEREBRAS_API_KEY"] == "ck-2" and env["B"] == "x=y" and env["LAST"] == "z"
    assert "OTHER" not in env and "9BAD" not in env and "BAD-NAME" not in env
    assert "HERCULES_DAEMON_TOKEN" not in env  # only `serve` needs one


def test_names_that_change_how_programs_run_are_refused(tmp_path: Path) -> None:
    env, stderr = entrypoint(tmp_path, {"keys.env": "PATH=/evil\nLD_PRELOAD=/evil.so\nPYTHONPATH=/evil\nHERCULES_HOME=/evil\nhttps_proxy=http://evil\nKEY=1\n"}, "doctor")
    assert env["PATH"].startswith(str(tmp_path / "bin")) and env["KEY"] == "1"
    assert not {"LD_PRELOAD", "PYTHONPATH", "HERCULES_HOME", "https_proxy"} & set(env)
    assert "ignoring LD_PRELOAD" in stderr and "/evil" not in stderr


def test_serve_gets_a_strong_token_unless_one_is_given(tmp_path: Path) -> None:
    env, _ = entrypoint(tmp_path, {}, "serve", "--allow-remote")
    assert len(env["HERCULES_DAEMON_TOKEN"]) >= 60
    again, _ = entrypoint(tmp_path, {}, "serve")
    assert again["HERCULES_DAEMON_TOKEN"] != env["HERCULES_DAEMON_TOKEN"]
    given, _ = entrypoint(tmp_path, {"token.env": "HERCULES_DAEMON_TOKEN=from-a-secret-file-0123456789\n"}, "serve")
    assert given["HERCULES_DAEMON_TOKEN"] == "from-a-secret-file-0123456789"


class _Recorder(http.server.BaseHTTPRequestHandler):
    status = 200
    seen: list[tuple[str, str | None]] = []

    def do_GET(self) -> None:  # noqa: N802 - the http.server API
        type(self).seen.append((self.path, self.headers.get("Authorization")))
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return None


def _serve(handler: type[http.server.BaseHTTPRequestHandler]) -> Iterator[int]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def daemon() -> Iterator[type[_Recorder]]:
    handler = type("Daemon", (_Recorder,), {"seen": [], "status": 200})
    for port in _serve(handler):
        handler.port = port  # type: ignore[attr-defined]
        yield handler


@pytest.fixture
def proxy() -> Iterator[type[_Recorder]]:
    handler = type("Proxy", (_Recorder,), {"seen": [], "status": 502})
    for port in _serve(handler):
        handler.port = port  # type: ignore[attr-defined]
        yield handler


def free_port() -> int:
    """A port nothing listens on (bound, then closed again)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def healthcheck(home: Path, **env: str) -> int:
    base = {"PATH": os.environ.get("PATH", ""), "HERCULES_HOME": str(home), **env}
    return subprocess.run([sys.executable, str(HEALTHCHECK)], env=base, capture_output=True, timeout=30).returncode


def test_the_health_check_asks_the_daemon_directly_never_through_a_proxy(tmp_path: Path, daemon: type[_Recorder], proxy: type[_Recorder]) -> None:
    (tmp_path / "daemon.json").write_text(json.dumps({"port": daemon.port, "token": "tok-7f3a"}))  # type: ignore[attr-defined]
    proxied = f"http://127.0.0.1:{proxy.port}"  # type: ignore[attr-defined]
    assert healthcheck(tmp_path, http_proxy=proxied, HTTP_PROXY=proxied, NO_PROXY="", no_proxy="") == 0
    assert daemon.seen == [("/api/health", "Bearer tok-7f3a")] and proxy.seen == []


def test_the_health_check_fails_without_a_healthy_daemon(tmp_path: Path, daemon: type[_Recorder]) -> None:
    assert healthcheck(tmp_path) == 1  # no daemon.json
    (tmp_path / "daemon.json").write_text("{not json")
    assert healthcheck(tmp_path) == 1
    (tmp_path / "daemon.json").write_text(json.dumps({"port": daemon.port, "token": "t"}))  # type: ignore[attr-defined]
    daemon.status = 503
    assert healthcheck(tmp_path) == 1
    (tmp_path / "daemon.json").write_text(json.dumps({"port": free_port(), "token": "t"}))
    assert healthcheck(tmp_path) == 1  # a stale daemon.json: nothing listens there now
