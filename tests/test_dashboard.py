"""Offline tests for the starlette dashboard (testzeus_hercules.dashboard.app).

Seeds a temp project root with a synthetic run output (merged JUnit XML plus
fake evidence files under proofs/), imports it through POST /import and
exercises every route via TestClient, including /artifact path-traversal
hardening. No network, no LLM, no uvicorn server.
"""

import base64
import os
import pathlib
import sqlite3
import subprocess
import sys
import urllib.parse
from xml.sax.saxutils import escape

import pytest
from sqlalchemy.exc import OperationalError
from starlette.testclient import TestClient
from testzeus_hercules.dashboard import app as dashboard_app
from testzeus_hercules.dashboard.app import create_app, main, normalize_db_url

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

RUN_ID = "run_20260102_030405"
PASSED_CASE_NAME = "Scenario: User views the bike list"
FAILED_CASE_NAME = "Scenario: Search fails: bike count wrong"

# Smallest valid 1x1 PNG; content is irrelevant, only the served media type matters.
PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _property_xml(name: str, value: str) -> str:
    return f'        <property name="{escape(name, {chr(34): "&quot;"})}" value="{escape(value, {chr(34): "&quot;"})}"/>'


def _junit_xml(project_root) -> str:
    """Two testcases: one passed, one failed with video/screenshot/network/planner evidence."""
    proofs = project_root / "proofs"
    passed_props = "\n".join(
        [
            _property_xml("Terminate", "yes"),
            _property_xml("total_cost", "0.05"),
        ]
    )
    failed_props = "\n".join(
        [
            _property_xml("Terminate", "no"),
            _property_xml("Proofs Video", str(proofs / "recording.webm")),
            _property_xml("Proofs Screenshot", str(proofs / "screenshots")),
            _property_xml("Network Logs", str(proofs / "network_logs.json")),
            _property_xml("Planner Thoughts", str(proofs / "planner_thoughts.json")),
        ]
    )
    return f"""<testsuites>
  <testsuite name="Feature: Bike Search" tests="2" failures="1" skipped="0" time="9.0">
    <testcase name="{escape(PASSED_CASE_NAME)}" classname="Feature: Bike Search" time="1.5">
      <properties>
{passed_props}
      </properties>
      <system-out>Final Response: ok</system-out>
    </testcase>
    <testcase name="{escape(FAILED_CASE_NAME)}" classname="Feature: Bike Search" time="7.5">
      <properties>
{failed_props}
      </properties>
      <failure message="Assertion failed: expected status code 200 but got 500" type="AssertionError"></failure>
      <system-out>Final Response: the bike count endpoint returned an error</system-out>
    </testcase>
  </testsuite>
</testsuites>
"""


@pytest.fixture()
def project_root(tmp_path):
    """Temp project root with output/<run>/final_result.xml plus proofs/ evidence files."""
    proofs = tmp_path / "proofs"
    screenshots = proofs / "screenshots"
    screenshots.mkdir(parents=True)
    (proofs / "recording.webm").write_bytes(b"\x1a\x45\xdf\xa3fake-webm-payload")
    (screenshots / "step_01.png").write_bytes(PNG_1X1)
    (screenshots / "step_02.png").write_bytes(PNG_1X1)
    (proofs / "network_logs.json").write_text(
        "\n".join(
            [
                '{"method": "GET", "url": "https://example.test/api/bikes", "status": 500, "duration": 123.4}',
                '{"url": "https://example.test/health"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (proofs / "planner_thoughts.json").write_text("planner thought: open the bike list\nplanner thought: count the results\n", encoding="utf-8")

    run_dir = tmp_path / "output" / RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "final_result.xml").write_text(_junit_xml(tmp_path), encoding="utf-8")
    return tmp_path


@pytest.fixture()
def client(project_root):
    """TestClient over a dashboard app bound to a temp sqlite history DB."""
    app = create_app(db_url=f"sqlite:///{project_root / 'history.db'}", project_root=str(project_root))
    with TestClient(app) as test_client:
        yield test_client


def _client_for(tmp_path, junit_text: str, run_id: str = RUN_ID):
    """TestClient over a fresh project root holding one output/<run>/final_result.xml."""
    run_dir = tmp_path / "output" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "final_result.xml").write_text(junit_text, encoding="utf-8")
    app = create_app(db_url=f"sqlite:///{tmp_path / 'history.db'}", project_root=str(tmp_path))
    return TestClient(app)


def test_home_empty_then_import_lists_run(client) -> None:
    empty = client.get("/")
    assert empty.status_code == 200
    assert "No runs recorded yet" in empty.text
    assert "Import from output folders" in empty.text

    imported = client.post("/import")
    assert imported.status_code == 200  # TestClient follows the 303 redirect back to /

    page = client.get("/")
    assert RUN_ID in page.text
    assert "badge-failed" in page.text
    assert f"/run/{RUN_ID}" in page.text


def test_import_is_idempotent(client) -> None:
    client.post("/import")
    client.post("/import")
    runs = client.get("/api/runs").json()
    assert [entry["run_id"] for entry in runs] == [RUN_ID]


def test_import_rejects_truncated_junit(tmp_path) -> None:
    truncated = '<testsuites><testsuite name="Feature: Broken" tests="1">'
    with _client_for(tmp_path, truncated) as client:
        response = client.post("/import")

        assert response.status_code == 400
        assert "could not parse junit xml" in response.text
        # No run row may be created from an unparseable file.
        assert client.get("/api/runs").json() == []


def test_import_rejects_junit_with_zero_testcases(tmp_path) -> None:
    empty = '<testsuites><testsuite name="Feature: Empty" tests="0" failures="0" skipped="0" time="0"></testsuite></testsuites>'
    with _client_for(tmp_path, empty) as client:
        response = client.post("/import")

        assert response.status_code == 400
        assert "0 testcases" in response.text
        assert client.get("/api/runs").json() == []


def test_import_error_body_does_not_leak_absolute_paths(tmp_path) -> None:
    """The 400 body names the offending file by basename only, never the server's absolute dirs."""
    truncated = '<testsuites><testsuite name="Feature: Broken" tests="1">'
    with _client_for(tmp_path, truncated) as client:
        response = client.post("/import")

        assert response.status_code == 400
        assert "final_result.xml" in response.text
        assert str(tmp_path) not in response.text
        assert "/" + tmp_path.name not in response.text


def _count_rows(db_path: pathlib.Path, table: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


def _count_rows_where(db_path: pathlib.Path, table: str, where: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
    finally:
        connection.close()


def test_import_replaces_poisoned_empty_run(tmp_path) -> None:
    """A run recorded empty (e.g. by an older import bug) must be re-importable once the XML is fixed."""
    with _client_for(tmp_path, _junit_xml(tmp_path)) as client:
        history = client.app.state.history
        history.start_run(RUN_ID)
        history.finish_run(RUN_ID, summary={"total": 0, "passed": 0, "failed": 0, "errors": 0}, status="passed")

        response = client.post("/import")

        assert response.status_code == 200
        runs = client.get("/api/runs").json()
        # Re-imported in place: same run_id (no "_1" rename duplicate) and real cases now.
        assert [entry["run_id"] for entry in runs] == [RUN_ID]
        assert runs[0]["case_count"] == 2
        page = client.get(f"/run/{RUN_ID}")
        assert FAILED_CASE_NAME in page.text


def test_import_replaces_poisoned_empty_run_without_orphans(tmp_path) -> None:
    """Replacing a poisoned empty run must clear run-level artifacts/healing events too (test_case_id IS NULL).

    A bare ORM session.delete(run) does NOT cascade to those rows — RunHistory.delete_run does.
    """
    with _client_for(tmp_path, _junit_xml(tmp_path)) as client:
        history = client.app.state.history
        history.start_run(RUN_ID)
        history.finish_run(RUN_ID, summary={"total": 0, "passed": 0, "failed": 0, "errors": 0}, status="passed")
        history.record_healing_event(RUN_ID, None, kind="heal_proposed", canonical_name="role=button|name=Search", detail="d", status="ok")
        history.record_artifact(RUN_ID, None, kind="video", path=str(tmp_path / "recording.webm"))

        response = client.post("/import")

        assert response.status_code == 200
        runs = client.get("/api/runs").json()
        assert [entry["run_id"] for entry in runs] == [RUN_ID]
        assert runs[0]["case_count"] == 2
        # Run-level children (test_case_id IS NULL) must be gone — a bare ORM
        # session.delete(run) cascade would orphan exactly these rows. The
        # case-level artifacts re-created by record_run_summary stay.
        assert _count_rows(tmp_path / "history.db", "healing_events") == 0
        assert _count_rows_where(tmp_path / "history.db", "artifacts", "test_case_id IS NULL") == 0
        assert _count_rows_where(tmp_path / "history.db", "artifacts", "test_case_id IS NOT NULL") == 4  # video+screenshot+network+planner of the failed case, re-created by record_run_summary


def test_import_returns_503_when_history_db_locked(client, monkeypatch) -> None:
    def locked_record(*args, **kwargs):
        raise OperationalError("INSERT INTO runs", {}, Exception("database is locked"))

    monkeypatch.setattr(dashboard_app, "record_run_summary", locked_record)

    response = client.post("/import")

    assert response.status_code == 503
    assert "locked" in response.text
    assert "again" in response.text
    assert client.get("/api/runs").json() == []


def test_import_503_body_does_not_leak_absolute_paths(client, monkeypatch) -> None:
    """The 503 retry hint keeps the driver message's first line but drops absolute paths and SQL."""
    db_path = client.app.state.history.db_url.replace("sqlite:///", "")
    inner = Exception(f"unable to open database file {db_path}")

    def locked_record(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, inner)

    monkeypatch.setattr(dashboard_app, "record_run_summary", locked_record)

    response = client.post("/import")

    assert response.status_code == 503
    assert "unable to open database file" in response.text
    assert "history.db" in response.text
    assert str(pathlib.Path(db_path).parent) not in response.text  # no directory part of the DB path
    assert "[SQL:" not in response.text and "SELECT" not in response.text


def test_run_page_shows_cases_and_trend_link(client) -> None:
    client.post("/import")
    page = client.get(f"/run/{RUN_ID}")
    assert page.status_code == 200
    assert FAILED_CASE_NAME in page.text
    assert "badge-failed" in page.text
    assert "/trend" in page.text
    # Case link must be URL-encoded (case names contain spaces and colons).
    assert urllib.parse.quote(FAILED_CASE_NAME, safe="") in page.text


def test_run_page_unknown_run_404(client) -> None:
    client.post("/import")
    assert client.get("/run/does_not_exist").status_code == 404


def test_case_page_classification_video_network_steps(client) -> None:
    client.post("/import")
    client.app.state.history.record_steps(
        RUN_ID,
        FAILED_CASE_NAME,
        [{"idx": 1, "type": "navigation", "description": "open the bike list page", "status": "passed", "duration_s": 1.0, "error": None}],
    )

    url = f"/case/{urllib.parse.quote(RUN_ID, safe='')}/{urllib.parse.quote(FAILED_CASE_NAME, safe='')}"
    page = client.get(url)
    assert page.status_code == 200

    # Classification card: "expected status code ... but got 500" matches 2 of 4
    # contract_violation signals (http_status_code, status_code_mention) -> 50%.
    assert "Failure classification" in page.text
    assert "contract_violation" in page.text
    assert "50%" in page.text
    assert "http_status_code" in page.text
    assert "status_code_mention" in page.text

    # Video player and screenshots gallery.
    assert "<video controls" in page.text
    assert "recording.webm" in page.text
    assert page.text.count("<img") == 2

    # Network log viewer renders both JSONL entries (missing fields tolerated).
    assert "example.test/api/bikes" in page.text
    assert "example.test/health" in page.text
    assert ">500<" in page.text

    assert "planner thought: open the bike list" in page.text
    assert "open the bike list page" in page.text  # steps table


def test_case_page_unknown_case_404(client) -> None:
    client.post("/import")
    url = f"/case/{RUN_ID}/{urllib.parse.quote('Scenario: Missing case', safe='')}"
    assert client.get(url).status_code == 404


def test_case_key_with_slash_is_routable(tmp_path) -> None:
    """Case names are legal JUnit names and may contain "/" — the link must still resolve."""
    slash_case = "Scenario: case/key:with slash"
    junit = (
        '<testsuites><testsuite name="Feature: Slash" tests="1" failures="1" skipped="0" time="1.0">'
        f'<testcase name="{escape(slash_case)}" classname="Feature: Slash" time="1.0">'
        '<failure message="Request to /api/x returned 404" type="AssertionError"></failure>'
        "</testcase></testsuite></testsuites>"
    )
    with _client_for(tmp_path, junit) as client:
        client.post("/import")

        run_page = client.get(f"/run/{RUN_ID}")
        assert run_page.status_code == 200

        href = f"/case/{urllib.parse.quote(RUN_ID, safe='')}/{urllib.parse.quote(slash_case, safe='')}"
        assert href in run_page.text
        page = client.get(href)
        assert page.status_code == 200
        assert slash_case in page.text


def test_case_page_renders_healing_events_and_prefers_recorded_classification(client) -> None:
    client.post("/import")
    history = client.app.state.history
    history.record_healing_event(
        RUN_ID,
        FAILED_CASE_NAME,
        kind="failure_classified",
        canonical_name="role=button|name=Search",
        detail="test_fragility (confidence=0.30)",
        status="ok",
    )
    history.record_healing_event(RUN_ID, FAILED_CASE_NAME, kind="heal_proposed", canonical_name="role=button|name=Search", detail="basis=llm, confidence=0.40", status="ok")

    url = f"/case/{urllib.parse.quote(RUN_ID, safe='')}/{urllib.parse.quote(FAILED_CASE_NAME, safe='')}"
    page = client.get(url)
    assert page.status_code == 200

    # Healing section renders the events recorded in run_db.
    assert "<h2>Healing</h2>" in page.text
    assert "failure_classified" in page.text
    assert "heal_proposed" in page.text
    assert "role=button|name=Search" in page.text

    # The recorded classification wins over the on-the-fly heuristic: the raw
    # message ("expected status code 200 but got 500") would recompute as
    # contract_violation, so its absence proves the recorded event was used.
    assert "test_fragility" in page.text
    assert "recorded at healing time" in page.text
    assert "contract_violation" not in page.text


def test_artifact_serves_files_with_media_types(client, project_root) -> None:
    response = client.get("/artifact", params={"path": str(project_root / "proofs" / "screenshots" / "step_01.png")})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG_1X1

    video = client.get("/artifact", params={"path": str(project_root / "proofs" / "recording.webm")})
    assert video.status_code == 200
    assert video.headers["content-type"] == "video/webm"


def test_artifact_keeps_safe_media_inline(client, project_root) -> None:
    """Images/videos stay renderable (no forced download), with nosniff on top."""
    response = client.get("/artifact", params={"path": str(project_root / "proofs" / "screenshots" / "step_01.png")})
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-disposition" not in response.headers

    video = client.get("/artifact", params={"path": str(project_root / "proofs" / "recording.webm")})
    assert "content-disposition" not in video.headers
    assert video.headers["x-content-type-options"] == "nosniff"


def test_artifact_downloads_active_content_instead_of_rendering(client, project_root) -> None:
    """text/html, image/svg+xml and unknown types must download, never render inline."""
    proofs = project_root / "proofs"
    (proofs / "page.html").write_text("<html><script>alert('xss')</script></html>", encoding="utf-8")
    (proofs / "vector.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>alert('xss')</script></svg>", encoding="utf-8")
    (proofs / "mystery.unknownext").write_bytes(b"\x00\x01binary-ish")

    html = client.get("/artifact", params={"path": str(proofs / "page.html")})
    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert html.headers["content-disposition"].startswith("attachment;")
    assert "page.html" in html.headers["content-disposition"]

    svg = client.get("/artifact", params={"path": str(proofs / "vector.svg")})
    assert svg.headers["content-type"] == "image/svg+xml"
    assert svg.headers["content-disposition"].startswith("attachment;")

    unknown = client.get("/artifact", params={"path": str(proofs / "mystery.unknownext")})
    assert unknown.headers["content-type"] == "application/octet-stream"
    assert unknown.headers["content-disposition"].startswith("attachment;")

    for response in (html, svg, unknown):
        assert response.headers["x-content-type-options"] == "nosniff"


def test_artifact_rejects_traversal_and_missing_files(client, project_root) -> None:
    assert client.get("/artifact", params={"path": "/etc/passwd"}).status_code == 403
    traversal = str(project_root / ".." / ".." / "etc" / "passwd")
    assert client.get("/artifact", params={"path": traversal}).status_code == 403
    assert client.get("/artifact", params={"path": str(project_root / "proofs" / "missing.png")}).status_code == 404
    assert client.get("/artifact").status_code == 400


def test_trend_page_renders_svg(client) -> None:
    client.post("/import")
    page = client.get("/trend")
    assert page.status_code == 200
    assert "<svg" in page.text
    assert "run_20260102" in page.text  # truncated run label under the bar
    assert "Flakiest cases" in page.text


def test_api_runs_and_latest(client) -> None:
    client.post("/import")
    runs = client.get("/api/runs").json()
    assert len(runs) == 1
    assert runs[0]["run_id"] == RUN_ID

    latest = client.get("/api/runs/latest").json()
    assert latest["run_id"] == RUN_ID
    assert latest["status"] == "failed"
    assert latest["case_passed"] == 1 and latest["case_failed"] == 1


def test_latest_returns_empty_dict_without_runs(client) -> None:
    assert client.get("/api/runs/latest").json() == {}


# ---------------------------------------------------------------------------
# _runs_table cost enrichment (list_runs fast path + get_run fallback)
# ---------------------------------------------------------------------------


class _CountingHistory:
    """RunHistory stand-in that records get_run calls."""

    def __init__(self):
        self.get_run_calls = []

    def get_run(self, run_id):
        self.get_run_calls.append(run_id)
        return {"total_cost": 9.99}


def _run_entry(**overrides) -> dict:
    entry = {
        "run_id": "run_x",
        "started_at": "2026-01-02T03:04:05",
        "status": "passed",
        "case_passed": 1,
        "case_failed": 0,
        "case_errors": 0,
        "duration_s": 1.0,
    }
    entry.update(overrides)
    return entry


def test_runs_table_uses_list_runs_total_cost_without_get_run() -> None:
    """Newer run_db carries total_cost in list_runs: no per-row enrichment query."""
    history = _CountingHistory()

    table = dashboard_app._runs_table([_run_entry(total_cost=0.5)], history)

    assert "$0.5000" in table
    assert history.get_run_calls == []


def test_runs_table_falls_back_to_get_run_without_total_cost_key() -> None:
    """Older run_db without the list_runs key still gets its cost via get_run."""
    history = _CountingHistory()

    table = dashboard_app._runs_table([_run_entry()], history)

    assert "$9.9900" in table
    assert history.get_run_calls == ["run_x"]


def test_runs_table_survives_get_run_failures() -> None:
    class BrokenHistory:
        def get_run(self, run_id):
            raise ValueError("run vanished")

    table = dashboard_app._runs_table([_run_entry()], BrokenHistory())

    assert "—" in table  # unknown cost renders as an em dash, page still renders


def test_live_page_polls_latest_api(client) -> None:
    page = client.get("/live")
    assert page.status_code == 200
    assert "setInterval" in page.text
    assert "/api/runs/latest" in page.text


def test_main_help_parses_without_server() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0


def test_normalize_db_url_accepts_urls_and_bare_paths(tmp_path) -> None:
    # Full URLs pass through untouched...
    assert normalize_db_url("postgresql://localhost/hist") == "postgresql://localhost/hist"
    assert normalize_db_url("sqlite:////absolute/other.db") == "sqlite:////absolute/other.db"
    # ...bare filesystem paths get the sqlite:/// prefix (like HERCULES_HISTORY_DB)...
    bare = tmp_path / "history.db"
    assert normalize_db_url(str(bare)) == f"sqlite:///{bare}"
    assert normalize_db_url("history.db") == f"sqlite:///{os.path.abspath('history.db')}"
    # ...and empty values stay empty so the default location is used.
    assert normalize_db_url(None) is None
    assert normalize_db_url("") is None


def test_module_entrypoint_help_smoke() -> None:
    """`python -m testzeus_hercules.dashboard.app --help` exits 0 (import + argparse wiring intact)."""
    result = subprocess.run(
        [sys.executable, "-m", "testzeus_hercules.dashboard.app", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    output = result.stdout + result.stderr
    assert "--help" in output
    assert "--port" in output  # the dashboard-specific flag the subcommand parser owns
