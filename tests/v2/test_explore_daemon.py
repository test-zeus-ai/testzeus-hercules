"""The daemon's exploration API: preflight, sessions through queued child runs, steering, the report, findings triage."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.explore.ledger import Ledger
from testzeus_hercules.explore.models import Finding
from tests.v2.fixtures.daemon_support import case_dict, client_for, make_config

BASE = "http://127.0.0.1:9"
FINISHED = {"finished", "failed", "stopped"}


@pytest.fixture()
def config(tmp_path: Path):  # type: ignore[no-untyped-def]
    return make_config(tmp_path)


def _spec(**extra: Any) -> dict[str, Any]:
    return {"base_url": BASE, "levers": ["inputs"], "workers": 1, "reproducers": 1, "budget": {"time_s": 120, "stall_charters": 3}, **extra}


def _wait_session(client: Any, session_id: str, timeout_s: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    since = 0
    while True:
        body = client.get(f"/api/explore/{session_id}", params={"wait_s": 2, "since": since}).json()
        since = body.get("last_seq", since)
        if body["status"] in FINISHED:
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"session {session_id} stayed {body['status']}: {body.get('phase_detail')}")


def test_preflight_reports_size_problems_and_an_estimate(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        ok = client.post("/api/explore/preflight", json=_spec(workers=2)).json()
        big = client.post("/api/explore/preflight", json=_spec(workers=40)).json()
        bad = client.post("/api/explore/preflight", json={"workers": 2})
    assert ok["problems"] == [] and ok["estimate"]["workers"] == 2 and ok["estimate"]["decisions"] > 0
    assert any("40 explorers" in problem for problem in big["problems"])
    assert bad.status_code == 422 and "say where" in bad.text


def test_a_session_runs_its_charters_as_child_runs_and_writes_the_report(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        started = client.post("/api/explore", json=_spec())
        assert started.status_code == 202, started.text
        session_id = started.json()["id"]
        resized = client.patch(f"/api/explore/{session_id}", json={"workers": 2, "by": "ana"})
        assert resized.status_code == 200 and resized.json()["workers"] == 2
        assert client.patch(f"/api/explore/{session_id}", json={}).status_code == 422
        over = client.patch(f"/api/explore/{session_id}", json={"workers": 40})
        assert over.status_code == 422 and "40 explorers" in over.text  # never a silent cut, never a silent raise
        added = client.post(f"/api/explore/{session_id}/charters", json={"title": "Try the cart with a zero quantity", "area": "/cart", "levers": ["inputs"], "pinned": True})
        assert added.status_code == 201, added.text
        assert client.post(f"/api/explore/{session_id}/charters/ch-nope", json={"action": "veto"}).status_code == 409
        view = _wait_session(client, session_id)
        listing = client.get("/api/explore").json()["sessions"]
        report = client.get(f"/api/explore/{session_id}/report")
        report_json = client.get(f"/api/explore/{session_id}/report", params={"format": "json"}).json()
        runs = client.get("/api/runs", params={"limit": 50}).json()
    assert view["status"] == "finished", view.get("message")
    assert view["size_changes"] and view["size_changes"][0]["workers"] == [1, 2] and view["size_changes"][0]["by"] == "ana"  # the person's change is logged
    titles = {charter["title"]: charter["status"] for charter in view["charters"]}
    assert titles.get("Try the cart with a zero quantity") == "done"
    assert any(charter["kind"] == "recon" and charter["status"] == "done" for charter in view["charters"])
    assert view["children"], "children run through the daemon's queue"
    assert session_id in {item["id"] for item in listing}
    assert report.status_code == 200 and "No findings is not a pass" in report.text and "## Coverage gaps" in report.text
    assert report_json["id"] == session_id and report_json["findings"] == []
    explore_runs = [run for run in runs.get("runs", runs if isinstance(runs, list) else []) if (run.get("source") or {}).get("kind") == "explore"]
    assert explore_runs or view["children"]


def test_a_production_looking_host_is_refused(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        session_id = client.post("/api/explore", json=_spec(base_url="https://shop.acme.com")).json()["id"]
        view = _wait_session(client, session_id, timeout_s=20)
    assert view["status"] == "failed" and "does not look like a test environment" in view["message"]


def test_stop_ends_a_session_with_a_report(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        session_id = client.post("/api/explore", json=_spec(budget={"time_s": 600})).json()["id"]
        assert client.post(f"/api/explore/{session_id}/stop").json() == {"stopping": True}
        view = _wait_session(client, session_id, timeout_s=40)
        report = client.get(f"/api/explore/{session_id}/report")
    assert view["status"] in ("stopped", "finished")
    assert report.status_code == 200


def test_unknown_sessions_and_findings_are_404(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        assert client.get("/api/explore/x_20260101_000000_abcdef").status_code == 404
        assert client.get("/api/explore/x_..%2F..%2Fetc").status_code == 404
        assert client.get("/api/explore/x_20260101_000000_abcdef/report").status_code == 404
        assert client.post("/api/explore/findings/fnope/triage", json={"by": "ana", "state": "confirmed"}).status_code == 404
        assert client.get("/api/explore/findings/fnope/issue").status_code == 404


def test_a_session_from_an_earlier_daemon_is_served_read_only(config) -> None:  # type: ignore[no-untyped-def]
    import json

    folder = config.paths.project_hercules_dir / "explore" / "x_20260101_000000_abcdef"
    folder.mkdir(parents=True)
    view = {"id": folder.name, "status": "exploring", "started_at": "2026-01-01T00:00:00Z", "elapsed_s": 30, "findings": []}
    (folder / "session.json").write_text(json.dumps(view), encoding="utf-8")
    with client_for(config) as client:
        got = client.get(f"/api/explore/{folder.name}").json()
    assert got["read_only"] and got["status"] == "stopped" and "daemon stopped" in got["message"] and got["events"] == []


def test_findings_triage_and_issue_text(config) -> None:  # type: ignore[no-untyped-def]
    ledger = Ledger(config.paths.project_hercules_dir / "explore")
    ledger.record(
        Finding(
            id="f1a2b3c4d5e",
            title="Saving a long name returns a server error",
            signature="server_error|/profile|5xx",
            oracle="server_error",
            tier="code",
            proposed_severity="P1",
            proposed_priority="P1",
            steps=['Open "/profile"'],
            expected="saved",
            actual="HTTP 500",
        )
    )
    with client_for(config) as client:
        listed = client.get("/api/explore/findings").json()
        assert [item["id"] for item in listed["findings"]] == ["f1a2b3c4d5e"] and listed["precision"]["untriaged"] == 1
        refused = client.post("/api/explore/findings/f1a2b3c4d5e/triage", json={"by": "ana", "state": "wont_fix"})
        assert refused.status_code == 409 and "say why" in refused.text
        triaged = client.post("/api/explore/findings/f1a2b3c4d5e/triage", json={"by": "ana", "state": "confirmed", "severity": "P0"}).json()
        assert triaged["state"] == "confirmed" and triaged["severity"] == "P0" and [event["field"] for event in triaged["audit"]] == ["severity", "state", "case"] and triaged["case"] == "kept"
        issue = client.get("/api/explore/findings/f1a2b3c4d5e/issue", params={"tracker": "github", "repository": "acme/shop"}).json()
        assert issue["title"].startswith("[P0]") and issue["url"].startswith("https://github.com/acme/shop/issues/new?")
        assert client.get("/api/explore/findings/f1a2b3c4d5e/issue", params={"tracker": "trello"}).status_code == 422
        assert client.get("/api/explore/findings/f1a2b3c4d5e/issue", params={"tracker": "github", "repository": "not a repo"}).status_code == 422
        confirmed = client.get("/api/explore/findings", params={"state": "confirmed"}).json()
    assert len(confirmed["findings"]) == 1 and confirmed["precision"]["precision"] == 1.0


def _finding(fid: str, sig: str, **extra: Any) -> Finding:
    return Finding(id=fid, title=f"finding {fid}", signature=sig, oracle="server_error", tier="code", proposed_severity="P2", proposed_priority="P2", **extra)


def test_one_finding_by_id_or_unique_prefix(config) -> None:  # type: ignore[no-untyped-def]
    ledger = Ledger(config.paths.project_hercules_dir / "explore")
    for fid, sig in (("fabc0000001", "a"), ("fabc0000002", "b"), ("f9990000000", "c")):
        ledger.record(_finding(fid, sig, sessions=["x_20260101_000000_abcdef"] if fid == "f9990000000" else []))
    with client_for(config) as client:
        whole = client.get("/api/explore/findings/fabc0000001")
        prefix = client.get("/api/explore/findings/f999")
        ambiguous = client.get("/api/explore/findings/fabc")
        short = client.get("/api/explore/findings/f99")
        missing = client.get("/api/explore/findings/f12345")
        triaged = client.post("/api/explore/findings/f999/triage", json={"by": "ana via coding agent", "state": "confirmed", "reason": 'the user said: "yes, confirm it"'})
        issue = client.get("/api/explore/findings/f999/issue")
    assert whole.status_code == 200 and whole.json()["id"] == "fabc0000001" and whole.json()["steps"] == []
    assert prefix.status_code == 200 and prefix.json()["id"] == "f9990000000"
    assert ambiguous.status_code == 400 and "fabc0000001" in ambiguous.text and "fabc0000002" in ambiguous.text
    assert short.status_code == 404 and missing.status_code == 404 and "no finding" in missing.text
    assert triaged.status_code == 200 and triaged.json()["audit"][-1]["by"] == "ana via coding agent"
    assert issue.status_code == 200


def test_findings_filter_by_case_and_session(config) -> None:  # type: ignore[no-untyped-def]
    ledger = Ledger(config.paths.project_hercules_dir / "explore")
    ledger.record(_finding("f0000000001", "a", sessions=["x_20260101_000000_aaaaaa"]))
    ledger.record(_finding("f0000000002", "b", sessions=["x_20260101_000000_bbbbbb"]))
    ledger.triage("f0000000002", by="ana", state="confirmed")
    with client_for(config) as client:
        kept = client.get("/api/explore/findings", params={"case": "kept"}).json()
        session = client.get("/api/explore/findings", params={"session": "x_20260101_000000_aaaaaa"}).json()
        bad = client.get("/api/explore/findings", params={"case": "maybe"})
    assert [item["id"] for item in kept["findings"]] == ["f0000000002"] and kept["total"] == 1
    assert [item["id"] for item in session["findings"]] == ["f0000000001"]
    assert bad.status_code == 422


def test_preflight_reports_a_production_looking_host_apart_from_size(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        prod = client.post("/api/explore/preflight", json=_spec(base_url="https://shop.acme.com")).json()
        owned = client.post("/api/explore/preflight", json=_spec(base_url="https://shop.acme.com", production_ok=True)).json()
        local = client.post("/api/explore/preflight", json=_spec()).json()
    assert prod["problems"] == [] and "shop.acme.com does not look like a test environment" in prod["host_problem"] and "Ask the user" in prod["host_problem"]
    assert owned["host_problem"] is None and local["host_problem"] is None


def test_stop_records_who_stopped_it(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        session_id = client.post("/api/explore", json=_spec(budget={"time_s": 600})).json()["id"]
        assert client.post(f"/api/explore/{session_id}/stop", json={"by": "a coding agent"}).json() == {"stopping": True}
        view = _wait_session(client, session_id, timeout_s=40)
    assert view["status"] in ("stopped", "finished") and "a coding agent" in view["message"]


def test_reverify_job_runs_kept_and_regression_cases(config) -> None:  # type: ignore[no-untyped-def]
    import json

    project = config.settings.project_dir
    staging = config.paths.project_hercules_dir / "explore" / "cases"
    staging.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(config.paths.project_hercules_dir / "explore")
    for fid, tags in (("f00000000aa", []), ("f00000000bb", ["fake-fail"])):
        case = {**case_dict(*tags, case_id=fid), "status": "known_bug"}
        (staging / f"{fid}.json").write_text(json.dumps(case), encoding="utf-8")
        ledger.record(_finding(fid, fid, case_path=f".hercules/explore/cases/{fid}.json"))
        ledger.triage(fid, by="ana", state="confirmed")
    ledger.triage("f00000000bb", by="ana", state="verify_fixed")
    ledger.triage("f00000000bb", by="ana", case="regression", reason="guards search")
    ledger.triage("f00000000bb", by="ana", state="closed")
    assert (project / ".hercules" / "cases" / "explore-f00000000bb.json").is_file()
    with client_for(config) as client:
        started = client.post("/api/explore/reverify", json={"build": "b7", "case_timeout_s": 120})
        assert started.status_code == 202, started.text
        job_id = started.json()["id"]
        again = client.post("/api/explore/reverify", json={})
        deadline = time.monotonic() + 60
        job = started.json()
        while not job["finished"] and time.monotonic() < deadline:
            job = client.get(f"/api/explore/reverify/{job_id}", params={"wait_s": 2}).json()
        missing = client.get("/api/explore/reverify/rv_00000000")
    assert again.status_code == 409 and job_id in again.text or again.status_code == 202  # one at a time (202 only if the first had already ended)
    assert job["finished"] and job["exit_code"] == 1, job
    outcomes = {item["finding"]: item["outcome"] for item in job["results"]}
    assert outcomes == {"f00000000aa": "verify_fixed", "f00000000bb": "regression"}
    assert ledger.get("f00000000aa").state == "verify_fixed" and ledger.get("f00000000bb").state == "new"  # type: ignore[union-attr]
    assert missing.status_code == 404
