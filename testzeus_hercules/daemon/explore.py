"""Exploration sessions in the daemon: ``/api/explore``, the finding ledger's ``/api/explore/findings`` and the
``/api/explore/reverify`` job that reruns the kept cases.

Each session's coordinator runs as a task in the daemon; its explorers and reproducers are ordinary daemon runs (worker
processes, live views, take over, approvals) started with an exploration job. The daemon reserves the slots for the
swarm size the user chose, beyond its usual parallel runs, and releases them when the session ends. Session events
(charters, clues, findings, size changes) are kept in memory for long-polls and written to the session folder.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from testzeus_hercules.cases.models import TestCase
from testzeus_hercules.explore.coordinator import Limits, Session, estimate, size_problems
from testzeus_hercules.explore.issues import TRACKERS, github_new_issue_url, issue_text, issue_title
from testzeus_hercules.explore.ledger import Ledger, TriageError, default_triager
from testzeus_hercules.explore.models import ExploreSpec, Finding, SessionView
from testzeus_hercules.explore.reverify import DEFAULT_CASE_TIMEOUT_S, REVERIFY_BY, reverify
from testzeus_hercules.explore.scope import production_host

if TYPE_CHECKING:
    from testzeus_hercules.daemon.app import Daemon
    from testzeus_hercules.daemon.manager import RunManager

__all__ = ["ExploreManager", "explore_routes"]

_SESSION_ID = re.compile(r"^x_[0-9]{8}_[0-9]{6}_[0-9a-f]{6}$")
_FINDING_ID = re.compile(r"^f[0-9a-f]{10}$")
_FINDING_PREFIX = re.compile(r"^f[0-9a-f]{3,9}$")  # an id prefix of at least 4 characters
_REVERIFY_ID = re.compile(r"^rv_[0-9a-f]{8}$")
_CASE_DECISIONS = ("proposed", "kept", "regression", "rejected")
MAX_WAIT_S = 50.0
_EVENTS_KEPT = 2000
_REVERIFY_KEPT = 20
_RUN_POLL_S = 0.5


def new_session_id() -> str:
    return f"x_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


class DaemonChildRunner:
    """Child runs through the daemon's run manager: each with its job file (private), linked to the session by source."""

    def __init__(self, manager: RunManager, session_id: str, jobs_dir: Path) -> None:
        self.manager = manager
        self.session_id = session_id
        self.jobs_dir = jobs_dir

    async def start(self, *, case: TestCase, job: Mapping[str, Any], label: str) -> str:
        options: dict[str, Any] = {}
        if job:
            self.jobs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.jobs_dir / f"{secrets.token_hex(6)}.json"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(dict(job), handle)
            options["explore_job"] = str(path)
        record = self.manager.submit(case, options=options, source={"kind": "explore", "session": self.session_id, "label": label[:200]})
        return str(record["id"])

    def status(self, run_id: str) -> str:
        record = self.manager.store.get_run(run_id)
        return str(record["status"]) if record else "error"

    def run_dir(self, run_id: str) -> Path:
        return self.manager.runs_dir / run_id

    async def cancel(self, run_id: str) -> None:
        with contextlib.suppress(Exception):
            self.manager.cancel(run_id)


@dataclass
class _Entry:
    session: Session
    task: asyncio.Task[Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    reserved: int = 0
    engines: Any = None


@dataclass
class ReverifyJob:
    """One ``reverify``: the lines it printed (as ``hercules explore reverify`` prints them) and its exit code."""

    id: str
    build: str
    status: str = "running"  # running, finished, failed
    case_timeout_s: float = DEFAULT_CASE_TIMEOUT_S
    only: list[str] | None = None
    lines: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    exit_code: int | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    task: asyncio.Task[Any] | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "build": self.build,
            "status": self.status,
            "finished": self.status != "running",
            "lines": list(self.lines),
            "results": list(self.results),
            "errors": list(self.errors),
            "run_ids": list(self.run_ids),
            "regressions": sum(item["outcome"] == "regression" for item in self.results),
            "inconclusive": sum(item["outcome"] == "inconclusive" for item in self.results),
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class ExploreManager:
    def __init__(self, daemon: Daemon) -> None:
        self.daemon = daemon
        self.sessions: dict[str, _Entry] = {}
        self.reverifies: dict[str, ReverifyJob] = {}
        self.explore_dir = daemon.config.paths.project_hercules_dir / "explore"

    @property
    def ledger(self) -> Ledger:
        return Ledger(self.explore_dir)

    def limits(self) -> Limits:
        from testzeus_hercules.daemon.config import MAX_PARALLEL_RUNS
        from testzeus_hercules.llm.tiers import detect_memory_bytes

        return Limits(max_workers=self.daemon.config.settings.explore.max_workers, parallel_cap=MAX_PARALLEL_RUNS, memory_bytes=detect_memory_bytes())

    async def start(self, spec: ExploreSpec, *, by: str) -> Session:
        from testzeus_hercules.cli.version import package_version

        session_id = new_session_id()
        runner = DaemonChildRunner(self.daemon.runs, session_id, self.explore_dir / session_id / "jobs")
        entry: _Entry
        settings = self.daemon.config.settings

        def emit(kind: str, data: dict[str, Any]) -> None:
            entry.events.append({"seq": len(entry.events) + 1, "kind": kind, "data": data, "at": time.time()})
            del entry.events[:-_EVENTS_KEPT]
            entry.changed.set()
            if kind == "size":
                self._reserve(entry)

        session = Session(
            id=session_id,
            spec=spec,
            project_dir=self.daemon.config.paths.project_hercules_dir.parent,
            runner=runner,
            emit=emit,
            limits=self.limits(),
            hercules_version=package_version(),
            llm=settings.llm.model,
            decider_name=settings.decider.backend or "auto",
            cost_per_hosted_call_usd=settings.explore.cost_per_decision_usd,
        )
        entry = _Entry(session=session)
        self.sessions[session_id] = entry
        self._reserve(entry)
        entry.task = asyncio.create_task(self._run(entry, by=by), name=f"hercules-explore-{session_id}")
        return session

    def _reserve(self, entry: _Entry) -> None:
        manager = self.daemon.runs
        manager.reserved -= entry.reserved
        view = entry.session.view
        entry.reserved = 0 if view.status in ("finished", "failed", "stopped") else view.workers + view.reproducers
        manager.reserved += entry.reserved
        manager.wake()

    async def _run(self, entry: _Entry, *, by: str) -> SessionView:
        session = entry.session
        telemetry = self._telemetry()
        started = time.monotonic()
        spec = session.spec
        if telemetry is not None:
            telemetry.emit("explore_started", workers=spec.workers, reproducers=spec.reproducers, time_budget_s=spec.budget.time_s)
        try:
            entry.engines = await self._engines()
            session.decider = entry.engines.decider if entry.engines is not None else None
            return await session.run()
        finally:
            entry.reserved, reserved = 0, entry.reserved
            self.daemon.runs.reserved -= reserved
            entry.changed.set()
            if telemetry is not None:
                view = session.view
                status = view.status if view.status in ("finished", "failed", "stopped") else "failed"
                telemetry.emit("explore_finished", explore_status=status, finding_count=len(view.findings), clue_count=view.clues, duration_ms=int((time.monotonic() - started) * 1000))
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(telemetry.close)
            if entry.engines is not None:
                with contextlib.suppress(Exception):
                    await entry.engines.aclose()

    def _telemetry(self) -> Any:
        """Counts-only telemetry for the session (None when it cannot be set up: telemetry never breaks a session)."""
        try:
            from testzeus_hercules.kernel.telemetry import TelemetryClient

            return TelemetryClient(self.daemon.config.settings, self.daemon.config.paths, run_origin="daemon")
        except Exception:
            return None

    async def _engines(self) -> Any:
        """The coordinator's model (it picks charters); None when no model is configured (charters go by priority)."""
        from testzeus_hercules.cli.deciders import prepare
        from testzeus_hercules.engine.factory import FactoryOptions, build_engines

        settings = self.daemon.config.settings
        setup = prepare(settings, self.daemon.config.worker_env)
        try:
            return await build_engines(setup.settings, env=setup.env, options=FactoryOptions())
        except Exception:
            return None

    def get(self, session_id: str) -> _Entry | None:
        return self.sessions.get(session_id)

    def past(self) -> list[dict[str, Any]]:
        """Sessions on disk that this daemon did not start (earlier runs of the daemon)."""
        found = []
        if self.explore_dir.is_dir():
            for path in sorted(self.explore_dir.glob("x_*/session.json"), reverse=True)[:50]:
                if path.parent.name in self.sessions:
                    continue
                with contextlib.suppress(OSError, ValueError):
                    found.append(json.loads(path.read_text(encoding="utf-8")))
        return found

    # ------------------------------------------------------------------ reverify

    def start_reverify(self, build: str, *, by: str = REVERIFY_BY, case_timeout_s: float = DEFAULT_CASE_TIMEOUT_S, only: list[str] | None = None) -> ReverifyJob:
        """Start a reverify job (one at a time: a second one while it runs is refused with ValueError)."""
        running = next((job for job in self.reverifies.values() if job.status == "running"), None)
        if running is not None:
            raise ValueError(f"a reverify is already running ({running.id}); follow it with GET /api/explore/reverify/{running.id}")
        job = ReverifyJob(id=f"rv_{secrets.token_hex(4)}", build=build, case_timeout_s=case_timeout_s, only=only)
        self.reverifies[job.id] = job
        for old in [key for key, item in self.reverifies.items() if item.status != "running"][:-_REVERIFY_KEPT]:
            del self.reverifies[old]
        job.task = asyncio.create_task(self._reverify(job, by=by), name=f"hercules-reverify-{job.id}")
        return job

    async def _reverify(self, job: ReverifyJob, *, by: str) -> None:
        from testzeus_hercules.daemon import cases as case_files
        from testzeus_hercules.daemon.store import FINAL_STATUSES

        project_dir = self.daemon.config.settings.project_dir
        manager = self.daemon.runs

        def say(line: str) -> None:
            job.lines.append(line)
            job.changed.set()

        async def start_run(case_path: Path) -> str | None:
            try:
                path = case_files.confine(project_dir, str(case_path))
                case = case_files.load_case_file(path)
                case = case_files.apply_environment(case, self.daemon.config.paths.envs_dir, environment=None, base_url=None)
            except case_files.CaseError as exc:
                job.errors.append(f"{case_path.name}: {exc}")
                return None
            source = {"kind": "case_path", "path": path.relative_to(project_dir.resolve()).as_posix(), "environment": None, "base_url": None, "origin": "daemon", "reverify": job.id}
            record = manager.submit(case, options={}, source=source)
            job.run_ids.append(str(record["id"]))
            job.changed.set()
            return str(record["id"])

        async def wait_run(run_id: str, timeout_s: float) -> str:
            deadline = time.monotonic() + timeout_s
            while True:
                record = manager.store.get_run(run_id)
                if record is None:
                    return "error"
                if record["status"] in FINAL_STATUSES:
                    return str(record["status"])
                if record["status"] in ("needs_approval", "needs_plan_approval"):  # nobody is there to answer: inconclusive
                    with contextlib.suppress(Exception):
                        manager.cancel(run_id)
                    return str(record["status"])
                if time.monotonic() > deadline:
                    with contextlib.suppress(Exception):
                        manager.cancel(run_id)
                    return "timeout"
                await asyncio.sleep(_RUN_POLL_S)

        def report(result: dict[str, Any]) -> None:
            job.results.append(result)
            job.changed.set()

        async def triage(finding_id: str, body: dict[str, Any]) -> None:
            try:
                await asyncio.to_thread(self.ledger.triage, finding_id, by=str(body["by"]), state=body.get("state"), reason=str(body.get("reason") or ""))
            except TriageError as exc:
                job.errors.append(f"{finding_id}: {exc}")

        try:
            findings = [json.loads(item.model_dump_json()) for item in await asyncio.to_thread(self.ledger.all)]
            job.exit_code = await reverify(
                findings,
                project_dir=project_dir,
                build=job.build,
                start_run=start_run,
                wait_run=wait_run,
                triage=triage,
                say=say,
                report=report,
                by=by,
                case_timeout_s=job.case_timeout_s,
                only=job.only,
            )
            job.status = "finished"
        except asyncio.CancelledError:
            job.status, job.exit_code = "failed", 2
            job.errors.append("the reverify was cancelled (the daemon stopped)")
            raise
        except Exception as exc:  # a job that breaks says so; it never vanishes
            job.status, job.exit_code = "failed", 2
            job.errors.append(f"the reverify failed ({type(exc).__name__}): {str(exc)[:300]}")
        finally:
            job.finished_at = time.time()
            job.changed.set()

    async def close(self) -> None:
        for job in self.reverifies.values():
            if job.task is not None and not job.task.done():
                job.task.cancel()
                with contextlib.suppress(BaseException):
                    await job.task
        for entry in self.sessions.values():
            entry.session.stop(by="the daemon stopping")
            if entry.task is not None and not entry.task.done():
                entry.task.cancel()
                with contextlib.suppress(BaseException):
                    await entry.task


# --------------------------------------------------------------------------- requests


class ExploreStart(ExploreSpec):
    by: str = "the tester UI"


class SizeChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workers: int | None = Field(default=None, ge=1, le=64)
    reproducers: int | None = Field(default=None, ge=1, le=16)
    allow_more_workers: bool = False
    by: str = "the tester UI"


class CharterAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    area: str = Field(default="/", max_length=200)
    levers: list[str] = Field(default_factory=list)
    timebox_s: int = Field(default=300, ge=30, le=3600)
    pinned: bool = False
    by: str = "the tester UI"


class CharterSteer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern=r"^(veto|pin|note)$")
    note: str = Field(default="", max_length=1000)
    by: str = "the tester UI"


class StopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by: str = Field(default="the tester UI", min_length=1, max_length=120)


class ReverifyStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    build: str = Field(default="current", min_length=1, max_length=200)
    findings: list[str] | None = Field(default=None, max_length=200, description="only these findings (ids or unique prefixes)")
    case_timeout_s: float = Field(default=DEFAULT_CASE_TIMEOUT_S, ge=30, le=7200, description="each case's run is cancelled and inconclusive after this")


class Triage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by: str = Field(min_length=1, max_length=120)
    state: str | None = None
    severity: str | None = None
    priority: str | None = None
    tracker: str | None = Field(default=None, max_length=200)
    duplicate_of: str | None = None
    case: str | None = Field(default=None, pattern=r"^(proposed|kept|regression|rejected)$")
    reason: str = Field(default="", max_length=1000)


def _api_error(status: int, message: str) -> Exception:
    from testzeus_hercules.daemon.app import ApiError

    return ApiError(status, message)


def _manager(daemon: Daemon) -> ExploreManager:
    manager = getattr(daemon, "explore", None)
    if manager is None:
        raise _api_error(503, "the daemon is still starting; try again")
    return manager  # type: ignore[no-any-return]


def _finding(daemon: Daemon, raw: str) -> Finding:
    """The finding with this id, or the only one whose id starts with it (at least 4 characters): 404 when none, 400
    with the candidates when the prefix names several."""
    ledger = _manager(daemon).ledger
    if _FINDING_ID.match(raw):
        found = ledger.get(raw)
        if found is not None:
            return found
    elif _FINDING_PREFIX.match(raw):
        matches = [item for item in ledger.all() if item.id.startswith(raw)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise _api_error(400, f"{raw} names {len(matches)} findings ({', '.join(item.id for item in matches[:10])}): give more of the id")
    raise _api_error(404, f"no finding {raw[:40]!r} in the ledger (ids look like f0123456789; a unique start of 4 or more characters works)")


async def _body_or_empty(request: Request) -> Any:
    """The JSON body, or {} when there is none (older clients post without one)."""
    from testzeus_hercules.daemon.app import _body

    return await _body(request) if await request.body() else {}


def _target_host(spec: ExploreSpec, project_dir: Path) -> str:
    """The host a session would explore, worked out as the session does (base URL, saved environment, start case); ""
    when it cannot be told before starting."""
    from urllib.parse import urlsplit

    base = spec.base_url
    with contextlib.suppress(Exception):
        if not base and spec.environment:
            from testzeus_hercules.daemon.cases import load_environment

            saved = load_environment(project_dir / ".hercules" / "envs", spec.environment)
            base = saved.base_url if saved is not None else None
        if not base and spec.start_case:
            from testzeus_hercules.daemon import cases as case_files

            base = case_files.load_case_file(case_files.confine(project_dir, spec.start_case)).environment.base_url
    return (urlsplit(base).hostname or "") if base else ""


def host_problem(spec: ExploreSpec, project_dir: Path) -> str | None:
    """Why the target host needs the user's word before exploring (it does not look like a test environment), or None."""
    host = _target_host(spec, project_dir)
    if not host or spec.production_ok or not production_host(host):
        return None
    return (
        f"{host} does not look like a test environment (no staging/test/qa/dev/local label), and explorers fill forms and change data. "
        "Ask the user to confirm this host is theirs and safe to explore; only with their explicit yes start again with "
        "i_own_this_environment (production_ok; the CLI's --i-own-this-environment)"
    )


def _entry(daemon: Daemon, session_id: str) -> _Entry:
    if not _SESSION_ID.match(session_id):
        raise _api_error(404, "no such session")
    entry = _manager(daemon).get(session_id)
    if entry is None:
        raise _api_error(404, "no such session (sessions from an earlier daemon are read-only: see their report)")
    return entry


# --------------------------------------------------------------------------- handlers


async def start_session(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _body, _parse

    body: ExploreStart = _parse(ExploreStart, await _body(request))
    spec = ExploreSpec.model_validate(body.model_dump(exclude={"by"}))
    session = await _manager(daemon).start(spec, by=body.by)
    return JSONResponse(json.loads(session.snapshot().model_dump_json()), status_code=202)


async def preflight(request: Request, daemon: Daemon) -> Response:
    """What the chosen size and time mean before starting: limits it exceeds, and the estimated decisions and cost."""
    from testzeus_hercules.daemon.app import _body, _parse

    spec: ExploreSpec = _parse(ExploreSpec, await _body(request))
    manager = _manager(daemon)
    local = daemon.config.settings.decider.backend in (None, "local") and daemon.config.settings.egress.policy == "local"
    host = await asyncio.to_thread(host_problem, spec, daemon.config.settings.project_dir)
    return JSONResponse(
        {
            "problems": size_problems(spec, manager.limits()),
            "host_problem": host,
            "estimate": estimate(spec, local_model=local, cost_per_decision_usd=daemon.config.settings.explore.cost_per_decision_usd),
        }
    )


async def list_sessions(request: Request, daemon: Daemon) -> Response:
    manager = _manager(daemon)
    live = [json.loads(entry.session.snapshot().model_dump_json()) for entry in manager.sessions.values()]
    return JSONResponse({"sessions": sorted(live, key=lambda item: item["started_at"], reverse=True) + manager.past()})


async def get_session(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _query_number

    session_id = request.path_params["session_id"]
    if _SESSION_ID.match(session_id) and _manager(daemon).get(session_id) is None:
        # A session an earlier daemon ran: its last saved view, read-only (no events, no steering).
        path = _manager(daemon).explore_dir / session_id / "session.json"
        if path.is_file():
            view = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
            if view.get("status") not in ("finished", "failed", "stopped"):
                view = {**view, "status": "stopped", "message": "the daemon stopped while this session ran"}
            return JSONResponse({**view, "events": [], "last_seq": 0, "read_only": True})
    entry = _entry(daemon, session_id)
    wait_s = _query_number(request, "wait_s", float, default=0.0, low=0, high=MAX_WAIT_S)
    since = _query_number(request, "since", int, default=0, low=0, high=10**9)
    done = entry.task is not None and entry.task.done()
    if wait_s and not done and not any(event["seq"] > since for event in entry.events):
        entry.changed.clear()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(entry.changed.wait(), wait_s)
    view = json.loads(entry.session.snapshot().model_dump_json())
    view["events"] = [event for event in entry.events if event["seq"] > since][-200:]
    view["last_seq"] = entry.events[-1]["seq"] if entry.events else 0
    return JSONResponse(view)


async def change_size(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _body, _parse

    entry = _entry(daemon, request.path_params["session_id"])
    body: SizeChange = _parse(SizeChange, await _body(request))
    if body.workers is None and body.reproducers is None:
        raise _api_error(422, "give workers and/or reproducers")
    try:
        entry.session.set_size(body.workers, body.reproducers, by=body.by, allow_more=body.allow_more_workers)
    except ValueError as exc:
        raise _api_error(422, str(exc)) from None
    return JSONResponse(json.loads(entry.session.snapshot().model_dump_json()))


async def stop_session(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _parse

    entry = _entry(daemon, request.path_params["session_id"])
    body: StopRequest = _parse(StopRequest, await _body_or_empty(request))
    entry.session.stop(by=body.by)
    entry.changed.set()
    return JSONResponse({"stopping": True})


async def add_charter(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _body, _parse

    entry = _entry(daemon, request.path_params["session_id"])
    body: CharterAdd = _parse(CharterAdd, await _body(request))
    try:
        charter = entry.session.add_charter(body.title, area=body.area, levers=body.levers or None, timebox_s=body.timebox_s, pinned=body.pinned, by=body.by)
    except ValueError as exc:
        raise _api_error(422, str(exc)) from None
    return JSONResponse(json.loads(charter.model_dump_json()), status_code=201)


async def steer_charter(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _body, _parse

    entry = _entry(daemon, request.path_params["session_id"])
    body: CharterSteer = _parse(CharterSteer, await _body(request))
    try:
        charter = entry.session.steer(request.path_params["charter_id"], action=body.action, by=body.by, note=body.note)
    except ValueError as exc:
        raise _api_error(409, str(exc)) from None
    return JSONResponse(json.loads(charter.model_dump_json()))


async def session_report(request: Request, daemon: Daemon) -> Response:
    session_id = request.path_params["session_id"]
    if not _SESSION_ID.match(session_id):
        raise _api_error(404, "no such session")
    folder = _manager(daemon).explore_dir / session_id
    fmt = request.query_params.get("format", "md")
    path = folder / ("report.json" if fmt == "json" else "report.md")
    if not path.is_file():
        raise _api_error(404, "the report is written when the session ends")
    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    if fmt == "json":
        return JSONResponse(json.loads(text))
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


async def list_findings(request: Request, daemon: Daemon) -> Response:
    ledger = _manager(daemon).ledger
    findings = await asyncio.to_thread(ledger.all)
    state = request.query_params.get("state")
    case = request.query_params.get("case")
    session = request.query_params.get("session")
    if case is not None and case not in _CASE_DECISIONS:
        raise _api_error(422, f"case must be one of {', '.join(_CASE_DECISIONS)}")
    chosen = [item for item in findings if (state is None or item.state == state) and (case is None or item.case == case) and (session is None or session in item.sessions)]
    items = [json.loads(item.model_dump_json()) for item in chosen]
    you = await asyncio.to_thread(default_triager, daemon.config.settings.project_dir)
    return JSONResponse({"findings": items, "total": len(items), "precision": ledger.precision(), "you": you})


async def get_finding(request: Request, daemon: Daemon) -> Response:
    finding = await asyncio.to_thread(_finding, daemon, request.path_params["finding_id"])
    return JSONResponse(json.loads(finding.model_dump_json()))


async def triage_finding(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _body, _parse

    finding_id = (await asyncio.to_thread(_finding, daemon, request.path_params["finding_id"])).id
    body: Triage = _parse(Triage, await _body(request))
    if body.duplicate_of:
        body = body.model_copy(update={"duplicate_of": (await asyncio.to_thread(_finding, daemon, body.duplicate_of)).id})
    try:
        finding = await asyncio.to_thread(
            _manager(daemon).ledger.triage,
            finding_id,
            by=body.by,
            state=body.state,
            severity=body.severity,
            priority=body.priority,
            tracker=body.tracker,
            duplicate_of=body.duplicate_of,
            case=body.case,
            reason=body.reason,
        )
    except TriageError as exc:
        raise _api_error(409, str(exc)) from None
    return JSONResponse(json.loads(finding.model_dump_json()))


async def finding_issue(request: Request, daemon: Daemon) -> Response:
    finding = await asyncio.to_thread(_finding, daemon, request.path_params["finding_id"])
    tracker = request.query_params.get("tracker", "markdown")
    if tracker not in TRACKERS:
        raise _api_error(422, f"tracker must be one of {', '.join(TRACKERS)}")
    body: dict[str, Any] = {"title": issue_title(finding), "body": issue_text(finding, tracker), "tracker": tracker}
    repository = request.query_params.get("repository")
    if tracker == "github" and repository:
        if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repository):
            raise _api_error(422, "repository is owner/name")
        body["url"] = github_new_issue_url(repository, finding)
    return JSONResponse(body)


async def start_reverify(request: Request, daemon: Daemon) -> Response:
    """Rerun the kept and regression cases in the background; follow it with ``GET /api/explore/reverify/{id}``."""
    from testzeus_hercules.daemon.app import _parse

    body: ReverifyStart = _parse(ReverifyStart, await _body_or_empty(request))
    only = [(await asyncio.to_thread(_finding, daemon, raw)).id for raw in body.findings] if body.findings else None
    try:
        job = _manager(daemon).start_reverify(body.build, case_timeout_s=body.case_timeout_s, only=only)
    except ValueError as exc:
        raise _api_error(409, str(exc)) from None
    return JSONResponse(job.view(), status_code=202)


async def get_reverify(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _query_number

    job_id = request.path_params["job_id"]
    job = _manager(daemon).reverifies.get(job_id) if _REVERIFY_ID.match(job_id) else None
    if job is None:
        raise _api_error(404, "no such reverify (jobs are kept in memory until the daemon restarts)")
    wait_s = _query_number(request, "wait_s", float, default=0.0, low=0, high=MAX_WAIT_S)
    since = _query_number(request, "since", int, default=0, low=0, high=10**6)
    if wait_s and job.status == "running" and len(job.lines) <= since:
        job.changed.clear()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(job.changed.wait(), wait_s)
    return JSONResponse(job.view())


def explore_routes() -> list[Any]:
    from starlette.routing import Route

    from testzeus_hercules.daemon.app import _endpoint

    return [
        Route("/api/explore", _endpoint(list_sessions), methods=["GET"]),
        Route("/api/explore", _endpoint(start_session), methods=["POST"]),
        Route("/api/explore/preflight", _endpoint(preflight), methods=["POST"]),
        Route("/api/explore/findings", _endpoint(list_findings), methods=["GET"]),
        Route("/api/explore/findings/{finding_id}", _endpoint(get_finding), methods=["GET"]),
        Route("/api/explore/reverify", _endpoint(start_reverify), methods=["POST"]),
        Route("/api/explore/reverify/{job_id}", _endpoint(get_reverify), methods=["GET"]),
        Route("/api/explore/findings/{finding_id}/triage", _endpoint(triage_finding), methods=["POST"]),
        Route("/api/explore/findings/{finding_id}/issue", _endpoint(finding_issue), methods=["GET"]),
        Route("/api/explore/{session_id}", _endpoint(get_session), methods=["GET"]),
        Route("/api/explore/{session_id}", _endpoint(change_size), methods=["PATCH"]),
        Route("/api/explore/{session_id}/stop", _endpoint(stop_session), methods=["POST"]),
        Route("/api/explore/{session_id}/charters", _endpoint(add_charter), methods=["POST"]),
        Route("/api/explore/{session_id}/charters/{charter_id}", _endpoint(steer_charter), methods=["POST"]),
        Route("/api/explore/{session_id}/report", _endpoint(session_report), methods=["GET"]),
    ]
