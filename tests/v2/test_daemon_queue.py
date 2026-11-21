"""Run queue lifecycle with a fake worker: pass, fail, crash, cancel, rerun, approval, concurrency, restart."""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import pytest

from testzeus_hercules.daemon.store import RunStore
from tests.v2.fixtures.daemon_support import case_dict, client_for, make_config, submit, wait_for


def test_passing_run_goes_queued_running_passed(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        run_id = submit(client)
        body = wait_for(client, run_id)
        events = [event["kind"] for event in client.get(f"/api/runs/{run_id}/events/list").json()["events"]]

    assert body["status"] == "passed"
    assert body["result"]["status"] == "passed"
    assert body["result"]["ui_url"].endswith(f"/runs/{run_id}")
    assert body["total_steps"] == 3 and body["steps_done"] == 3
    assert body["started_at"] and body["finished_at"]
    statuses = [kind for kind in events if kind == "status"]
    assert events[0] == "status" and statuses
    for kind in ("run_started", "step_started", "action", "step_finished", "run_finished"):
        assert kind in events
    assert (config.settings.project_dir / ".hercules" / "runs" / run_id / "case.json").is_file()


def test_failed_run_keeps_the_result_and_evidence(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, "fake-fail")
        body = wait_for(client, run_id)

    assert body["status"] == "failed"
    step = body["result"]["steps"][1]
    assert step["status"] == "failed"
    assert f"hercules://runs/{run_id}/steps/2/plain.jpg" in step["evidence"]


def test_crashed_worker_marks_the_run_error_with_a_reason(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, "fake-crash")
        body = wait_for(client, run_id)

    assert body["status"] == "error"
    assert "exited with code 3" in body["reason"]
    assert body["result"] is None


def test_worker_error_event_becomes_the_reason(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, "fake-error-event")
        body = wait_for(client, run_id)

    assert body["status"] == "error"
    assert "model server is not reachable" in body["reason"]


def test_cancel_running_run_terminates_the_process_group(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path, cancel_grace_s=0.5)) as client:
        run_id = submit(client, "fake-hang")
        running = wait_for(client, run_id, {"running"})
        deadline = time.monotonic() + 10
        while client.get(f"/api/runs/{run_id}").json().get("current_step") is None and time.monotonic() < deadline:
            time.sleep(0.05)
        pid = client.get(f"/api/runs/{run_id}").json()["worker_pid"]
        assert pid

        response = client.post(f"/api/runs/{run_id}/cancel")
        body = wait_for(client, run_id)

    assert running["status"] == "running"
    assert response.status_code == 200
    assert body["status"] == "cancelled"
    assert "cancel" in body["reason"]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cancel_queued_run_and_finished_run(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        first = submit(client, "fake-hang")
        second = submit(client)
        assert client.get(f"/api/runs/{second}").json()["status"] == "queued"

        assert client.post(f"/api/runs/{second}/cancel").status_code == 200
        assert client.get(f"/api/runs/{second}").json()["status"] == "cancelled"
        assert client.post(f"/api/runs/{second}/cancel").status_code == 409
        client.post(f"/api/runs/{first}/cancel")
        wait_for(client, first)


def test_concurrency_is_one_by_default(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        first = submit(client, "fake-slow")
        second = submit(client, "fake-slow")
        wait_for(client, first, {"running"})
        assert client.get(f"/api/runs/{second}").json()["status"] == "queued"
        wait_for(client, first)
        assert wait_for(client, second)["status"] == "passed"


def test_parallel_runs_setting_allows_two(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path, parallel_runs=2)) as client:
        first = submit(client, "fake-hang")
        second = submit(client, "fake-hang")
        wait_for(client, first, {"running"})
        wait_for(client, second, {"running"})
        client.post(f"/api/runs/{first}/cancel")
        client.post(f"/api/runs/{second}/cancel")
        wait_for(client, first)
        wait_for(client, second)


def test_rerun_whole_and_failed_only(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        passed = submit(client)
        failed = submit(client, "fake-fail")
        wait_for(client, passed)
        wait_for(client, failed)

        nothing = client.post(f"/api/runs/{passed}/rerun", json={"scope": "failed_only"})
        whole = client.post(f"/api/runs/{passed}/rerun", json={"scope": "whole"})
        again = client.post(f"/api/runs/{failed}/rerun", json={"scope": "failed_only"})
        bad = client.post(f"/api/runs/{failed}/rerun", json={"scope": "everything"})

        assert nothing.status_code == 409
        assert whole.status_code == 201 and again.status_code == 201
        assert bad.status_code == 422
        rerun = wait_for(client, again.json()["run_id"])
        assert rerun["parent_run_id"] == failed
        assert rerun["case_id"] == "demo-login"
        assert wait_for(client, whole.json()["run_id"])["status"] == "passed"


def test_rerun_of_a_case_path_reads_the_file_again(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    case_file = config.settings.project_dir / ".hercules" / "cases" / "login.json"
    case_file.write_text(json.dumps(case_dict("fake-fail")))
    with client_for(config) as client:
        first = submit(client, case_path=".hercules/cases/login.json")
        assert wait_for(client, first)["status"] == "failed"
        case_file.write_text(json.dumps(case_dict()))
        second = client.post(f"/api/runs/{first}/rerun", json={"scope": "failed_only"}).json()["run_id"]
        assert wait_for(client, second)["status"] == "passed"


def test_draft_case_waits_for_approval(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, test_case=case_dict(status="draft"))
        time.sleep(0.3)
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "needs_approval"

        approved = client.post(f"/api/runs/{run_id}/approve")
        assert approved.status_code == 200
        assert wait_for(client, run_id, {"needs_plan_approval"})["approval"]["kind"] == "plan", "a drafted case's plan is reviewed too"
        assert client.post(f"/api/runs/{run_id}/approve").status_code == 200
        assert wait_for(client, run_id)["status"] == "passed"
        assert client.post(f"/api/runs/{run_id}/approve").status_code == 409


def test_description_becomes_a_draft(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        response = client.post("/api/runs", json={"description": "Open /login.html\nSign in as demo\nCheck the welcome text", "base_url": "http://127.0.0.1:9"})
        body = client.get(f"/api/runs/{response.json()['run_id']}").json()
    assert response.status_code == 201
    assert body["status"] == "needs_approval"
    assert body["total_steps"] == 3


def test_worker_gets_the_run_flags(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        run_id = submit(client, test_case=case_dict(), mode="pure", egress="hybrid-text", decider="stub", max_steps=2)
        heuristic = client.post("/api/runs", json={"test_case": case_dict(), "decider": "heuristic"})
        rules_only = client.post("/api/runs", json={"test_case": case_dict(), "llm_interpreter": False})
        wait_for(client, run_id)
    args = json.loads((config.settings.project_dir / ".hercules" / "runs" / run_id / "worker_args.json").read_text())
    assert args[args.index("--mode") + 1] == "pure"
    assert args[args.index("--egress") + 1] == "hybrid-text"
    assert args[args.index("--decider") + 1] == "stub"
    assert heuristic.status_code == 422 and rules_only.status_code == 422, "no coded decider or rules-only reading can be asked for"
    assert args[args.index("--max-steps") + 1] == "2"


def test_list_runs_filters_by_status(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        ids = [submit(client), submit(client, "fake-fail")]
        for run_id in ids:
            wait_for(client, run_id)
        everything = client.get("/api/runs").json()["runs"]
        failed = client.get("/api/runs?status=failed").json()["runs"]
        limited = client.get("/api/runs?limit=1").json()["runs"]
        bad = client.get("/api/runs?status=bogus")
        too_many = client.get("/api/runs?limit=100000")

    assert {run["run_id"] for run in everything} == set(ids)
    assert [run["run_id"] for run in failed] == [ids[1]]
    assert len(limited) == 1 and limited[0]["run_id"] == ids[1]
    assert bad.status_code == 422 and too_many.status_code == 422


def test_unknown_run_is_404(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        assert client.get("/api/runs/r_missing").status_code == 404
        assert client.post("/api/runs/r_missing/cancel").status_code == 404
        assert client.get("/api/runs/..%2f..%2fetc").status_code in (404, 422)


def test_restart_requeues_queued_and_fails_interrupted_runs(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db = config.settings.project_dir / ".hercules" / "history.db"
    store = RunStore(db)
    store.create_run("r_old_running", case=case_dict(), options={}, status="running")
    store.create_run("r_old_queued", case=case_dict(), options={}, status="queued")
    store.close()

    with client_for(config) as client:
        interrupted = client.get("/api/runs/r_old_running").json()
        requeued = wait_for(client, "r_old_queued")

    assert interrupted["status"] == "error"
    assert "daemon stopped" in interrupted["reason"]
    assert requeued["status"] == "passed"


def test_shutdown_terminates_running_workers(tmp_path: Path) -> None:
    config = make_config(tmp_path, cancel_grace_s=0.5)
    with client_for(config) as client:
        run_id = submit(client, "fake-hang")
        wait_for(client, run_id, {"running"})
        deadline = time.monotonic() + 10
        pid = None
        while not pid and time.monotonic() < deadline:
            pid = client.get(f"/api/runs/{run_id}").json()["worker_pid"]
            time.sleep(0.05)
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    store = RunStore(config.settings.project_dir / ".hercules" / "history.db")
    assert store.get_run(run_id)["status"] == "cancelled"
    store.close()


def test_a_killed_workers_browser_is_stopped_by_the_daemon(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    browser_pid = None
    try:
        with client_for(config) as client:
            run_id = submit(client, "fake-hang", "fake-browser")
            wait_for(client, run_id, {"running"})
            record = config.settings.project_dir / ".hercules" / "runs" / run_id / "browser.json"
            deadline = time.monotonic() + 10
            while not (record.is_file() and record.read_text()) and time.monotonic() < deadline:
                time.sleep(0.05)
            browser_pid = json.loads(record.read_text())["pid"]
            worker_pid = client.get(f"/api/runs/{run_id}").json()["worker_pid"]
            os.killpg(worker_pid, signal.SIGKILL)  # the worker's group dies; the browser has its own group
            body = wait_for(client, run_id)

        assert body["status"] == "error"
        deadline = time.monotonic() + 10
        while _alive(browser_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(browser_pid), "the daemon stops the browser a dead worker left running"
        assert not record.exists()
    finally:
        if browser_pid is not None and _alive(browser_pid):
            os.killpg(browser_pid, signal.SIGKILL)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_signal_kill_of_worker_is_reported(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, "fake-hang")
        wait_for(client, run_id, {"running"})
        deadline = time.monotonic() + 10
        pid = None
        while not pid and time.monotonic() < deadline:
            pid = client.get(f"/api/runs/{run_id}").json()["worker_pid"]
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        body = wait_for(client, run_id)
    assert body["status"] == "error"
    assert "SIGKILL" in body["reason"]
