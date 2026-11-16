"""Daemon hardening: approvals bound to their question, private files for real values, case inputs that never outlive
their worker, bounded plan jobs, exclusive case saves, and bounded live-view bookkeeping."""

from __future__ import annotations

import asyncio
import logging
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.cases.models import TestCase
from testzeus_hercules.daemon import cases as case_files
from testzeus_hercules.daemon import manager as manager_module
from testzeus_hercules.daemon.config import TOKEN_ENV
from testzeus_hercules.daemon.hub import EventHub
from testzeus_hercules.daemon.manager import MAX_PLAN_JOBS, RunConflict, RunManager, RunNotFound
from testzeus_hercules.daemon.screencast import MAX_ENDED, ScreencastHub
from testzeus_hercules.daemon.store import RunStore
from tests.v2.fixtures.daemon_support import case_dict, client_for, isolated_env, make_config, submit, wait_for

SLEEPER = (sys.executable, "-c", "import time; time.sleep(60)")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _inputs(config: Any) -> Path:
    return config.paths.runs_dir.parent / "run-inputs"


# --------------------------------------------------------------------------- approvals


def test_an_answer_for_another_question_is_409_and_the_right_one_is_taken(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, test_case=case_dict("fake-approval", steps=4))
        view = wait_for(client, run_id, {"needs_approval"})
        question = view["approval"]["id"]
        stale = client.post(f"/api/runs/{run_id}/approve", json={"decision": "approve", "approval_id": question + "-old"})
        assert stale.status_code == 409 and question in stale.json()["error"]
        assert wait_for(client, run_id, {"needs_approval"})["approval"]["id"] == question  # still waiting: nothing was answered
        answered = client.post(f"/api/runs/{run_id}/approve", json={"decision": "approve", "approval_id": question})
        assert answered.status_code == 200, answered.text
        assert wait_for(client, run_id)["status"] == "passed"
        again = client.post(f"/api/runs/{run_id}/approve", json={"decision": "approve", "approval_id": question})
        assert again.status_code == 409


def test_a_draft_that_has_not_started_takes_no_question_id(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, test_case=case_dict(status="draft"))
        assert client.post(f"/api/runs/{run_id}/approve", json={"approval_id": "q1"}).status_code == 409
        assert client.post(f"/api/runs/{run_id}/approve", json={}).status_code == 200


# --------------------------------------------------------------------------- private files


def test_history_db_and_the_project_folder_are_private(tmp_path: Path) -> None:
    hercules = tmp_path / "project" / ".hercules"
    hercules.mkdir(parents=True)
    hercules.chmod(0o755)
    config = make_config(tmp_path)
    with client_for(config) as client:
        run_id = submit(client, test_case=case_dict())
        wait_for(client, run_id)
        db = config.paths.history_db
        assert _mode(config.paths.project_hercules_dir) == 0o700
        assert _mode(db) == 0o600
        for suffix in ("-wal", "-shm"):
            wal = db.with_name(db.name + suffix)
            if wal.exists():
                assert _mode(wal) == 0o600


def test_an_existing_world_readable_history_db_is_made_private(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    RunStore(db).close()
    db.chmod(0o644)
    RunStore(db).close()
    assert _mode(db) == 0o600


# --------------------------------------------------------------------------- case inputs


def _manager(config: Any) -> RunManager:
    return RunManager(config, RunStore(config.paths.history_db), EventHub())


def test_leftover_case_inputs_are_removed_when_the_daemon_starts(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    inputs = _inputs(config)
    inputs.mkdir(parents=True)
    (inputs / "r_old_000.json").write_text('{"password": "real"}', encoding="utf-8")
    (inputs / "p_old_000.plan.json").write_text("{}", encoding="utf-8")

    async def scenario() -> None:
        manager = _manager(config)
        await manager.start()
        await manager.stop()

    asyncio.run(scenario())
    assert list(inputs.iterdir()) == []


def test_the_case_input_is_removed_when_the_run_leaves_queued_during_launch(tmp_path: Path) -> None:
    config = make_config(tmp_path, worker_command=SLEEPER)

    async def scenario() -> tuple[str, Any]:
        manager = _manager(config)
        record = manager.submit(TestCase.model_validate(case_dict()), options={}, source={})
        real = manager.store.transition

        def cancelled_meanwhile(run_id: str, expected: Any, target: str, **fields: Any) -> bool:
            if target == "running":  # a cancel won the race while the worker was starting
                real(run_id, ("queued",), "cancelled", reason="cancelled before it started")
                return False
            return real(run_id, expected, target, **fields)

        manager.store.transition = cancelled_meanwhile  # type: ignore[method-assign]
        await manager._launch(record["id"])
        return record["id"], manager

    run_id, manager = asyncio.run(scenario())
    assert not manager._case_input(run_id).exists()
    assert manager._procs == {}


def test_the_case_input_is_removed_when_the_worker_cannot_start(tmp_path: Path) -> None:
    config = make_config(tmp_path, worker_command=(str(tmp_path / "no-such-worker"),))
    with client_for(config) as client:
        run_id = submit(client, test_case=case_dict())
        view = wait_for(client, run_id)
    assert view["status"] == "error" and "could not start the worker" in view["reason"]
    assert list(_inputs(config).iterdir()) == []


def test_workers_do_not_get_the_daemon_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path, env=isolated_env(tmp_path, **{TOKEN_ENV: "x" * 40, "MODEL_KEY": "kept"}))
    seen: list[dict[str, str]] = []

    async def spawn(*args: Any, env: dict[str, str], **kwargs: Any) -> Any:
        seen.append(env)
        raise OSError("not starting a worker in this test")

    monkeypatch.setattr(manager_module.asyncio, "create_subprocess_exec", spawn)

    async def scenario() -> None:
        manager = _manager(config)
        record = manager.submit(TestCase.model_validate(case_dict()), options={}, source={})
        with pytest.raises(OSError):
            await manager._launch(record["id"])
        with pytest.raises(OSError):
            await manager.plan_job(TestCase.model_validate(case_dict()), {})

    asyncio.run(scenario())
    assert len(seen) == 2
    for env in seen:
        assert TOKEN_ENV not in env and env["MODEL_KEY"] == "kept"
    assert list(_inputs(config).iterdir()) == []


# --------------------------------------------------------------------------- plan jobs


def test_a_cancelled_plan_job_kills_its_worker_and_removes_its_files(tmp_path: Path) -> None:
    config = make_config(tmp_path, worker_command=SLEEPER)

    async def scenario() -> tuple[int, bool]:
        manager = _manager(config)
        job = asyncio.create_task(manager.plan_job(TestCase.model_validate(case_dict()), {}))
        deadline = time.monotonic() + 10
        while not manager._plan_procs and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        (proc,) = manager._plan_procs
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        code = await asyncio.wait_for(proc.wait(), 5)
        return code, bool(manager._plan_procs)

    code, left = asyncio.run(scenario())
    assert code < 0 and not left
    assert list(_inputs(config).iterdir()) == []


def test_plan_jobs_run_at_most_two_at_once_and_stop_kills_them(tmp_path: Path) -> None:
    config = make_config(tmp_path, worker_command=SLEEPER)

    async def scenario() -> tuple[int, list[Any]]:
        manager = _manager(config)
        await manager.start()
        jobs = [asyncio.create_task(manager.plan_job(TestCase.model_validate(case_dict()), {})) for _ in range(MAX_PLAN_JOBS + 1)]
        deadline = time.monotonic() + 10
        while len(manager._plan_procs) < MAX_PLAN_JOBS and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)
        running = len(manager._plan_procs)
        procs = list(manager._plan_procs)
        await manager.stop()
        for proc in procs:
            await asyncio.wait_for(proc.wait(), 5)
        for job in jobs:
            job.cancel()
        return running, await asyncio.gather(*jobs, return_exceptions=True)

    running, outcomes = asyncio.run(scenario())
    assert running == MAX_PLAN_JOBS
    assert all(isinstance(outcome, (RunConflict, asyncio.CancelledError)) for outcome in outcomes)


# --------------------------------------------------------------------------- manager errors


def test_unknown_runs_raise_a_clear_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = _manager(make_config(tmp_path))
        for call in (manager.cancel, manager.approve, manager.resume):
            with pytest.raises(RunNotFound, match="r_missing_000 not found"):
                call("r_missing_000")

    asyncio.run(scenario())


def test_cancel_never_labels_a_run_it_could_not_stop(tmp_path: Path) -> None:
    async def scenario() -> dict[str, str]:
        manager = _manager(make_config(tmp_path))
        record = manager.submit(TestCase.model_validate(case_dict()), options={}, source={})
        manager.store.transition(record["id"], ("queued",), "running")  # started, but not this manager's worker
        with pytest.raises(RunConflict, match="changed state"):
            manager.cancel(record["id"])
        return manager._cancel_reason

    assert asyncio.run(scenario()) == {}


def test_a_browser_that_cannot_be_stopped_is_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def stuck(run_dir: Path) -> None:
        raise RuntimeError("cannot signal it")

    monkeypatch.setattr(manager_module, "stop_browser", stuck)
    with caplog.at_level(logging.WARNING, logger=manager_module.__name__):
        with client_for(make_config(tmp_path)) as client:
            run_id = submit(client, test_case=case_dict())
            assert wait_for(client, run_id)["status"] == "passed"
    assert any(run_id in record.getMessage() and record.exc_info for record in caplog.records)


# --------------------------------------------------------------------------- cases and live view


def test_saving_a_new_case_never_replaces_one_created_meanwhile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "login.json"
    path.write_text("theirs", encoding="utf-8")
    monkeypatch.setattr(Path, "exists", lambda self: False if self == path else os.path.exists(self))  # the check ran before it appeared
    with pytest.raises(case_files.CaseExists):
        case_files._write_new(path, "ours", overwrite=False)
    assert path.read_text(encoding="utf-8") == "theirs"
    assert [item.name for item in tmp_path.iterdir()] == ["login.json"]
    case_files._write_new(path, "ours", overwrite=True)
    assert path.read_text(encoding="utf-8") == "ours"


def test_the_live_view_forgets_old_runs() -> None:
    hub = ScreencastHub()
    for number in range(MAX_ENDED + 50):
        run_id = f"r_{number}"
        hub.publish(run_id, b"\xff\xd8", 1, 1, 0.0)
        hub.end(run_id, "passed")
    assert len(hub._ended) == MAX_ENDED and hub._counts == {}
    assert "r_0" not in hub._ended and f"r_{MAX_ENDED + 49}" in hub._ended
