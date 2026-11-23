"""`hercules explore` and the MCP explore tools against a daemon on a real port (children run by the fake worker)."""

from __future__ import annotations

import asyncio
import io
import json
import re
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.cli.main import main
from testzeus_hercules.explore.ledger import Ledger
from testzeus_hercules.explore.models import Finding
from tests.v2.fixtures.daemon_support import daemon_thread, make_config

BASE = "http://127.0.0.1:9"


@pytest.fixture()
def daemon(tmp_path: Path):  # type: ignore[no-untyped-def]
    config = make_config(tmp_path, cancel_grace_s=0.5)
    with daemon_thread(config) as info:
        yield config, info


def _cli(config: Any, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["explore", *argv, "--project", str(config.settings.project_dir)] if argv and argv[0] in ("start",) else ["explore", *argv],
        env={**config.env, "HERCULES_PROJECT": str(config.settings.project_dir)},
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def test_cli_start_waits_and_prints_the_report_path(daemon, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon
    monkeypatch.chdir(config.settings.project_dir)
    code, out, err = _cli(config, "start", "--base-url", BASE, "--time", "2m", "--workers", "1", "--levers", "inputs", "--yes")
    assert code == 0, err
    assert "Swarm: 1 explorer(s), 1 reproducer(s) for 2 min" in out
    session_id = re.search(r"Session (x_\S+) started", out).group(1)  # type: ignore[union-attr]
    code, status, _ = _cli(config, "status", session_id, "--json")
    assert code == 0 and json.loads(status)["status"] == "finished"
    code, report, _ = _cli(config, "report", session_id)
    assert code == 0 and "No findings is not a pass" in report


def test_cli_refuses_a_size_over_the_guard_rail(daemon, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon
    monkeypatch.chdir(config.settings.project_dir)
    code, out, err = _cli(config, "start", "--base-url", BASE, "--workers", "40", "--yes", "--no-wait")
    assert code == 2 and "over the guard rail: 40 explorers" in out and "--allow-more-workers" in err


def test_cli_findings_triage_and_issue(daemon, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon
    monkeypatch.chdir(config.settings.project_dir)
    Ledger(config.paths.project_hercules_dir / "explore").record(
        Finding(id="f0123456789", title="Badge shows a stale count", signature="invariant|/cart|badge", oracle="invariant", tier="invariant", proposed_severity="P2", proposed_priority="P2")
    )
    code, listed, _ = _cli(config, "findings")
    assert code == 0 and "f0123456789" in listed and "Badge shows a stale count" in listed
    code, _, err = _cli(config, "triage", "f0123456789", "--state", "by_design", "--by", "ana")
    assert code == 2 and "say why" in err
    code, _, err = _cli(config, "triage", "f0123456789", "--state", "confirmed", "--severity", "P1", "--by", "ana")
    assert code == 0, err
    code, issue, _ = _cli(config, "issue", "f0123456789", "--tracker", "jira")
    assert code == 0 and "h3. Steps to reproduce" in issue and "P1" in issue


def test_cli_keeps_a_findings_case_in_the_suite_and_rejects_another(daemon, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon
    project = config.settings.project_dir
    monkeypatch.chdir(project)
    staging = config.paths.project_hercules_dir / "explore" / "cases"
    staging.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(config.paths.project_hercules_dir / "explore")
    for fid, sig in (("f1111111111", "server_error|/search|5xx"), ("f2222222222", "page_error|/|push sdk")):
        case = {"id": fid, "title": f"case {fid}", "steps": [{"action": 'Open "/"', "expected": "No error"}], "status": "known_bug", "tags": ["explore"]}
        (staging / f"{fid}.json").write_text(json.dumps(case), encoding="utf-8")
        ledger.record(
            Finding(id=fid, title=f"finding {fid}", signature=sig, oracle="server_error", tier="code", proposed_severity="P2", proposed_priority="P2", case_path=f".hercules/explore/cases/{fid}.json")
        )
    code, out, err = _cli(config, "triage", "f1111111111", "--case", "kept", "--by", "ana")
    assert code == 0 and "case kept at .hercules/cases/explore-f1111111111.json" in out, err
    assert json.loads((project / ".hercules" / "cases" / "explore-f1111111111.json").read_text(encoding="utf-8"))["status"] == "known_bug"
    code, _, err = _cli(config, "triage", "f2222222222", "--case", "rejected", "--by", "ana")
    assert code == 2 and "say why" in err
    code, out, err = _cli(config, "triage", "f2222222222", "--case", "rejected", "--by", "ana", "--reason", "a third-party script, not the app")
    assert code == 0 and "case rejected" in out, err
    code, listed, _ = _cli(config, "findings", "--case", "kept")
    assert "f1111111111" in listed and "f2222222222" not in listed


def test_mcp_explore_tools_start_follow_and_report(daemon) -> None:  # type: ignore[no-untyped-def]
    from tests.v2.test_mcp_shim import _text, call, mcp_session

    config, _ = daemon

    async def scenario() -> dict[str, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            nowhere = await call(session, "hercules_explore", {"mission": "p1"})
            too_big = await call(session, "hercules_explore", {"base_url": BASE, "workers": 40})
            started = await call(session, "hercules_explore", {"base_url": BASE, "time_s": 120, "workers": 1})
            session_id = started.structuredContent["session_id"]
            early = await call(session, "hercules_explore_report", {"session_id": session_id})
            status = started
            for _ in range(60):
                status = await call(session, "hercules_explore_status", {"session_id": session_id, "wait_s": 10})
                if status.structuredContent["finished"]:
                    break
            report = await call(session, "hercules_explore_report", {"session_id": session_id})
            full = await call(session, "hercules_explore_report", {"session_id": session_id, "full": True})
            sessions = await call(session, "hercules_explore_sessions", {"limit": 5})
            prod = await call(session, "hercules_explore", {"base_url": "https://shop.acme.com", "time_s": 120, "workers": 1})
            bad = await call(session, "hercules_explore_status", {"session_id": "../x"})
            return {"nowhere": nowhere, "too_big": too_big, "started": started, "early": early, "status": status, "report": report, "full": full, "sessions": sessions, "prod": prod, "bad": bad}

    out = asyncio.run(scenario())
    assert out["nowhere"].isError and "say where to explore" in _text(out["nowhere"])
    assert out["too_big"].isError and "40 explorers" in _text(out["too_big"])
    assert out["started"].structuredContent["workers"] == 1 and "estimate" in out["started"].structuredContent
    assert out["status"].structuredContent["finished"] and out["status"].structuredContent["status"] == "finished"
    assert out["status"].structuredContent["next"] and isinstance(out["status"].structuredContent["recent"], list)
    compact = out["report"].structuredContent
    assert compact["ready"] and "report" not in compact and "No findings is not a pass" in compact["next"] and "coverage" in compact
    assert "No findings is not a pass" in out["full"].structuredContent["report"]
    assert out["sessions"].structuredContent["sessions"][0]["session_id"] == out["started"].structuredContent["session_id"]
    assert out["prod"].isError and "does not look like a test environment" in _text(out["prod"]) and "Ask the user" in _text(out["prod"])
    assert out["bad"].isError


def test_mcp_findings_detail_triage_issue_and_reverify(daemon) -> None:  # type: ignore[no-untyped-def]
    from tests.v2.test_mcp_shim import _text, call, mcp_session

    config, _ = daemon
    Ledger(config.paths.project_hercules_dir / "explore").record(
        Finding(
            id="f0a1b2c3d4e",
            title="Checkout total ignores the coupon",
            signature="invariant|/cart|total",
            oracle="invariant",
            tier="invariant",
            proposed_severity="P1",
            proposed_priority="P1",
            severity_reason="money shown wrongly",
            steps=['Open "/cart"', 'Apply the coupon "SAVE10"'],
            expected="the total drops by 10%",
            actual="the total is unchanged",
            run_ids=["r_20260101_000000_abc"],
            evidence=[".hercules/runs/r_20260101_000000_abc/steps/2/plain.jpg", "r_20260101_000000_abc/video.webm"],
            sessions=["x_20260101_000000_abcdef"],
        )
    )

    async def scenario() -> dict[str, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            return {
                "listed": await call(session, "hercules_explore_findings", {"session_id": "x_20260101_000000_abcdef"}),
                "other": await call(session, "hercules_explore_findings", {"case": "kept"}),
                "detail": await call(session, "hercules_explore_finding", {"finding_id": "f0a1b2"}),
                "missing": await call(session, "hercules_explore_finding", {"finding_id": "f9999"}),
                "unsaid": await call(session, "hercules_explore_triage", {"finding_id": "f0a1b2c3d4e", "by": "Ana", "user_said": "   ", "state": "confirmed"}),
                "refused": await call(session, "hercules_explore_triage", {"finding_id": "f0a1b2c3d4e", "by": "Ana", "user_said": "it is fixed", "state": "closed"}),
                "triaged": await call(
                    session, "hercules_explore_triage", {"finding_id": "f0a1b2c3d4e", "by": "Ana", "user_said": "yes, that's a real P0, confirm it", "state": "confirmed", "severity": "P0"}
                ),
                "issue": await call(session, "hercules_explore_issue", {"finding_id": "f0a1b2c3d4e", "tracker": "github", "repository": "acme/shop"}),
                "reverify": await call(session, "hercules_explore_reverify", {"wait_s": 0}),
                "stop_bad": await call(session, "hercules_explore_stop", {"session_id": "x_20260101_000000_abcdef"}),
            }

    out = asyncio.run(scenario())
    listed = out["listed"].structuredContent
    assert listed["total"] == 1 and listed["findings"][0]["severity_label"] == "P1 (proposed)" and listed["findings"][0]["ui_url"].endswith("finding=f0a1b2c3d4e")
    assert "expected: the total drops by 10%" in _text(out["listed"]) and out["other"].structuredContent["total"] == 0
    detail = out["detail"].structuredContent
    assert detail["id"] == "f0a1b2c3d4e" and detail["severity_reason"] == "money shown wrongly" and len(detail["steps"]) == 2
    uris = [link.get("uri") for link in detail["evidence"]]
    assert "hercules://runs/r_20260101_000000_abc/steps/2/plain.jpg" in uris and "hercules://runs/r_20260101_000000_abc/video.webm" in uris
    assert out["missing"].isError
    assert out["unsaid"].isError and "explicit decision" in _text(out["unsaid"])
    assert out["refused"].isError and "a new finding cannot move to closed" in _text(out["refused"])  # the ledger's words, verbatim
    triaged = out["triaged"].structuredContent
    assert triaged["finding"]["severity_label"] == "P0 (confirmed)" and triaged["audit"][0]["by"] == "Ana via coding agent"
    assert 'the user said: "yes, that\'s a real P0, confirm it"' in triaged["audit"][0]["reason"]
    assert out["issue"].structuredContent["url"].startswith("https://github.com/acme/shop/issues/new?") and "File it only if they ask" in _text(out["issue"])
    reverify = out["reverify"].structuredContent
    assert reverify["job_id"].startswith("rv_") and reverify["next"]
    assert out["stop_bad"].isError
