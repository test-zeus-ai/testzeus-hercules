"""The daemon's HTTP API (Starlette): runs, events (SSE), evidence, cases, environments, models.

Plans: ``POST /api/plans`` makes a case's plan without running it, ``GET /api/runs/{id}/plan`` is the plan a run used
(criteria results, amendments, lint), ``GET /api/runs/{id}/plan/diff`` compares it with another run's (``against``) or
with what the run changed, and ``GET /api/cases/{name}/plan`` is the latest plan of a saved case. A run with
``plan_review: ask`` waits in ``needs_plan_approval``; ``POST /api/runs/{id}/approve`` answers it (``edit`` with ``plan``).

Debugging a running run: ``POST /api/runs/{id}/control`` (pause, resume, step, skip,
retry), ``POST /api/runs/{id}/approve`` (approve, reject or edit), ``POST
/api/runs/{id}/input`` (drive a paused run's browser), ``POST /api/runs/{id}/resume``
(continue a run whose worker died) and the live view at ``GET /api/runs/{id}/screencast``
(WebSocket; binary messages are frames, text messages ``{"type": "end"}``).

Every route sits behind :class:`~testzeus_hercules.daemon.security.GuardMiddleware`
(token or session cookie, Host and Origin checks). Request bodies are validated
with pydantic; error responses name the problem without echoing secrets.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AbstractSet, Any, AsyncIterator, Awaitable, Callable, Literal

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from testzeus_hercules.act.redact import mask_url_credentials
from testzeus_hercules.cases.models import TestCase, placeholder_names, set_environment_names
from testzeus_hercules.cli.version import package_version
from testzeus_hercules.clouds.runner import CloudRuns
from testzeus_hercules.daemon import cases as case_files
from testzeus_hercules.daemon import clouds as cloud_api
from testzeus_hercules.daemon import files as files_api
from testzeus_hercules.daemon import secrets as secrets_api
from testzeus_hercules.daemon.config import DaemonConfig
from testzeus_hercules.daemon.decisions import read_step_decisions
from testzeus_hercules.daemon.explore import explore_routes
from testzeus_hercules.daemon.evidence import (
    IMAGE_SUFFIXES,
    MAX_SIDE,
    MIN_SIDE,
    content_type,
    downscale_jpeg,
    resolve_evidence,
)
from testzeus_hercules.daemon.hub import EventHub
from testzeus_hercules.daemon.manager import RunConflict, RunManager, read_result
from testzeus_hercules.daemon.models import (
    SETTING_NAMES,
    ApprovalRequest,
    ApprovalView,
    CaseSave,
    CaseValidate,
    ControlRequest,
    Environment,
    EnvironmentSave,
    ExportView,
    InputRequest,
    PauseView,
    PlanCreate,
    PlanView,
    RerunRequest,
    RunCreate,
    RunCreated,
    RunSettings,
    RunView,
)
from testzeus_hercules.daemon.openapi import build_openapi
from testzeus_hercules.daemon.security import GuardMiddleware, SessionBook
from testzeus_hercules.daemon.store import ALL_STATUSES, FINAL_STATUSES, RunStore
from testzeus_hercules.daemon.system import models_report
from testzeus_hercules.daemon.ui import serve_ui, ui_root
from testzeus_hercules.kernel.paths import validate_run_id

MAX_WAIT_S = 50.0
MAX_LIST = 200
SSE_KEEPALIVE_S = 15.0
_EVIDENCE_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox", "Cache-Control": "no-store"}
MAX_STEP = 10_000
SCREENCAST_IDLE_S = 15.0
APPROVAL_SETTLE_S = 3.0


class ApiError(Exception):
    def __init__(self, status: int, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.status, self.message, self.detail = status, message, detail


@dataclass
class Daemon:
    config: DaemonConfig
    store: RunStore
    hub: EventHub = field(default_factory=EventHub)
    sessions: SessionBook = field(default_factory=SessionBook)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    manager: RunManager | None = None
    cloud_runs: CloudRuns | None = None
    explore: Any = None  # daemon.explore.ExploreManager, set when the daemon starts

    @property
    def runs(self) -> RunManager:
        if self.manager is None:
            raise ApiError(503, "the daemon is still starting; try again")
        return self.manager

    def ui_url(self, run_id: str) -> str:
        return f"{self.config.public_url}/runs/{run_id}"

    def view(self, record: dict[str, Any], *, with_result: bool = True) -> dict[str, Any]:
        run_id = record["id"]
        run_dir = self.config.paths.runs_dir / run_id
        finished = record["status"] in FINAL_STATUSES
        result = read_result(run_dir) if finished and with_result else None
        if result is not None and not result.ui_url:
            result = result.model_copy(update={"ui_url": self.ui_url(run_id)})
        options = record.get("options") or {}
        live = bool(record.get("worker_pid"))
        draft_steps = [mask_url_credentials(str(step.get("action", ""))) for step in record["case"].get("steps", [])] if record["status"] == "needs_approval" and not live else None
        approval = self._approval(record) if record["status"] in ("needs_approval", "needs_plan_approval") and live else None
        paused = self._paused(record) if record["status"] == "paused" else None
        # A finished run drops its checkpoint, so one left behind means the worker died mid-run.
        resumable = record["status"] == "error" and (self.config.paths.project_hercules_dir / "checkpoints" / f"{run_id}.db").is_file()
        view = RunView(
            run_id=run_id,
            status=record["status"],
            finished=finished,
            case_id=record.get("case_id"),
            title=record.get("title"),
            created_at=record["created_at"],
            started_at=record.get("started_at"),
            finished_at=record.get("finished_at"),
            total_steps=record.get("total_steps"),
            steps_done=record.get("steps_done") or 0,
            current_step=record.get("current_step"),
            current_action=record.get("current_action"),
            reason=record.get("reason"),
            parent_run_id=record.get("parent_run_id"),
            started_by=_started_by(record),
            agent=(record.get("source") or {}).get("agent") if _started_by(record) == "agent" else None,
            worker_pid=record.get("worker_pid"),
            last_seq=self.store.last_seq(run_id),
            mode=options.get("mode"),
            egress=options.get("egress"),
            settings=_settings_of(record),
            run_dir=str(run_dir),
            ui_url=self.ui_url(run_id),
            summary=result.summary_text() if result is not None else _status_line(record),
            draft_steps=draft_steps,
            approval=approval,
            paused=paused,
            resumable=resumable,
            result=result,
        )
        return view.model_dump(mode="json")

    def _approval(self, record: dict[str, Any]) -> ApprovalView | None:
        event = self.store.last_event(record["id"], ("needs_approval",))
        if event is None:
            return None
        data = event["data"]
        fields = {name: data.get(name) for name in ("id", "kind", "step", "reason", "action", "target", "screenshot", "timeout_s")}
        return ApprovalView(**fields, allow_edit=bool(data.get("allow_edit", True)), asked_at=event["at"])

    def _paused(self, record: dict[str, Any]) -> PauseView | None:
        event = self.store.last_event(record["id"], ("paused",))
        if event is None:
            return None
        data = event["data"]
        return PauseView(step=data.get("step"), where=data.get("where"), action=data.get("action"), since=event["at"])


def _status_line(record: dict[str, Any]) -> str:
    line = f"Run {record['id']} {str(record['status']).upper()} (case {record.get('case_id') or '?'})"
    if record["status"] in ("running", "paused") and record.get("current_step"):
        line += f": step {record['current_step']}/{record.get('total_steps') or '?'} {record.get('current_action') or ''}".rstrip()
    if record.get("reason"):
        line += f"\nReason: {record['reason']}"
    return line


Handler = Callable[[Request], Awaitable[Response]]


def _endpoint(handler: Callable[[Request, Daemon], Awaitable[Response]]) -> Handler:
    @functools.wraps(handler)
    async def wrapped(request: Request) -> Response:
        try:
            return await handler(request, request.app.state.daemon)
        except ApiError as exc:
            body: dict[str, Any] = {"error": exc.message}
            if exc.detail is not None:
                body["detail"] = exc.detail
            return JSONResponse(body, status_code=exc.status)

    return wrapped


async def _body(request: Request) -> Any:
    try:
        return await request.json()
    except ValueError:
        raise ApiError(400, "the request body must be JSON") from None


def _parse(model: Any, data: Any) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ApiError(422, case_files.describe_validation(exc)) from None


def _chosen_environment_names(daemon: Daemon, data: Any) -> AbstractSet[str]:
    name = data.get("environment") if isinstance(data, dict) else None
    return set(case_files.environment_data(daemon.config.paths.envs_dir, name)) if isinstance(name, str) else set()


def _query_number(request: Request, name: str, kind: type, *, default: Any, low: float, high: float) -> Any:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = kind(raw)
    except ValueError:
        raise ApiError(422, f"{name} must be a number") from None
    if not low <= value <= high:
        raise ApiError(422, f"{name} must be between {low:g} and {high:g}")
    return value


def _record(daemon: Daemon, run_id: str) -> dict[str, Any]:
    try:
        validate_run_id(run_id)
    except ValueError:
        raise ApiError(404, "run not found") from None
    record = daemon.store.get_run(run_id)
    if record is None:
        raise ApiError(404, "run not found")
    return record


# --------------------------------------------------------------------------- runs


def _case_from_request(daemon: Daemon, body: RunCreate) -> tuple[TestCase, dict[str, Any]]:
    paths = daemon.config.paths
    source: dict[str, Any] = {"environment": body.environment, "base_url": body.base_url}
    try:
        if body.test_case is not None:
            case, source["kind"] = body.test_case, "test_case"
        elif body.gherkin is not None:
            case, source["kind"] = case_files.compile_gherkin(body.gherkin, test_data=body.test_data), "gherkin"
        elif body.description is not None:
            case, source["kind"] = case_files.draft_from_description(body.description), "description"
        elif body.case_path is not None:
            path = case_files.confine(daemon.config.settings.project_dir, body.case_path)
            case, source["kind"] = case_files.load_case_file(path), "case_path"
            source["path"] = path.relative_to(daemon.config.settings.project_dir.resolve()).as_posix()
        else:
            raise ApiError(422, "give one of test_case, gherkin, description or case_path")
        case = case_files.apply_environment(case, paths.envs_dir, environment=body.environment, base_url=body.base_url)
    except case_files.CaseError as exc:
        raise ApiError(422, str(exc)) from None
    return case, source


def _started_by(record: dict[str, Any]) -> Literal["daemon", "agent"] | None:
    origin = (record.get("source") or {}).get("origin")
    if origin == "agent":
        return "agent"
    return "daemon" if origin == "daemon" else None


def run_origin(request: Request) -> dict[str, Any]:
    """Who started a run: a coding agent over MCP (the shim names it in a header), else the tester UI or HTTP API."""
    from testzeus_hercules.kernel.telemetry import agent_client_from_name

    agent = agent_client_from_name(request.headers.get("x-hercules-agent", "").strip()[:64])
    return {"origin": "agent", "agent": agent} if agent else {"origin": "daemon"}


def _inherited(record: dict[str, Any]) -> dict[str, Any]:
    """A run's options as given to ``POST /api/runs``: the worker flags plus the environment and base URL of its source."""
    source = record.get("source") or {}
    return {**(record.get("options") or {}), "environment": source.get("environment"), "base_url": source.get("base_url")}


def _settings_of(record: dict[str, Any]) -> RunSettings | None:
    inherited = {name: value for name, value in _inherited(record).items() if name in SETTING_NAMES}
    try:
        return RunSettings.model_validate(inherited)
    except ValidationError:  # written by an older daemon with different options: show none rather than fail the view
        return None


def _created(daemon: Daemon, record: dict[str, Any]) -> JSONResponse:
    created = RunCreated(run_id=record["id"], status=record["status"], case_id=record.get("case_id"), ui_url=daemon.ui_url(record["id"]))
    return JSONResponse(created.model_dump(mode="json"), status_code=201)


async def create_run(request: Request, daemon: Daemon) -> Response:
    raw = await _body(request)
    with placeholder_names(_chosen_environment_names(daemon, raw)):
        body = _parse(RunCreate, raw)
        case, source = _case_from_request(daemon, body)
    source.update(run_origin(request))
    record = daemon.runs.submit(case, options=body.run_options(), source=source, auto_approve=body.auto_approve, plan=body.plan)
    return _created(daemon, record)


async def list_runs(request: Request, daemon: Daemon) -> Response:
    status = request.query_params.get("status")
    if status is not None and status not in ALL_STATUSES:
        raise ApiError(422, f"status must be one of {', '.join(sorted(ALL_STATUSES))}")
    limit = _query_number(request, "limit", int, default=50, low=1, high=MAX_LIST)
    with_result = request.query_params.get("with_result", "").lower() in ("1", "true", "yes")
    records = daemon.store.list_runs(status=status, limit=limit)
    return JSONResponse({"runs": [daemon.view(record, with_result=with_result) for record in records]})


async def get_run(request: Request, daemon: Daemon) -> Response:
    run_id = request.path_params["run_id"]
    wait_s = _query_number(request, "wait_s", float, default=0.0, low=0, high=MAX_WAIT_S)
    after_seq = _query_number(request, "after_seq", int, default=None, low=0, high=2**31)
    record = _record(daemon, run_id)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while True:
        waiter = daemon.hub.waiter(run_id)
        record = _record(daemon, run_id)
        settled = record["status"] in FINAL_STATUSES or record["status"] in ("needs_approval", "needs_plan_approval")
        changed = after_seq is not None and daemon.store.last_seq(run_id) > after_seq
        remaining = deadline - loop.time()
        if settled or changed or remaining <= 0:
            break
        await daemon.hub.wait(waiter, remaining)
    return JSONResponse(daemon.view(record))


def _last_event_id(request: Request) -> int:
    raw = request.headers.get("last-event-id") or request.query_params.get("after") or "0"
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(422, "Last-Event-ID must be an event number") from None
    if value < 0:
        raise ApiError(422, "Last-Event-ID must be an event number")
    return value


def _sse(event: dict[str, Any]) -> str:
    return f"id: {event['seq']}\nevent: {event['kind']}\ndata: {json.dumps({**event['data'], 'at': event['at']})}\n\n"


async def run_events(request: Request, daemon: Daemon) -> Response:
    run_id = request.path_params["run_id"]
    _record(daemon, run_id)
    last = _last_event_id(request)

    async def stream() -> AsyncIterator[str]:
        nonlocal last
        while True:
            waiter = daemon.hub.waiter(run_id)
            for event in daemon.store.events(run_id, after=last):
                last = event["seq"]
                yield _sse(event)
            record = daemon.store.get_run(run_id)
            if record is None or record["status"] in FINAL_STATUSES:
                for event in daemon.store.events(run_id, after=last):
                    last = event["seq"]
                    yield _sse(event)
                return
            if not await daemon.hub.wait(waiter, SSE_KEEPALIVE_S):
                yield ": keepalive\n\n"
            if await request.is_disconnected():
                return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


async def list_events(request: Request, daemon: Daemon) -> Response:
    run_id = request.path_params["run_id"]
    _record(daemon, run_id)
    after = _query_number(request, "after", int, default=0, low=0, high=2**31)
    limit = _query_number(request, "limit", int, default=1000, low=1, high=5000)
    return JSONResponse({"events": daemon.store.events(run_id, after=after, limit=limit)})


async def cancel_run(request: Request, daemon: Daemon) -> Response:
    run_id = request.path_params["run_id"]
    _record(daemon, run_id)
    try:
        daemon.runs.cancel(run_id)
    except RunConflict as exc:
        raise ApiError(409, str(exc)) from None
    return JSONResponse(daemon.view(_record(daemon, run_id)))


async def delete_run(request: Request, daemon: Daemon) -> Response:
    run_id = request.path_params["run_id"]
    record = _record(daemon, run_id)
    if record["status"] not in FINAL_STATUSES:
        raise ApiError(409, f"run {run_id} is {record['status']}; cancel it before deleting it")
    run_dir = daemon.config.paths.run_dir(run_id)
    if run_dir.is_dir() and not run_dir.is_symlink():
        await asyncio.to_thread(shutil.rmtree, run_dir)
    removed = daemon.store.delete_run(run_id)
    return JSONResponse({"run_id": run_id, "removed": removed})


async def _optional_body(request: Request) -> Any:
    return await _body(request) if (await request.body()).strip() else {}


async def approve_run(request: Request, daemon: Daemon) -> Response:
    run_id = request.path_params["run_id"]
    _record(daemon, run_id)
    body = _parse(ApprovalRequest, await _optional_body(request))
    record = _record(daemon, run_id)
    live = bool(record.get("worker_pid"))
    try:
        daemon.runs.decide(run_id, body.decision, body.step_text, body.plan, approval_id=body.approval_id)
    except RunConflict as exc:
        raise ApiError(409, str(exc)) from None
    if live:  # a running worker answers within moments: report the state it moved to
        await _await_change(daemon, run_id, record["status"], APPROVAL_SETTLE_S)
    return JSONResponse(daemon.view(_record(daemon, run_id)))


async def _await_change(daemon: Daemon, run_id: str, status: str, timeout_s: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        waiter = daemon.hub.waiter(run_id)
        record = daemon.store.get_run(run_id)
        remaining = deadline - loop.time()
        if record is None or record["status"] != status or remaining <= 0:
            return
        await daemon.hub.wait(waiter, remaining)


async def control_run(request: Request, daemon: Daemon) -> Response:
    run_id = request.path_params["run_id"]
    _record(daemon, run_id)
    body = _parse(ControlRequest, await _body(request))
    try:
        daemon.runs.control(run_id, body.action)
    except RunConflict as exc:
        raise ApiError(409, str(exc)) from None
    return JSONResponse({**daemon.view(_record(daemon, run_id), with_result=False), "sent": body.action}, status_code=202)


async def run_input(request: Request, daemon: Daemon) -> Response:
    run_id = request.path_params["run_id"]
    _record(daemon, run_id)
    body = _parse(InputRequest, await _body(request))
    try:
        daemon.runs.send_input(run_id, body.model_dump(exclude_none=True))
    except RunConflict as exc:
        raise ApiError(409, str(exc)) from None
    return JSONResponse({"sent": body.kind}, status_code=202)


async def resume_run(request: Request, daemon: Daemon) -> Response:
    run_id = request.path_params["run_id"]
    _record(daemon, run_id)
    try:
        daemon.runs.resume(run_id)
    except RunConflict as exc:
        raise ApiError(409, str(exc)) from None
    return JSONResponse(daemon.view(_record(daemon, run_id)))


async def screencast(websocket: WebSocket) -> None:
    """Relay the run's live frames until the run ends or the client leaves; slow clients skip frames."""
    daemon: Daemon = websocket.app.state.daemon
    run_id = websocket.path_params["run_id"]
    try:
        validate_run_id(run_id)
        record = daemon.store.get_run(run_id)
    except ValueError:
        record = None
    if record is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    if record["status"] in FINAL_STATUSES:
        await websocket.send_json({"type": "end", "status": record["status"]})
        await websocket.close()
        return
    hub = daemon.runs.screencast
    viewer = hub.subscribe(run_id)
    gone = asyncio.create_task(_until_disconnect(websocket))
    try:
        while not gone.done():
            item = await viewer.next(SCREENCAST_IDLE_S)
            if gone.done():
                break
            if isinstance(item, str):
                await websocket.send_json({"type": "end", "status": item})
                await websocket.close()
                break
            if item is None:
                await websocket.send_json({"type": "idle"})
                continue
            await websocket.send_bytes(item.packet())
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        gone.cancel()
        hub.unsubscribe(viewer)


async def _until_disconnect(websocket: WebSocket) -> None:
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return


async def rerun(request: Request, daemon: Daemon) -> Response:
    run_id = request.path_params["run_id"]
    record = _record(daemon, run_id)
    body = _parse(RerunRequest, await _body(request) if (await request.body()).strip() else {})
    if record["status"] not in FINAL_STATUSES:
        raise ApiError(409, f"run {run_id} is still {record['status']}; wait for it to finish or cancel it")
    if body.scope == "failed_only" and record["status"] == "passed":
        raise ApiError(409, f"run {run_id} passed; nothing failed to re-run (use scope 'whole' to run it again)")
    try:
        settings = body.settings(_inherited(record))
    except ValidationError as exc:
        raise ApiError(422, case_files.describe_validation(exc)) from None
    source = {**(record.get("source") or {}), "environment": settings.environment, "base_url": settings.base_url, **run_origin(request)}
    moved = bool({"environment", "base_url"} & body.overrides().keys())
    try:
        if source.get("kind") == "case_path" and source.get("path"):
            path = case_files.confine(daemon.config.settings.project_dir, source["path"])
            case = case_files.apply_environment(case_files.load_case_file(path), daemon.config.paths.envs_dir, environment=settings.environment, base_url=settings.base_url)
        else:
            # The stored case already has the parent's environment applied; apply only a changed one.
            case = TestCase.model_validate(record["case"])
            if moved and (settings.environment or settings.base_url):
                case = case_files.apply_environment(case, daemon.config.paths.envs_dir, environment=settings.environment, base_url=settings.base_url)
    except case_files.CaseError as exc:
        raise ApiError(422, str(exc)) from None
    except ValidationError as exc:
        raise ApiError(422, case_files.describe_validation(exc)) from None
    created = daemon.runs.submit(case, options=settings.run_options(), source=source, parent_run_id=run_id, auto_approve=True)
    return _created(daemon, created)


async def evidence(request: Request, daemon: Daemon) -> Response:
    run_id = request.path_params["run_id"]
    _record(daemon, run_id)
    path = resolve_evidence(daemon.config.paths.runs_dir, run_id, request.path_params["path"])
    if path is None:
        raise ApiError(404, "evidence not found")
    max_side = _query_number(request, "max_side", int, default=None, low=MIN_SIDE, high=MAX_SIDE)
    if max_side is not None and path.suffix.lower() in IMAGE_SUFFIXES:
        data = await asyncio.to_thread(downscale_jpeg, path, max_side)
        return Response(data, media_type="image/jpeg", headers=_EVIDENCE_HEADERS)
    media_type = content_type(path)
    headers = dict(_EVIDENCE_HEADERS)
    if media_type == "application/octet-stream":
        headers["Content-Disposition"] = "attachment"
    return FileResponse(path, media_type=media_type, headers=headers)


async def export_run(request: Request, daemon: Daemon) -> Response:
    """A finished, passed run as code: ``format`` spec, playwright-ts, playwright-py or (a native app run) appium-py
    (read-only; nothing is written). Without ``format``: appium-py for a native app run, else playwright-ts."""
    from testzeus_hercules.export import FORMATS, GUIDANCE, ExportError, default_format
    from testzeus_hercules.export import export_run as render

    run_id = request.path_params["run_id"]
    record = _record(daemon, run_id)
    fmt = request.query_params.get("format")
    if fmt is not None and fmt not in FORMATS:
        raise ApiError(422, f"format must be one of {', '.join(FORMATS)}")
    if record["status"] not in FINAL_STATUSES:
        raise ApiError(409, f"run {run_id} has not finished ({record['status']}); export it once it has passed")
    result = read_result(daemon.config.paths.runs_dir / run_id)
    if result is None:
        raise ApiError(409, f"run {run_id} has no result to export")
    fmt = fmt or default_format(result)
    allow_failed = request.query_params.get("allow_failed", "").lower() in ("1", "true", "yes")
    target_dir = request.query_params.get("target_dir") or None
    if target_dir is not None and (len(target_dir) > 300 or target_dir.startswith(("/", "\\")) or ".." in target_dir.replace("\\", "/").split("/")):
        raise ApiError(422, "target_dir must be a relative folder inside the project, without '..'")
    try:
        exported = await asyncio.to_thread(render, result, fmt, allow_failed=allow_failed, target_dir=target_dir)  # type: ignore[arg-type]
    except ExportError as exc:
        raise ApiError(409, str(exc)) from None
    view = ExportView.model_validate({**exported.to_dict(), "guidance": GUIDANCE})
    return JSONResponse(view.model_dump(mode="json"))


async def run_case(request: Request, daemon: Daemon) -> Response:
    run_id = request.path_params["run_id"]
    record = _record(daemon, run_id)
    source = record.get("source") or {}
    # The run's case as evidence: URL credentials masked (the case file itself is read and edited through /api/cases).
    test_case = json.loads(mask_url_credentials(json.dumps(record["case"])))
    return JSONResponse({"run_id": run_id, "test_case": test_case, "source": {key: source.get(key) for key in ("kind", "path", "environment", "base_url")}})


async def step_decisions(request: Request, daemon: Daemon) -> Response:
    run_id = request.path_params["run_id"]
    _record(daemon, run_id)
    raw = request.path_params["step"]
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_STEP:
        raise ApiError(422, f"step must be a number between 1 and {MAX_STEP}")
    step = int(raw)
    decisions = await asyncio.to_thread(read_step_decisions, daemon.config.paths.runs_dir, run_id, step)
    return JSONResponse({"run_id": run_id, "step": step, "decisions": decisions})


# --------------------------------------------------------------------------- plans


def _plan_view(plan: Any, *, run_id: str | None = None) -> dict[str, Any]:
    from testzeus_hercules.engine.plan import render_plan

    text = mask_url_credentials(render_plan(plan))
    view = PlanView(plan=plan, lint_errors=len(plan.lint_errors), lint_warnings=len(plan.lint_warnings), run_id=run_id, approved=plan.review.state in ("approved", "edited"), summary=text)
    return json.loads(mask_url_credentials(json.dumps(view.model_dump(mode="json"))))


def _run_plan(daemon: Daemon, run_id: str) -> Any:
    from testzeus_hercules.engine.plan_run import read_plan

    plan = read_plan(daemon.config.paths.runs_dir / run_id)
    if plan is None:
        raise ApiError(404, "this run has no plan (it has not been planned yet, or every step runs from a payload)")
    return plan


async def run_plan(request: Request, daemon: Daemon) -> Response:
    """The plan a run made or was given (``plan.json``): steps, criteria with their results, amendments, lint."""
    run_id = request.path_params["run_id"]
    _record(daemon, run_id)
    return JSONResponse(_plan_view(_run_plan(daemon, run_id), run_id=run_id))


async def run_plan_diff(request: Request, daemon: Daemon) -> Response:
    """How a run's plan differs from another run's (``against``), or, without ``against``, what the run changed in its plan
    (its amendments, as plan steps)."""
    from testzeus_hercules.engine.plan import as_run, diff_plans

    run_id = request.path_params["run_id"]
    _record(daemon, run_id)
    plan = _run_plan(daemon, run_id)
    against = request.query_params.get("against")
    if against:
        _record(daemon, against)
        other = _run_plan(daemon, against)
        diff = diff_plans(other, plan)
        body = {"run_id": run_id, "against": against, "diff": diff}
    else:
        diff = diff_plans(plan, as_run(plan))
        body = {"run_id": run_id, "against": None, "diff": diff, "amendments": [amendment.model_dump(mode="json") for amendment in plan.amendments]}
    return JSONResponse(json.loads(mask_url_credentials(json.dumps(body))))


def _plan_case(daemon: Daemon, body: PlanCreate) -> TestCase:
    try:
        if body.test_case is not None:
            case = body.test_case
        elif body.gherkin is not None:
            case = case_files.compile_gherkin(body.gherkin, test_data=body.test_data)
        elif body.case_path is not None:
            case = case_files.load_case_file(case_files.confine(daemon.config.settings.project_dir, body.case_path))
        else:
            raise ApiError(422, "give one of test_case, gherkin or case_path")
        return case_files.apply_environment(case, daemon.config.paths.envs_dir, environment=body.environment, base_url=body.base_url)
    except case_files.CaseError as exc:
        raise ApiError(422, str(exc)) from None


async def create_plan(request: Request, daemon: Daemon) -> Response:
    """Make a case's plan and lint it without running it (the model reads the case; the decider audits its coverage)."""
    from testzeus_hercules.engine.plan import PlanStore, approved, plan_key

    raw = await _body(request)
    with placeholder_names(_chosen_environment_names(daemon, raw)):
        body = _parse(PlanCreate, raw)
        case = _plan_case(daemon, body)
    options = body.model_dump(include={"mode", "egress", "decider", "strict"})
    try:
        plan = await daemon.runs.plan_job(case, options)
    except RunConflict as exc:
        raise ApiError(409, str(exc)) from None
    if body.approve:
        plan = approved(plan, by="agent")
        if plan.model:
            PlanStore(daemon.config.paths.project_hercules_dir / "plans").save_approved(plan_key(plan.case_hash, plan.model), plan)
    return JSONResponse(_plan_view(plan))


async def case_plan(request: Request, daemon: Daemon) -> Response:
    """The latest plan made for a saved case (as ``hercules plan``, ``POST /api/plans`` or a run made it); ``current`` says
    whether it was made for the case as it is now."""
    from testzeus_hercules.engine.data import protect_case
    from testzeus_hercules.engine.plan import PlanStore, case_hash

    try:
        _, case = case_files.read_saved_case(daemon.config.paths.cases_dir, request.path_params["name"])
    except FileNotFoundError:
        raise ApiError(404, "case not found") from None
    except case_files.CaseError as exc:
        raise ApiError(422, str(exc)) from None
    plan = PlanStore(daemon.config.paths.project_hercules_dir / "plans").latest(case.id)
    if plan is None:
        raise ApiError(404, "no plan has been made for this case yet; make one with POST /api/plans (or hercules plan)")
    try:
        current_case = case_files.apply_environment(case, daemon.config.paths.envs_dir, environment=None, base_url=None)
    except case_files.CaseError:
        current_case = case
    current = plan.case_hash == case_hash(protect_case(current_case)[0])
    return JSONResponse({**_plan_view(plan), "current": current})


# --------------------------------------------------------------------------- cases and environments


async def list_cases(request: Request, daemon: Daemon) -> Response:
    paths = daemon.config.paths
    return JSONResponse({"cases": case_files.list_cases(paths.cases_dir, daemon.config.settings.project_dir)})


async def get_case(request: Request, daemon: Daemon) -> Response:
    try:
        path, case = case_files.read_saved_case(daemon.config.paths.cases_dir, request.path_params["name"])
    except FileNotFoundError:
        raise ApiError(404, "case not found") from None
    except case_files.CaseError as exc:
        raise ApiError(422, str(exc)) from None
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        source = None
    return JSONResponse({"name": path.name, "kind": path.suffix.lstrip("."), "source": source, "test_case": case_files.case_json(case)})


async def validate_case(request: Request, daemon: Daemon) -> Response:
    body = _parse(CaseValidate, await _body(request))
    if body.gherkin is not None:
        try:
            case = case_files.compile_gherkin(body.gherkin, test_data=body.test_data)
        except case_files.CaseError as exc:
            return JSONResponse({"valid": False, "error": str(exc), "test_case": None})
    else:
        try:
            case = TestCase.model_validate(body.test_case)
        except ValidationError as exc:
            return JSONResponse({"valid": False, "error": case_files.describe_validation(exc), "test_case": None})
    return JSONResponse({"valid": True, "error": None, "test_case": case_files.case_json(case)})


async def save_case(request: Request, daemon: Daemon) -> Response:
    body = _parse(CaseSave, await _body(request))
    try:
        path, case = case_files.save_case(daemon.config.paths.cases_dir, test_case=body.test_case, gherkin=body.gherkin, overwrite=body.overwrite, test_data=body.test_data)
    except case_files.CaseExists as exc:
        raise ApiError(409, str(exc)) from None
    except case_files.CaseError as exc:
        raise ApiError(422, str(exc)) from None
    relative = path.relative_to(daemon.config.settings.project_dir).as_posix()
    return JSONResponse({"name": path.name, "path": relative, "id": case.id}, status_code=201)


async def delete_case(request: Request, daemon: Daemon) -> Response:
    try:
        path = case_files.delete_case(daemon.config.paths.cases_dir, daemon.config.settings.project_dir, request.path_params["name"])
    except FileNotFoundError:
        raise ApiError(404, "case not found") from None
    except case_files.CaseError as exc:
        raise ApiError(422, str(exc)) from None
    return JSONResponse({"name": path.name, "removed": True})


async def delete_environment(request: Request, daemon: Daemon) -> Response:
    name = request.path_params["name"]
    if not case_files.delete_environment(daemon.config.paths.envs_dir, daemon.config.settings.project_dir, name):
        raise ApiError(404, "environment not found")
    return JSONResponse({"name": name, "removed": True})


async def list_environments(request: Request, daemon: Daemon) -> Response:
    envs = case_files.list_environments(daemon.config.paths.envs_dir)
    return JSONResponse({"environments": [env.model_dump(mode="json") for env in envs]})


async def list_devices(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.cases.devices import device_names

    return JSONResponse({"devices": list(device_names())})


async def get_environment(request: Request, daemon: Daemon) -> Response:
    env = case_files.load_environment(daemon.config.paths.envs_dir, request.path_params["name"])
    if env is None:
        raise ApiError(404, "environment not found")
    return JSONResponse(env.model_dump(mode="json"))


async def save_environment(request: Request, daemon: Daemon) -> Response:
    body = _parse(EnvironmentSave, await _body(request))
    env = Environment.model_validate(body.model_dump(exclude={"overwrite"}))
    try:
        case_files.save_environment(daemon.config.paths.envs_dir, env, overwrite=body.overwrite)
    except case_files.CaseExists as exc:
        raise ApiError(409, str(exc)) from None
    except case_files.CaseError as exc:
        raise ApiError(422, str(exc)) from None
    return JSONResponse(env.model_dump(mode="json"), status_code=201)


# --------------------------------------------------------------------------- system


async def health(request: Request, daemon: Daemon) -> Response:
    return JSONResponse(
        {
            "status": "ok",
            "version": package_version(),
            "pid": os.getpid(),
            "started_at": daemon.started_at,
            "project_dir": str(daemon.config.settings.project_dir),
            "parallel_runs": daemon.config.parallel_runs,
            "queue": daemon.store.counts(),
            "ui_url": daemon.config.public_url,
        }
    )


async def models(request: Request, daemon: Daemon) -> Response:
    probe = request.query_params.get("probe", "").lower() in ("1", "true", "yes")
    report = await asyncio.to_thread(models_report, daemon.config, probe)
    return JSONResponse(report)


async def login_url(request: Request, daemon: Daemon) -> Response:
    code = daemon.sessions.new_code()
    return JSONResponse({"url": f"{daemon.config.public_url}/login?code={code}", "expires_in_s": 300})


async def openapi(request: Request, daemon: Daemon) -> Response:
    return JSONResponse(build_openapi(package_version()))


async def page(request: Request, daemon: Daemon) -> Response:
    return await asyncio.to_thread(serve_ui, request, ui_root(daemon.config.ui_dir))


def create_app(config: DaemonConfig) -> Starlette:
    """The daemon application; workers start with the app's lifespan."""
    paths = config.paths
    paths.ensure_dirs()
    daemon = Daemon(config=config, store=RunStore(paths.history_db))
    set_environment_names(case_files.environment_names(paths.envs_dir))

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        daemon.manager = RunManager(config, daemon.store, daemon.hub)
        await daemon.manager.start()
        daemon.cloud_runs = CloudRuns(paths.project_hercules_dir / "cloud-runs", config.settings, config.worker_env, paths.project_hercules_dir.parent)
        daemon.cloud_runs.recover()
        from testzeus_hercules.daemon.explore import ExploreManager

        daemon.explore = ExploreManager(daemon)
        try:
            yield
        finally:
            await daemon.explore.close()
            await daemon.cloud_runs.close()
            await daemon.manager.stop()
            daemon.store.close()

    routes = [
        Route("/api/health", _endpoint(health), methods=["GET"]),
        Route("/api/login-url", _endpoint(login_url), methods=["POST"]),
        Route("/api/openapi.json", _endpoint(openapi), methods=["GET"]),
        Route("/api/models", _endpoint(models), methods=["GET"]),
        Route("/api/runs", _endpoint(list_runs), methods=["GET"]),
        Route("/api/runs", _endpoint(create_run), methods=["POST"]),
        Route("/api/runs/{run_id}", _endpoint(get_run), methods=["GET"]),
        Route("/api/runs/{run_id}", _endpoint(delete_run), methods=["DELETE"]),
        Route("/api/runs/{run_id}/events", _endpoint(run_events), methods=["GET"]),
        Route("/api/runs/{run_id}/events/list", _endpoint(list_events), methods=["GET"]),
        Route("/api/runs/{run_id}/cancel", _endpoint(cancel_run), methods=["POST"]),
        Route("/api/runs/{run_id}/approve", _endpoint(approve_run), methods=["POST"]),
        Route("/api/runs/{run_id}/control", _endpoint(control_run), methods=["POST"]),
        Route("/api/runs/{run_id}/input", _endpoint(run_input), methods=["POST"]),
        Route("/api/runs/{run_id}/resume", _endpoint(resume_run), methods=["POST"]),
        WebSocketRoute("/api/runs/{run_id}/screencast", screencast),
        Route("/api/runs/{run_id}/rerun", _endpoint(rerun), methods=["POST"]),
        Route("/api/runs/{run_id}/evidence/{path:path}", _endpoint(evidence), methods=["GET"]),
        Route("/api/runs/{run_id}/case", _endpoint(run_case), methods=["GET"]),
        Route("/api/runs/{run_id}/export", _endpoint(export_run), methods=["GET"]),
        Route("/api/runs/{run_id}/cloud-runs", _endpoint(cloud_api.list_cloud_runs), methods=["GET"]),
        Route("/api/runs/{run_id}/cloud-runs", _endpoint(cloud_api.create_cloud_run), methods=["POST"]),
        Route("/api/runs/{run_id}/cloud-bundle", _endpoint(cloud_api.cloud_bundle), methods=["GET"]),
        Route("/api/cloud-runs/{cloud_run_id}", _endpoint(cloud_api.get_cloud_run), methods=["GET"]),
        Route("/api/cloud-runs/{cloud_run_id}/cancel", _endpoint(cloud_api.cancel_cloud_run), methods=["POST"]),
        *explore_routes(),
        Route("/api/secrets", _endpoint(secrets_api.list_secrets), methods=["GET"]),
        Route("/api/secrets/{name}", _endpoint(secrets_api.put_secret), methods=["PUT"]),
        Route("/api/secrets/{name}", _endpoint(secrets_api.delete_secret), methods=["DELETE"]),
        Route("/api/clouds", _endpoint(cloud_api.list_clouds), methods=["GET"]),
        Route("/api/clouds/{cloud}", _endpoint(cloud_api.save_cloud), methods=["PUT"]),
        Route("/api/clouds/{cloud}", _endpoint(cloud_api.remove_cloud), methods=["DELETE"]),
        Route("/api/clouds/{cloud}/check", _endpoint(cloud_api.check_cloud), methods=["POST"]),
        Route("/api/clouds/{cloud}/region", _endpoint(cloud_api.save_cloud_region), methods=["PUT"]),
        Route("/api/runs/{run_id}/steps/{step}/decisions", _endpoint(step_decisions), methods=["GET"]),
        Route("/api/runs/{run_id}/plan", _endpoint(run_plan), methods=["GET"]),
        Route("/api/runs/{run_id}/plan/diff", _endpoint(run_plan_diff), methods=["GET"]),
        Route("/api/plans", _endpoint(create_plan), methods=["POST"]),
        Route("/api/cases", _endpoint(list_cases), methods=["GET"]),
        Route("/api/cases", _endpoint(save_case), methods=["POST"]),
        Route("/api/cases/validate", _endpoint(validate_case), methods=["POST"]),
        Route("/api/cases/{name}", _endpoint(get_case), methods=["GET"]),
        Route("/api/cases/{name}", _endpoint(delete_case), methods=["DELETE"]),
        Route("/api/cases/{name}/plan", _endpoint(case_plan), methods=["GET"]),
        Route("/api/cases/{name}/files", _endpoint(files_api.list_files), methods=["GET"]),
        Route("/api/cases/{name}/files/{filename}", _endpoint(files_api.put_file), methods=["PUT"]),
        Route("/api/cases/{name}/files/{filename}", _endpoint(files_api.delete_file), methods=["DELETE"]),
        Route("/api/environments", _endpoint(list_environments), methods=["GET"]),
        Route("/api/devices", _endpoint(list_devices), methods=["GET"]),
        Route("/api/environments", _endpoint(save_environment), methods=["POST"]),
        Route("/api/environments/{name}", _endpoint(get_environment), methods=["GET"]),
        Route("/api/environments/{name}", _endpoint(delete_environment), methods=["DELETE"]),
        Route("/api/environments/{name}/files", _endpoint(files_api.list_files), methods=["GET"]),
        Route("/api/environments/{name}/files/{filename}", _endpoint(files_api.put_file), methods=["PUT"]),
        Route("/api/environments/{name}/files/{filename}", _endpoint(files_api.delete_file), methods=["DELETE"]),
        Route("/", _endpoint(page), methods=["GET"]),
        Route("/{path:path}", _endpoint(page), methods=["GET"]),
    ]
    middleware = [Middleware(GuardMiddleware, token=config.token, sessions=daemon.sessions, allow_remote=config.allow_remote, bound_host=config.host, allowed_hosts=config.allowed_hosts)]
    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
    app.state.daemon = daemon
    return app
