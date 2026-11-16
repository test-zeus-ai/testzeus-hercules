"""Plans through the daemon and MCP: plan review (needs_plan_approval: approve, edit, reject), plan jobs, plan endpoints,
diffs, drafts asking for their plan, worker flags, and steps_done counting finished steps."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.daemon.manager import RunManager

from tests.v2.fixtures.daemon_support import case_dict, client_for, daemon_thread, make_config, submit, wait_for
from tests.v2.test_mcp_shim import _text, call, mcp_session


def _run_dir(config: Any, run_id: str) -> Path:
    return config.settings.project_dir / ".hercules" / "runs" / run_id


def _waiting_plan(client: Any) -> tuple[str, dict[str, Any]]:
    run_id = submit(client, test_case=case_dict(), plan_review="ask")
    return run_id, wait_for(client, run_id, {"needs_plan_approval"})


def test_a_run_with_plan_review_ask_waits_for_its_plan(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        run_id, body = _waiting_plan(client)
        plan = client.get(f"/api/runs/{run_id}/plan")
        approved = client.post(f"/api/runs/{run_id}/approve", json={"decision": "approve"})
        final = wait_for(client, run_id)

    assert body["approval"]["kind"] == "plan" and body["approval"]["step"] == 0 and not body["finished"]
    assert plan.status_code == 200 and plan.json()["plan"]["case_id"] == "demo-login"
    assert plan.json()["lint_warnings"] == 1 and "1.1 click" in plan.json()["summary"]
    assert approved.status_code == 200 and approved.json()["status"] != "needs_plan_approval"
    assert final["status"] == "passed"
    args = json.loads((_run_dir(config, run_id) / "worker_args.json").read_text())
    assert args[args.index("--plan-review") + 1] == "ask"


def test_a_plan_is_edited_or_rejected_through_the_approval(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        run_id, _ = _waiting_plan(client)
        plan = client.get(f"/api/runs/{run_id}/plan").json()["plan"]
        plan["steps"][0]["text"] = 'click "the first step, edited"'
        other = {**plan, "case_id": "another-case"}
        refused = client.post(f"/api/runs/{run_id}/approve", json={"decision": "edit", "plan": other})
        no_plan = client.post(f"/api/runs/{run_id}/approve", json={"decision": "edit"})
        edited = client.post(f"/api/runs/{run_id}/approve", json={"decision": "edit", "plan": plan})
        assert wait_for(client, run_id)["status"] == "passed"

        rejected_id, _ = _waiting_plan(client)
        client.post(f"/api/runs/{rejected_id}/approve", json={"decision": "reject"})
        rejected = wait_for(client, rejected_id)

    assert refused.status_code == 409 and "another-case" in refused.json()["error"]
    assert no_plan.status_code == 422
    assert edited.status_code == 200
    seen = json.loads((_run_dir(config, run_id) / "plan_edit_seen.json").read_text())
    assert seen[0] == 'click "the first step, edited"'
    assert rejected["status"] == "cancelled"


def test_a_drafted_case_asks_for_its_plan_after_its_steps(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        response = client.post("/api/runs", json={"description": "Open /login.html\nSign in as demo", "base_url": "http://127.0.0.1:9"})
        run_id = response.json()["run_id"]
        client.post(f"/api/runs/{run_id}/approve")
        body = wait_for(client, run_id, {"needs_plan_approval"})
        client.post(f"/api/runs/{run_id}/approve")
        final = wait_for(client, run_id)
        explicit = submit(client, description="Open /login.html", plan_review="auto", auto_approve=True, base_url="http://127.0.0.1:9")
        assert wait_for(client, explicit)["status"] == "passed", "a draft run with plan_review auto does not ask"

    assert body["approval"]["kind"] == "plan"
    assert final["status"] == "passed"


def test_strict_and_a_given_plan_reach_the_worker(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        made = client.post("/api/plans", json={"test_case": case_dict()}).json()["plan"]
        run_id = submit(client, test_case=case_dict(), strict=True, plan=made)
        wait_for(client, run_id)
        bad = client.post("/api/runs", json={"test_case": case_dict(), "plan": {"case_id": "x"}})
    args = json.loads((_run_dir(config, run_id) / "worker_args.json").read_text())
    assert "--strict" in args
    assert Path(args[args.index("--plan") + 1]).name == "plan.given.json"
    assert json.loads((_run_dir(config, run_id) / "plan.given.json").read_text())["case_id"] == "demo-login"
    assert bad.status_code == 422


def test_plans_are_made_without_running_and_read_back(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        saved = client.post("/api/cases", json={"test_case": case_dict()})
        missing = client.get("/api/cases/demo-login.json/plan")
        made = client.post("/api/plans", json={"test_case": case_dict(), "approve": True})
        failing = client.post("/api/plans", json={"test_case": case_dict("fake-plan-fail")})
        neither = client.post("/api/plans", json={})
        latest = client.get("/api/cases/demo-login.json/plan")
    store = config.settings.project_dir / ".hercules" / "plans"

    assert made.status_code == 200, made.text
    body = made.json()
    assert body["approved"] and body["plan"]["review"]["state"] == "approved" and body["plan"]["review"]["by"] == "agent"
    assert (body["lint_errors"], body["lint_warnings"]) == (0, 1)
    assert list(store.glob("approved-*.json")), "an approved plan is stored for its case version and model"
    assert failing.status_code == 409 and "model server is not reachable" in failing.json()["error"]
    assert neither.status_code == 422
    assert saved.status_code == 201 and missing.status_code == 404
    assert latest.status_code == 200 and latest.json()["plan"]["case_id"] == "demo-login" and latest.json()["current"] is True


def test_plan_diffs_between_runs_and_against_the_runs_amendments(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        first, _ = _waiting_plan(client)
        client.post(f"/api/runs/{first}/approve")
        wait_for(client, first)
        second, _ = _waiting_plan(client)
        plan = json.loads((_run_dir(config, second) / "plan.json").read_text())
        plan["steps"][1]["text"] = "changed"
        plan["amendments"] = [{"step": 2, "kind": "reread", "before": "a", "after": 'click "Other"', "reason": "no such button", "source": "model"}]
        (_run_dir(config, second) / "plan.json").write_text(json.dumps(plan))
        between = client.get(f"/api/runs/{second}/plan/diff", params={"against": first}).json()
        amended = client.get(f"/api/runs/{second}/plan/diff").json()
        unknown = client.get(f"/api/runs/{second}/plan/diff", params={"against": "r_nope"})
        client.post(f"/api/runs/{second}/cancel")

    assert [item["id"] for item in between["diff"]["changed"]] == ["2.1"]
    assert amended["amendments"][0]["kind"] == "reread" and [item["text"] for item in amended["diff"]["added"]] == ['reread: click "Other"']
    assert unknown.status_code == 404


def test_the_openapi_lists_the_plan_routes(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        spec = client.get("/api/openapi.json").json()
    assert {"/api/plans", "/api/runs/{run_id}/plan", "/api/runs/{run_id}/plan/diff", "/api/cases/{name}/plan"} <= set(spec["paths"])
    assert {"PlanCreate", "PlanView"} <= set(spec["components"]["schemas"])
    assert "needs_plan_approval" in spec["components"]["schemas"]["RunView"]["properties"]["status"]["enum"]


def test_steps_done_counts_the_steps_that_finished_not_the_highest_number(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    async def scenario() -> int:
        from testzeus_hercules.daemon.hub import EventHub
        from testzeus_hercules.daemon.store import RunStore
        from testzeus_hercules.cases.models import TestCase

        store = RunStore(config.paths.project_hercules_dir / "history.db")
        manager = RunManager(config, store, EventHub())
        record = manager.submit(TestCase.model_validate(case_dict(steps=4)), options={}, source={})
        for step in (1, 3, 3, 2):  # step 3 ran before step 2 (a field it depends on), and step 3 finished twice (a retry)
            manager._record(record["id"], "step_finished", {"step": step, "status": "passed"})
        if step == 2:
            done = store.get_run(record["id"])["steps_done"]
        return int(done)

    assert asyncio.run(scenario()) == 3


@pytest.fixture()
def daemon(tmp_path: Path):  # type: ignore[no-untyped-def]
    config = make_config(tmp_path, cancel_grace_s=0.5)
    with daemon_thread(config) as info:
        yield config, info


def test_mcp_plan_tool_makes_reads_and_diffs_plans(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon

    async def scenario() -> dict[str, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            made = await call(session, "hercules_plan", {"test_cases": case_dict(), "approve": True})
            started = await call(session, "hercules_run", {"test_cases": case_dict(), "plan_review": "ask"})
            run_id = started.structuredContent["runs"][0]["run_id"]
            waiting = await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 20})
            read = await call(session, "hercules_plan", {"run_id": run_id})
            plan = read.structuredContent["plan"]
            plan["steps"][0]["text"] = "edited by the agent"
            approved = await call(session, "hercules_approve", {"run_id": run_id, "decision": "edit", "plan": plan})
            final = await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 30})
            neither = await call(session, "hercules_plan", {})
            return {"made": made, "waiting": waiting, "read": read, "approved": approved, "final": final, "neither": neither, "run_id": run_id}

    out = asyncio.run(scenario())
    assert out["made"].structuredContent["approved"] and out["made"].structuredContent["lint_warnings"] == 1
    assert "Run it with hercules_run" in _text(out["made"])
    assert out["waiting"].structuredContent["status"] == "needs_plan_approval"
    assert "hercules_plan (run_id" in _text(out["waiting"]) and "edit with plan" in _text(out["waiting"])
    assert out["read"].structuredContent["diff"] is not None
    assert out["final"].structuredContent["status"] == "passed"
    assert json.loads((_run_dir(config, out["run_id"]) / "plan_edit_seen.json").read_text())[0] == "edited by the agent"
    assert out["neither"].isError and "give exactly one of" in _text(out["neither"])
