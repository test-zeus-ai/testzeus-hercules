"""End to end: a session id in a step's URL reaches the browser, and nothing else.

A real ``hercules worker`` (as the daemon starts it, stderr kept as ``worker.log`` in the run
directory) runs a case that opens ``/login?sid=...&next=/home`` on a local app that refuses
any other session id, in real Chromium, with a fake local model that records every request.
The session id must appear in no file under the run directory, in no event and in no model
request, while the app sees the real one.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Iterator

import pytest

from tests.v2.browser_fixtures import chromium_executable
from tests.v2.fixtures.daemon_support import isolated_env
from tests.v2.fixtures.fake_model import FakeModel, action
from testzeus_hercules.engine.browser_process import BROWSER_FILE, stop_browser

SECRET = "SECRET123"
RUN_ID = "r_20261026_150000_5ec123"
LOGIN = """<!doctype html><html><head><meta charset="utf-8"><title>Home</title></head><body>
<h1>Welcome home</h1><a href="/home?sid={sid}&amp;tab=2">Open dashboard</a>
<script>console.log("signed in at " + location.href);</script>
</body></html>"""


class RecordingModel(FakeModel):
    """The fake local model, keeping every request body it was sent."""

    def __init__(self) -> None:
        super().__init__()
        self.bodies: list[dict[str, Any]] = []

    def reply(self, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        return super().reply(body)


@contextlib.contextmanager
def session_app() -> Iterator[tuple[str, list[str]]]:
    """An app whose pages need ``sid=SECRET123``: ``/login`` redirects to ``/home`` with the session id kept in the URL."""
    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return None

        def do_GET(self) -> None:  # noqa: N802
            parts = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parts.query)
            seen.append(self.path)
            if parts.path == "/favicon.ico":
                self.send_error(404)
                return
            if query.get("sid") != [SECRET]:
                self.send_error(403, "no session")
                return
            if parts.path == "/login":
                self.send_response(302)
                self.send_header("Location", f"/home?sid={SECRET}&from={urllib.parse.quote(query.get('next', ['/'])[0])}")
                self.end_headers()
                return
            body = LOGIN.format(sid=SECRET).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def stop_leftover_browsers(tmp_path: Path) -> Iterator[None]:
    yield
    for record in tmp_path.glob(f"**/.hercules/runs/*/{BROWSER_FILE}"):
        stop_browser(record.parent)


def test_a_session_id_in_a_step_url_reaches_the_browser_and_nothing_else(tmp_path: Path) -> None:
    chromium = chromium_executable()
    if chromium is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for worker tests")
    project = tmp_path / "project"
    project.mkdir()
    model = RecordingModel()
    with session_app() as (base_url, seen):
        url = f"{base_url}/login?sid={SECRET}&next=/home"
        masked = f"{base_url}/login?sid={{{{url_credential_1}}}}&next=/home"
        # The model is only ever shown the placeholder; it reads the step back with it.
        model.add(f'I open "{masked}"', action("navigate", url=masked))
        model.add('I click "Open dashboard"', action("click", target="Open dashboard"))
        model.add('I should see "Welcome home"', action("assert_text", expected="Welcome home"))
        case = {
            "id": "session",
            "title": "Open the app with a session link",
            "environment": {"base_url": base_url},
            "steps": [{"action": f'I open "{url}"'}, {"action": 'I click "Open dashboard"'}, {"action": 'I should see "Welcome home"'}],
        }
        case_path = tmp_path / "inputs" / "case.json"
        case_path.parent.mkdir()
        case_path.write_text(json.dumps(case), encoding="utf-8")
        run_dir = project / ".hercules" / "runs" / RUN_ID
        run_dir.mkdir(parents=True)
        with model.env() as model_env:
            env = isolated_env(tmp_path, HERCULES_CHROMIUM=chromium or "", **model_env)
            command = [sys.executable, "-m", "testzeus_hercules.cli.main", "worker", "--run-id", RUN_ID, "--case", str(case_path), "--project", str(project)]
            with (run_dir / "worker.log").open("wb") as log:
                proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=log, env=env, cwd=str(project), timeout=180)

    events = proc.stdout.decode("utf-8")
    result = json.loads((run_dir / "run.json").read_text())
    assert proc.returncode == 0, (result["status"], [step.get("actual") for step in result["steps"]], (run_dir / "worker.log").read_text())
    assert [step["status"] for step in result["steps"]] == ["passed"] * 3

    # The browser reached the app with the real session id.
    assert f"/login?sid={SECRET}&next=/home" in seen
    assert any(path.startswith(f"/home?sid={SECRET}") for path in seen)

    # It is in no file under the run directory, ...
    files = [path for path in run_dir.rglob("*") if path.is_file()]
    assert {"run.json", "case.json", "junit.xml", "network.jsonl", "console.jsonl", "worker.log"} <= {path.name for path in files}
    for path in files:
        assert SECRET.encode() not in path.read_bytes(), path
    # ... in no event, ...
    assert SECRET not in events and "url_credential_1" in events
    # ... and in no model request.
    assert model.bodies, "the model read the case and decided"
    for body in model.bodies:
        assert SECRET not in json.dumps(body), body
    assert any("{{url_credential_1}}" in json.dumps(body) for body in model.bodies), "the model saw the placeholder"

    # What the evidence keeps instead: the URL with the session id masked or as its placeholder.
    assert result["steps"][0]["action"] == f'I open "{masked}"'
    network = (run_dir / "network.jsonl").read_text()
    assert "sid=[REDACTED]" in network or "sid=******" in network
    console = (run_dir / "console.jsonl").read_text()
    assert "signed in at" in console and "sid=" in console
    assert "sid=[REDACTED]" in (run_dir / "case.json").read_text()
