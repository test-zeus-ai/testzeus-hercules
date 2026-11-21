"""Debugging runs through the daemon: control, approvals (approve/reject/edit), input relay, resume, and the screencast WebSocket."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from testzeus_hercules.engine.live import FRAME_HEADER
from tests.v2.fixtures.daemon_support import case_dict, client_for, make_config, submit, wait_for

WS = "ws://127.0.0.1:7373"


def events_of(client: Any, run_id: str) -> list[dict[str, Any]]:
    return client.get(f"/api/runs/{run_id}/events/list").json()["events"]


def wait_event(client: Any, run_id: str, kind: str, timeout_s: float = 10.0, **match: Any) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for event in events_of(client, run_id):
            if event["kind"] == kind and all(event["data"].get(key) == value for key, value in match.items()):
                return event
        time.sleep(0.05)
    raise AssertionError(f"no {kind} event {match}: {[event['kind'] for event in events_of(client, run_id)]}")


def control(client: Any, run_id: str, action: str) -> Any:
    return client.post(f"/api/runs/{run_id}/control", json={"action": action})


def test_pause_step_and_resume_move_the_run_between_states(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, test_case=case_dict("fake-control", steps=4))
        wait_for(client, run_id, {"running"})
        assert control(client, run_id, "pause").status_code == 202
        body = wait_for(client, run_id, {"paused"})
        assert body["paused"]["where"] == "step" and body["paused"]["step"] >= 1
        assert "PAUSED" in body["summary"]
        paused_at = body["paused"]["step"]
        assert control(client, run_id, "step").status_code == 202
        wait_event(client, run_id, "paused", step=paused_at + 1)
        assert wait_for(client, run_id, {"paused"})["paused"]["step"] == paused_at + 1
        assert control(client, run_id, "resume").status_code == 202
        final = wait_for(client, run_id)
        kinds = [event["kind"] for event in events_of(client, run_id)]
        statuses = [event["data"]["status"] for event in events_of(client, run_id) if event["kind"] == "status"]

    assert final["status"] == "passed"
    assert "paused" in kinds and "resumed" in kinds
    assert statuses[:2] == ["queued", "running"] and "paused" in statuses and statuses[-1] == "passed"


def test_skip_while_paused_and_conflicts(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, test_case=case_dict("fake-control", steps=4))
        wait_for(client, run_id, {"running"})
        assert control(client, run_id, "skip").status_code == 409  # only while paused
        assert control(client, run_id, "explode").status_code == 422
        control(client, run_id, "pause")
        wait_for(client, run_id, {"paused"})
        assert control(client, run_id, "skip").status_code == 202
        control(client, run_id, "resume")
        final = wait_for(client, run_id)
        assert control(client, run_id, "pause").status_code == 409  # finished

    assert final["status"] == "failed"
    assert "skipped" in [step["status"] for step in final["result"]["steps"]]


def test_needs_approval_edit_replaces_the_step(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, test_case=case_dict("fake-approval", steps=4))
        body = wait_for(client, run_id, {"needs_approval"})
        assert body["approval"]["kind"] == "destructive" and body["approval"]["step"] == 2
        assert "Delete" in body["approval"]["reason"] and body["draft_steps"] is None
        bad = client.post(f"/api/runs/{run_id}/approve", json={"decision": "edit"})
        assert bad.status_code == 422 and "step_text" in bad.json()["error"]
        answered = client.post(f"/api/runs/{run_id}/approve", json={"decision": "edit", "step_text": 'Click "Keep"'})
        assert answered.status_code == 200 and answered.json()["status"] != "needs_approval"  # the worker's answer is awaited briefly
        final = wait_for(client, run_id)

    assert final["status"] == "passed"
    assert final["result"]["steps"][1]["action"] == 'Click "Keep"'


def test_needs_approval_reject_blocks_the_step(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, test_case=case_dict("fake-approval", steps=4))
        wait_for(client, run_id, {"needs_approval"})
        client.post(f"/api/runs/{run_id}/approve", json={"decision": "reject"})
        final = wait_for(client, run_id)
        statuses = [event["data"]["status"] for event in events_of(client, run_id) if event["kind"] == "status"]

    assert final["status"] == "failed"
    assert final["result"]["steps"][1]["status"] == "blocked"
    assert "needs_approval" in statuses and statuses[statuses.index("needs_approval") + 1] == "running"


def test_draft_reject_cancels_and_edit_is_refused(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, test_case=case_dict(status="draft"))
        assert client.post(f"/api/runs/{run_id}/approve", json={"decision": "edit", "step_text": "x"}).status_code == 409
        body = client.post(f"/api/runs/{run_id}/approve", json={"decision": "reject"}).json()
        assert body["status"] == "cancelled"
        other = submit(client, test_case=case_dict(status="draft"))
        assert client.post(f"/api/runs/{other}/approve").json()["status"] in ("queued", "running", "passed")  # an empty body still approves


def test_input_is_relayed_only_while_paused(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, test_case=case_dict("fake-control", steps=4))
        wait_for(client, run_id, {"running"})
        click = {"kind": "click", "x": 0.5, "y": 0.25}
        assert client.post(f"/api/runs/{run_id}/input", json=click).status_code == 409
        assert client.post(f"/api/runs/{run_id}/input", json={"kind": "click", "x": 2, "y": 0}).status_code == 422
        assert client.post(f"/api/runs/{run_id}/input", json={"kind": "type"}).status_code == 422
        control(client, run_id, "pause")
        wait_for(client, run_id, {"paused"})
        assert client.post(f"/api/runs/{run_id}/input", json=click).status_code == 202
        result = wait_event(client, run_id, "input_result")
        control(client, run_id, "resume")
        wait_for(client, run_id)

    assert result["data"]["ok"] is True


def test_worker_gets_control_and_human_flags(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        run_id = submit(client, test_case=case_dict(), confirm_destructive=True, destructive_terms=["archive", "close"], ask_on_failure=True, approval_timeout_s=30)
        wait_for(client, run_id)
        bad = client.post("/api/runs", json={"test_case": case_dict(), "destructive_terms": ["a,b"]})
    args = json.loads((config.settings.project_dir / ".hercules" / "runs" / run_id / "worker_args.json").read_text())
    assert "--control" in args and "--confirm-destructive" in args and "--ask-on-failure" in args
    assert args[args.index("--destructive-terms") + 1] == "archive,close"
    assert args[args.index("--approval-timeout-s") + 1] == "30.0"
    assert "--no-llm-interpreter" not in args
    assert bad.status_code == 422


def test_resume_requeues_a_run_whose_worker_died(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        run_id = submit(client, "fake-crash")
        body = wait_for(client, run_id)
        assert body["status"] == "error" and body["resumable"] is False
        assert client.post(f"/api/runs/{run_id}/resume").status_code == 409  # no checkpoint
        checkpoints = config.settings.project_dir / ".hercules" / "checkpoints"
        checkpoints.mkdir(parents=True, exist_ok=True)
        (checkpoints / f"{run_id}.db").write_bytes(b"")
        assert client.get(f"/api/runs/{run_id}").json()["resumable"] is True
        response = client.post(f"/api/runs/{run_id}/resume")
        assert response.status_code == 200, response.text
        wait_for(client, run_id, {"running", "error", "passed", "failed"})
        wait_for(client, run_id)
        passed = submit(client)
        wait_for(client, passed)
        assert client.post(f"/api/runs/{passed}/resume").status_code == 409
    args = json.loads((config.settings.project_dir / ".hercules" / "runs" / run_id / "worker_args.json").read_text())
    assert args[args.index("--resume") + 1] == run_id


# --------------------------------------------------------------------------- screencast


def test_screencast_streams_frames_while_watched_and_ends_with_the_run(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        run_id = submit(client, test_case=case_dict("fake-frames", "fake-control", steps=6))
        wait_for(client, run_id, {"running"})
        control(client, run_id, "pause")  # keep the run alive while watching
        wait_for(client, run_id, {"paused"})
        frames: list[tuple[int, int, int]] = []
        started = time.monotonic()
        with client.websocket_connect(f"{WS}/api/runs/{run_id}/screencast") as ws:
            while len(frames) < 5:
                message = ws.receive()
                if message.get("bytes"):
                    data = message["bytes"]
                    length, width, height, _ = FRAME_HEADER.unpack(data[: FRAME_HEADER.size])
                    assert data[FRAME_HEADER.size : FRAME_HEADER.size + 2] == b"\xff\xd8" and len(data) == FRAME_HEADER.size + length
                    frames.append((length, width, height))
            elapsed = time.monotonic() - started
            control(client, run_id, "resume")
            ending: dict[str, Any] = {}
            while not ending:
                message = ws.receive()
                if message.get("text"):
                    payload = json.loads(message["text"])
                    if payload["type"] == "end":
                        ending = payload
        assert ending["status"] == "passed"
        with client.websocket_connect(f"{WS}/api/runs/{run_id}/screencast") as ws:
            assert json.loads(ws.receive_text()) == {"type": "end", "status": "passed"}

    assert frames[0][1:] == (1600, 1000)
    assert len(frames) / elapsed > 3  # the fake worker sends ~10 frames per second


def test_screencast_websocket_needs_auth_and_a_loopback_origin(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        run_id = submit(client, test_case=case_dict("fake-control", steps=4))
        control(client, run_id, "pause")
        with client_for(config, auth=False) as anonymous:
            with pytest.raises(WebSocketDisconnect):
                with anonymous.websocket_connect(f"{WS}/api/runs/{run_id}/screencast") as ws:
                    ws.receive()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"{WS}/api/runs/{run_id}/screencast", headers={"Origin": "http://evil.example"}) as ws:
                ws.receive()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"{WS}/api/runs/r_unknown_000/screencast") as ws:
                ws.receive()
        control(client, run_id, "resume")
        wait_for(client, run_id)
