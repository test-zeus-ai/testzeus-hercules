"""The run page's API calls (``GET /api/runs/{id}/http``): each step's ``http.json`` as one call, with the checks and saves
of the steps that used its response, read as written (masked values stay masked)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from testzeus_hercules.cases.models import RunResult, StepResult
from testzeus_hercules.daemon.http_trace import read_http_calls
from tests.v2.fixtures.daemon_support import client_for, make_config, submit, wait_for

RUN = "r_20261119_001607_10002c"
EXCHANGE = {
    "request": {"method": "POST", "url": "https://restful-booker.herokuapp.com/auth", "headers": [["Content-Type", "application/json"]], "body": '{"username": "admin", "password": "[REDACTED]"}'},
    "duration_ms": 1357,
    "response": {"status": 200, "reason": "OK", "headers": [["content-type", "application/json; charset=utf-8"]], "body": '{"token": "[REDACTED]"}', "bytes": 27},
}


def _write(run_dir: Path, step: int, name: str, record: object) -> None:
    folder = run_dir / "steps" / str(step)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(record), encoding="utf-8")


def _check(step: int, check: str, expected: str, actual: str, verdict: str = "passed") -> dict:
    return {"checks": [{"step": step, "check": check, "expected": expected, "actual": actual, "verdict": verdict, "source": "deterministic"}]}


def _evidence(step: int) -> str:
    return f"hercules://runs/{RUN}/steps/{step}/http.json"


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    folder = tmp_path / RUN
    _write(folder, 1, "http.json", EXCHANGE)
    _write(folder, 2, "checks.json", _check(2, "http_status", "200", "200 OK"))
    _write(folder, 3, "checks.json", _check(3, "json_not_empty", "$.token", "[REDACTED]"))
    (folder / "steps" / "4").mkdir()
    return folder


def _finish(run_dir: Path, steps: list[StepResult]) -> None:
    (run_dir / "run.json").write_text(RunResult(run_id=RUN, case_id="auth", status="passed", steps=steps).model_dump_json(), encoding="utf-8")


def test_a_call_carries_the_checks_and_saves_of_the_steps_that_used_its_response(run_dir: Path) -> None:
    _finish(
        run_dir,
        [
            StepResult(index=1, action='I send a POST request to "/auth"', status="passed", evidence=[_evidence(1)]),
            StepResult(index=2, action="verify", expected="the response status code should be 200", status="passed", evidence=[_evidence(1)]),
            StepResult(index=3, action="verify", expected='the response JSON "$.token" should not be empty', status="passed", evidence=[_evidence(1)]),
            StepResult(index=4, action='I save the response JSON "$.token" as "authToken"', status="passed", actual="saved [REDACTED] as {{authToken}}", evidence=[_evidence(1)]),
        ],
    )
    body = read_http_calls(run_dir.parent, RUN)
    assert len(body["calls"]) == 1 and body["more"] is False
    call = body["calls"][0]
    assert (call["method"], call["url"], call["duration_ms"], call["step"]) == ("POST", "https://restful-booker.herokuapp.com/auth", 1357, 1)
    assert call["response"]["status"] == 200 and call["response"]["bytes"] == 27
    assert call["request"]["body"] == '{"username": "admin", "password": "[REDACTED]"}', "read as written: the password stays masked"
    assert [(check["step"], check["check"], check["text"]) for check in call["checks"]] == [
        (2, "http_status", "the response status code should be 200"),
        (3, "json_not_empty", 'the response JSON "$.token" should not be empty'),
    ]
    assert call["saves"] == [{"step": 4, "name": "authToken", "value": "[REDACTED]"}]
    assert call["used_by"] == [1, 2, 3, 4]


def test_while_the_run_is_going_checks_attach_to_the_latest_call(run_dir: Path) -> None:
    second = {**EXCHANGE, "request": {**EXCHANGE["request"], "method": "GET", "url": "https://restful-booker.herokuapp.com/booking/1"}}
    _write(run_dir, 5, "http.json", second)
    _write(run_dir, 6, "checks.json", _check(6, "http_status", "200", "404 Not Found", "failed"))
    calls = read_http_calls(run_dir.parent, RUN)["calls"]
    assert [call["step"] for call in calls] == [1, 5]
    assert [check["step"] for check in calls[0]["checks"]] == [2, 3]
    assert calls[1]["checks"][0]["verdict"] == "failed" and calls[1]["saves"] == []


def test_a_database_step_does_not_lend_its_checks_to_an_earlier_call(run_dir: Path) -> None:
    _write(run_dir, 5, "sql.json", {"query": "SELECT 1", "rows": []})
    _write(run_dir, 6, "checks.json", _check(6, "row_count_eq", "1", "0 row(s)", "failed"))
    calls = read_http_calls(run_dir.parent, RUN)["calls"]
    assert [check["step"] for check in calls[0]["checks"]] == [2, 3]


def test_a_request_that_got_no_response_shows_why(tmp_path: Path) -> None:
    folder = tmp_path / RUN
    _write(folder, 1, "http.json", {"request": EXCHANGE["request"], "duration_ms": 30000, "error": "timed out after 30 s"})
    call = read_http_calls(tmp_path, RUN)["calls"][0]
    assert call["response"] is None and call["error"] == "timed out after 30 s"


def test_broken_or_unsafe_files_are_skipped(tmp_path: Path) -> None:
    folder = tmp_path / RUN
    _write(folder, 1, "http.json", EXCHANGE)
    (folder / "steps" / "2").mkdir()
    (folder / "steps" / "2" / "http.json").write_text("not json", encoding="utf-8")
    (folder / "steps" / "3").symlink_to(folder / "steps" / "1")
    assert [call["step"] for call in read_http_calls(tmp_path, RUN)["calls"]] == [1]
    assert read_http_calls(tmp_path, "../etc")["calls"] == []
    assert read_http_calls(tmp_path, "r_20261119_000000_000000")["calls"] == []


def test_the_route_lists_a_runs_calls(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        run_id = submit(client, "fake-fail")
        wait_for(client, run_id)
        _write(config.paths.runs_dir / run_id, 1, "http.json", {**EXCHANGE, "request": {**EXCHANGE["request"], "url": "https://user:hunter2@api.example.com/auth"}})
        listed = client.get(f"/api/runs/{run_id}/http")
        missing = client.get("/api/runs/r_19990101_000000_000000/http")
        documented = client.get("/api/openapi.json").json()
    assert listed.status_code == 200
    call = listed.json()["calls"][0]
    assert call["method"] == "POST" and "hunter2" not in call["url"], "URL credentials are masked on the way out"
    assert missing.status_code == 404
    assert "/api/runs/{run_id}/http" in documented["paths"] and "HttpCalls" in documented["components"]["schemas"]
