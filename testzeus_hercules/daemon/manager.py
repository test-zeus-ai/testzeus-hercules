"""Run queue and worker processes.

Each run executes in its own subprocess (``hercules worker``) started in a new
session, so the worker and its browser form one process group. The worker streams
JSON-lines events on stdout (``{"kind": ..., "data": {...}}``); anything else on
stdout is ignored and stderr goes to ``<run dir>/worker.log``.

* Crash isolation: a worker that dies without writing ``run.json`` marks its run
  ``error`` with the exit code or signal as the reason; leftover processes in its
  group are killed.
* Cancel: SIGTERM to the group, SIGKILL after a grace period.
* Concurrency: at most ``parallel_runs`` workers at a time; the rest stay queued.
* Control: the daemon writes JSON-lines messages to the worker's stdin (pause, resume,
  step, skip, retry, approvals, screencast on/off, input while paused); the worker's
  ``paused``/``resumed``/``needs_approval``/``approval_result`` events move the run
  between ``running``, ``paused`` and ``needs_approval``.
* Live view: screencast frames come on a separate pipe and are relayed to WebSocket
  viewers by :class:`~testzeus_hercules.daemon.screencast.ScreencastHub`; the worker
  streams only while someone watches.
* Resume: a run whose worker died (status ``error``) can be queued again with
  ``resume``; its worker continues from the last completed step.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from testzeus_hercules.act.redact import mask_url_credentials
from testzeus_hercules.cases.models import RunResult, TestCase
from testzeus_hercules.cases.plan import Plan
from testzeus_hercules.daemon.config import DaemonConfig
from testzeus_hercules.daemon.hub import EventHub
from testzeus_hercules.daemon.screencast import ScreencastHub, read_frames
from testzeus_hercules.daemon.store import FINAL_STATUSES, LIVE_STATUSES, RunStore, now_iso
from testzeus_hercules.engine.browser_process import stop_browser

logger = logging.getLogger(__name__)

_EVENT_KIND = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_MAX_EVENT_BYTES = 64 * 1024
_MAX_LINE_BYTES = 1024 * 1024
_ACTION_CLIP = 300
_REASON_CLIP = 500
_SCHEDULER_TICK_S = 1.0
_READER_DRAIN_S = 2.0
# Next to the runs directory: the cases workers read (with their real values), each removed when its worker exits.
_CASE_INPUTS = "run-inputs"
_LIVE = tuple(sorted(LIVE_STATUSES))
# In the run directory: a plan the run was started with, and a reviewer's edited plan (the worker reads them).
GIVEN_PLAN_FILE = "plan.given.json"
EDITED_PLAN_FILE = "plan.edited.json"
# A plan job (POST /api/plans) may take this long, waiting for a slot included: an MCP tool call returns within 55 s.
PLAN_JOB_TIMEOUT_S = 50.0
# Plan jobs at once (each is a worker process that calls the model); more wait for a slot.
MAX_PLAN_JOBS = 2


class RunConflict(Exception):
    """The run is not in a state that allows the request."""


class RunNotFound(RunConflict):
    """No run has this id (it was never created, or the history was reset)."""


def new_run_id() -> str:
    """Sortable, unique run id such as ``r_20261025_141502_9f3a1c`` (UTC)."""
    return f"r_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{secrets.token_hex(3)}"


def read_result(run_dir: Path) -> RunResult | None:
    """The worker's ``run.json``, or None when it is missing or invalid."""
    path = run_dir / "run.json"
    if path.is_symlink():
        return None
    try:
        return RunResult.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError):
        return None


def _signal_group(pid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


def describe_exit(returncode: int) -> str:
    if returncode < 0:
        try:
            name = signal.Signals(-returncode).name
        except ValueError:
            name = f"signal {-returncode}"
        return f"the worker was killed by {name} before it wrote a result"
    return f"the worker exited with code {returncode} without a result (see worker.log in the run directory)"


class RunManager:
    def __init__(self, config: DaemonConfig, store: RunStore, hub: EventHub) -> None:
        self.config = config
        self.store = store
        self.hub = hub
        self.runs_dir = config.paths.runs_dir
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._supervisors: dict[str, asyncio.Task[None]] = {}
        self._launching: set[str] = set()
        self._cancel_reason: dict[str, str] = {}
        self._escalations: dict[str, asyncio.Task[None]] = {}
        self._wake = asyncio.Event()
        self._scheduler: asyncio.Task[None] | None = None
        self._stopping = False
        self._frame_readers: dict[str, asyncio.Task[int]] = {}
        self._resuming: set[str] = set()
        # Per live run: the steps that finished (steps_done counts them; steps may run out of order).
        self._finished: dict[str, set[int]] = {}
        self._plan_slots = asyncio.Semaphore(MAX_PLAN_JOBS)
        self._plan_procs: set[asyncio.subprocess.Process] = set()
        self.screencast = ScreencastHub(on_viewers=self._viewers_changed)
        # Slots exploration sessions reserved for their children, beyond parallel_runs (the swarm size the user set).
        self.reserved = 0

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._sweep_inputs()
        for run_id in self.store.recover_after_restart():
            self.store.add_event(run_id, "status", {"status": "error", "reason": "the daemon stopped while this run was running"})
        self._scheduler = asyncio.create_task(self._schedule_loop(), name="hercules-scheduler")

    def _sweep_inputs(self) -> None:
        """Remove the case inputs (real values) and plan outputs a previous daemon left when it stopped without cleaning up:
        no worker of this daemon has started yet, so none is in use."""
        inputs = self.runs_dir.parent / _CASE_INPUTS
        if not inputs.is_dir() or inputs.is_symlink():
            return
        for path in inputs.iterdir():
            if path.is_file() or path.is_symlink():
                with contextlib.suppress(OSError):
                    path.unlink()

    async def stop(self) -> None:
        """Terminate every worker (SIGTERM, then SIGKILL after the grace period) and plan job, and stop scheduling."""
        self._stopping = True
        for proc in list(self._plan_procs):
            _signal_group(proc.pid, signal.SIGKILL)
        if self._scheduler is not None:
            self._scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._scheduler
        for run_id, proc in list(self._procs.items()):
            self._cancel_reason.setdefault(run_id, "cancelled: the daemon shut down")
            _signal_group(proc.pid, signal.SIGTERM)
        supervisors = list(self._supervisors.values())
        if supervisors:
            _, pending = await asyncio.wait(supervisors, timeout=self.config.cancel_grace_s)
            if pending:
                for proc in list(self._procs.values()):
                    _signal_group(proc.pid, signal.SIGKILL)
                await asyncio.wait(pending, timeout=self.config.cancel_grace_s + _READER_DRAIN_S)

    def wake(self) -> None:
        self._wake.set()

    @property
    def active(self) -> int:
        return len(self._procs) + len(self._launching)

    # ------------------------------------------------------------------ requests

    def submit(
        self, case: TestCase, *, options: Mapping[str, Any], source: Mapping[str, Any], parent_run_id: str | None = None, auto_approve: bool = False, plan: Plan | None = None
    ) -> dict[str, Any]:
        options = dict(options)
        if case.status == "draft" and options.get("plan_review") is None:
            options["plan_review"] = "ask"  # a drafted case's plan is reviewed before it runs
        if case.status == "draft" and auto_approve:
            case = case.model_copy(update={"status": "ready"})
        status = "needs_approval" if case.status == "draft" else "queued"
        run_id = new_run_id()
        if plan is not None:  # an agent's (edited) plan: the worker runs it, checked against the case
            run_dir = self.runs_dir / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / GIVEN_PLAN_FILE).write_text(plan.model_dump_json(indent=1), encoding="utf-8")
        record = self.store.create_run(run_id, case=case.model_dump(mode="json", exclude_none=True), options=options, status=status, source=source, parent_run_id=parent_run_id)
        self._status_event(run_id, status, "review the drafted steps, then approve the run" if status == "needs_approval" else None)
        self.wake()
        return record

    def approve(self, run_id: str) -> None:
        record = self._require(run_id)
        if record["status"] != "needs_approval":
            raise RunConflict(f"run {run_id} is {record['status']}; only runs in needs_approval can be approved")
        case = {**record["case"], "status": "ready"}
        if not self.store.transition(run_id, ("needs_approval",), "queued", case_json=json.dumps(case)):
            raise RunConflict(f"run {run_id} changed state; try again")
        self._status_event(run_id, "queued", "approved")
        self.wake()

    def decide(self, run_id: str, decision: str, step_text: str | None = None, plan: Plan | None = None, approval_id: str | None = None) -> None:
        """Answer a run waiting in ``needs_approval`` (a draft before it starts, or a running worker's question) or in
        ``needs_plan_approval`` (its plan: approve, reject, or edit with the edited ``plan``). With ``approval_id`` the
        answer is for that question only: :class:`RunConflict` when the run waits on another one, or on none."""
        record = self._require(run_id)
        if record["status"] == "needs_plan_approval":
            self._decide_plan(run_id, record, decision, plan, approval_id)
            return
        if record["status"] != "needs_approval":
            raise RunConflict(f"run {run_id} is {record['status']}; only runs in needs_approval or needs_plan_approval can be approved or rejected")
        if run_id not in self._procs:  # a drafted case that has not started
            if approval_id is not None:
                raise RunConflict(f"run {run_id} has no question {approval_id!r} waiting (it waits for its drafted steps to be approved)")
            if decision == "approve":
                self.approve(run_id)
            elif decision == "reject":
                self.cancel(run_id)
            else:
                raise RunConflict("a drafted run cannot be edited here; change the case and start it again")
            return
        request = _matching(run_id, self.pending_approval(run_id), approval_id, "question")
        if decision == "edit" and not request.get("allow_edit", True):
            raise RunConflict(f"a {request.get('kind')} approval cannot be edited")
        message: dict[str, Any] = {"type": "approval", "id": request.get("id"), "decision": decision}
        if decision == "edit":
            message["step_text"] = step_text
        self._send(run_id, message)

    def _decide_plan(self, run_id: str, record: dict[str, Any], decision: str, plan: Plan | None, approval_id: str | None) -> None:
        request = _matching(run_id, self.pending_approval(run_id), approval_id, "plan")
        message: dict[str, Any] = {"type": "approval", "id": request.get("id"), "decision": decision}
        if decision == "edit":
            if plan is None:
                raise RunConflict("editing a plan needs the edited plan (plan)")
            if plan.case_id != record.get("case_id"):
                raise RunConflict(f"the edited plan is for case {plan.case_id!r}, not this run's case {record.get('case_id')!r}")
            (self.runs_dir / run_id / EDITED_PLAN_FILE).write_text(plan.model_dump_json(indent=1), encoding="utf-8")
        self._send(run_id, message)

    def pending_approval(self, run_id: str) -> dict[str, Any] | None:
        """The question a running worker waits on (the latest ``needs_approval`` event), if it still waits."""
        record = self.store.get_run(run_id)
        if record is None or record["status"] not in ("needs_approval", "needs_plan_approval") or not record.get("worker_pid"):
            return None
        event = self.store.last_event(run_id, ("needs_approval", "approval_result"))
        return dict(event["data"]) if event is not None and event["kind"] == "needs_approval" else None

    def control(self, run_id: str, action: str) -> None:
        """Pause, resume, step, skip or retry a running run."""
        record = self._require(run_id)
        status = record["status"]
        if status not in LIVE_STATUSES or run_id not in self._procs:
            raise RunConflict(f"run {run_id} is {status}; only a running run can be controlled")
        if action in ("skip", "retry") and status != "paused":
            raise RunConflict(f"{action} needs the run to be paused (it is {status})")
        self._send(run_id, {"type": "control", "action": action})

    def send_input(self, run_id: str, event: Mapping[str, Any]) -> None:
        """Relay a click or keystroke to a paused run's browser (take over)."""
        record = self._require(run_id)
        if record["status"] != "paused" or run_id not in self._procs:
            raise RunConflict(f"run {run_id} is {record['status']}; pause it before driving the browser")
        self._send(run_id, {**event, "type": "input"})

    def resume(self, run_id: str) -> None:
        """Queue a run whose worker died again; its new worker continues from the last completed step."""
        record = self._require(run_id)
        if record["status"] != "error" or read_result(self.runs_dir / run_id) is not None:
            raise RunConflict(f"run {run_id} is {record['status']}; only a run whose worker stopped without a result can be resumed")
        checkpoint = self.config.paths.project_hercules_dir / "checkpoints" / f"{run_id}.db"
        if not checkpoint.is_file():
            raise RunConflict(f"run {run_id} has no checkpoint to resume from")
        if not self.store.transition(run_id, ("error",), "queued", reason=None, finished_at=None, exit_code=None):
            raise RunConflict(f"run {run_id} changed state; try again")
        self._resuming.add(run_id)
        self.screencast.forget(run_id)
        self._status_event(run_id, "queued", "resuming from the last completed step")
        self.wake()

    def _send(self, run_id: str, message: Mapping[str, Any]) -> None:
        proc = self._procs.get(run_id)
        if proc is None or proc.stdin is None or proc.stdin.is_closing():
            raise RunConflict(f"run {run_id} has no worker to talk to")
        try:
            proc.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            raise RunConflict(f"the worker of run {run_id} is not listening") from None

    def _viewers_changed(self, run_id: str, count: int) -> None:
        if count > 1 or run_id not in self._procs:
            return
        with contextlib.suppress(RunConflict):
            self._send(run_id, {"type": "screencast", "on": count == 1})

    def cancel(self, run_id: str) -> None:
        record = self._require(run_id)
        status = record["status"]
        if status in FINAL_STATUSES:
            raise RunConflict(f"run {run_id} already finished ({status})")
        if status in ("queued", "needs_approval") and run_id not in self._procs:
            reason = "cancelled before it started"
            if self.store.transition(run_id, ("queued", "needs_approval"), "cancelled", reason=reason, finished_at=now_iso()):
                self._status_event(run_id, "cancelled", reason)
                return
        proc = self._procs.get(run_id)
        if proc is None:  # it moved on (started, or finished) between the read and the transition: never label another outcome
            raise RunConflict(f"run {run_id} changed state; try again")
        self._cancel_reason.setdefault(run_id, "cancelled by request")
        if run_id not in self._escalations:
            _signal_group(proc.pid, signal.SIGTERM)
            self._escalations[run_id] = asyncio.create_task(self._escalate(proc), name=f"hercules-cancel-{run_id}")

    async def _escalate(self, proc: asyncio.subprocess.Process) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), self.config.cancel_grace_s)
        if proc.returncode is None:
            _signal_group(proc.pid, signal.SIGKILL)

    def _require(self, run_id: str) -> dict[str, Any]:
        record = self.store.get_run(run_id)
        if record is None:
            raise RunNotFound(f"run {run_id} not found")
        return record

    # ------------------------------------------------------------------ scheduling

    async def _schedule_loop(self) -> None:
        while not self._stopping:
            self._wake.clear()
            for run_id in self.store.queued():
                if self.active >= self.config.parallel_runs + self.reserved:
                    break
                if run_id in self._procs or run_id in self._launching:
                    continue
                self._launching.add(run_id)
                try:
                    await self._launch(run_id)
                except Exception as exc:  # never let one bad run stop the queue
                    logger.exception("launching run %s failed", run_id)
                    self._fail_launch(run_id, f"could not start the worker ({type(exc).__name__})")
                finally:
                    self._launching.discard(run_id)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), _SCHEDULER_TICK_S)

    def _worker_args(self, run_id: str, case_path: Path, options: Mapping[str, Any]) -> list[str]:
        args = [*self.config.worker_command, "--run-id", run_id, "--case", str(case_path), "--project", str(self.config.settings.project_dir)]
        if options.get("mode"):
            args += ["--mode", str(options["mode"])]
        if options.get("egress"):
            args += ["--egress", str(options["egress"])]
        if options.get("decider"):
            args += ["--decider", str(options["decider"])]
        if options.get("headed"):
            args.append("--headed")
        if options.get("max_steps"):
            args += ["--max-steps", str(int(options["max_steps"]))]
        if options.get("step_timeout_s"):
            args += ["--step-timeout-s", str(float(options["step_timeout_s"]))]
        if options.get("confirm_destructive"):
            args.append("--confirm-destructive")
        if options.get("destructive_terms"):
            args += ["--destructive-terms", ",".join(str(term) for term in options["destructive_terms"])]
        if options.get("ask_on_failure"):
            args.append("--ask-on-failure")
        if options.get("approval_timeout_s"):
            args += ["--approval-timeout-s", str(float(options["approval_timeout_s"]))]
        if options.get("a11y"):
            args.append("--a11y")
        if options.get("visual"):
            args.append("--visual")
        if options.get("fail_on") and (options.get("a11y") or options.get("visual")):
            args += ["--quality-fail-on", str(options["fail_on"])]
        if options.get("strict"):
            args.append("--strict")
        if options.get("plan_review") in ("auto", "ask"):
            args += ["--plan-review", str(options["plan_review"])]
        if options.get("explore_job"):
            args += ["--explore", str(options["explore_job"])]
        given = self.runs_dir / run_id / GIVEN_PLAN_FILE
        if given.is_file() and not given.is_symlink():
            args += ["--plan", str(given)]
        args.append("--control")
        if run_id in self._resuming:
            args.append("--resume")
            args.append(run_id)
        return args

    async def _launch(self, run_id: str) -> None:
        record = self._require(run_id)
        if record["status"] != "queued":
            return
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        case_text = json.dumps(record["case"], indent=2)
        # The run directory keeps a copy with URL credentials masked (it is evidence); the worker reads the case from a
        # private file outside it, removed when the worker exits (by its supervisor) or here when no worker took it.
        (run_dir / "case.json").write_text(mask_url_credentials(case_text), encoding="utf-8")
        case_path = self._case_input(run_id)
        supervised = False
        try:
            _write_private(case_path, case_text)
            supervised = await self._spawn(run_id, record, case_path)
        finally:
            if not supervised:
                with contextlib.suppress(OSError):
                    case_path.unlink(missing_ok=True)

    async def _spawn(self, run_id: str, record: dict[str, Any], case_path: Path) -> bool:
        """Start the run's worker and its supervisor; False when the run left ``queued`` meanwhile (the worker is killed)."""
        run_dir = self.runs_dir / run_id
        env = {**self.config.worker_env, "PYTHONUNBUFFERED": "1", **_origin_env(record.get("source"))}
        frames_read, frames_write = os.pipe()
        try:
            with (run_dir / "worker.log").open("ab") as log:
                proc = await asyncio.create_subprocess_exec(
                    *self._worker_args(run_id, case_path, record["options"]),
                    "--frames-fd",
                    str(frames_write),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=log,
                    env=env,
                    cwd=str(self.config.settings.project_dir),
                    start_new_session=True,
                    limit=_MAX_LINE_BYTES,
                    pass_fds=(frames_write,),
                )
        except BaseException:
            os.close(frames_read)
            raise
        finally:
            os.close(frames_write)
        resumed = run_id in self._resuming
        self._resuming.discard(run_id)
        fields: dict[str, Any] = {"worker_pid": proc.pid} if resumed else {"started_at": now_iso(), "worker_pid": proc.pid}
        if not self.store.transition(run_id, ("queued",), "running", **fields):
            os.close(frames_read)
            _signal_group(proc.pid, signal.SIGKILL)
            await proc.wait()
            return False
        self._procs[run_id] = proc
        self._frame_readers[run_id] = asyncio.create_task(self._read_frames(run_id, frames_read), name=f"hercules-frames-{run_id}")
        self._status_event(run_id, "running", None)
        self._supervisors[run_id] = asyncio.create_task(self._supervise(run_id, proc), name=f"hercules-run-{run_id}")
        return True

    async def plan_job(self, case: TestCase, options: Mapping[str, Any]) -> Plan:
        """Make ``case``'s plan in a worker process (``hercules worker --plan-only``), without running it; raises
        :class:`RunConflict` with the worker's reason when it cannot. At most ``MAX_PLAN_JOBS`` run at once; a job that
        is cancelled (the client left, the daemon stops) kills its worker and removes its files."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PLAN_JOB_TIMEOUT_S
        try:
            await asyncio.wait_for(self._plan_slots.acquire(), PLAN_JOB_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise RunConflict(f"{MAX_PLAN_JOBS} plans are being made; try again in a minute") from None
        try:
            return await self._plan(case, options, deadline - loop.time())
        finally:
            self._plan_slots.release()

    async def _plan(self, case: TestCase, options: Mapping[str, Any], timeout_s: float) -> Plan:
        job_id = "p" + new_run_id()[1:]
        case_path = self._case_input(job_id)
        out = self.runs_dir.parent / _CASE_INPUTS / f"{job_id}.plan.json"
        args = [*self.config.worker_command, "--run-id", job_id, "--case", str(case_path), "--project", str(self.config.settings.project_dir), "--plan-only", "--plan-out", str(out)]
        for name in ("mode", "egress", "decider"):
            if options.get(name):
                args += [f"--{name}", str(options[name])]
        if options.get("strict"):
            args.append("--strict")
        if options.get("confirm_destructive"):
            args.append("--confirm-destructive")
        proc: asyncio.subprocess.Process | None = None
        try:
            _write_private(case_path, json.dumps(case.model_dump(mode="json", exclude_none=True)))
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env={**self.config.worker_env, "PYTHONUNBUFFERED": "1"},
                cwd=str(self.config.settings.project_dir),
                start_new_session=True,
            )
            self._plan_procs.add(proc)
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), max(timeout_s, 0.1))
            except asyncio.TimeoutError:
                _signal_group(proc.pid, signal.SIGKILL)
                await proc.wait()
                raise RunConflict(f"making the plan took longer than {PLAN_JOB_TIMEOUT_S:g} s; try `hercules plan` in a terminal") from None
            except BaseException:  # cancelled (the request went away, the daemon stops): never leave the worker running
                _signal_group(proc.pid, signal.SIGKILL)
                raise
            if proc.returncode != 0 or not out.is_file():
                lines = [line for line in mask_url_credentials(stderr.decode("utf-8", "replace")).splitlines() if line.strip()]
                raise RunConflict(lines[-1][:_REASON_CLIP] if lines else f"the plan worker exited with code {proc.returncode}")
            try:
                return Plan.model_validate_json(out.read_bytes())
            except (OSError, ValidationError, ValueError):
                raise RunConflict("the plan worker wrote no valid plan") from None
        finally:
            if proc is not None:
                self._plan_procs.discard(proc)
            for path in (case_path, out):
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)

    def _case_input(self, run_id: str) -> Path:
        """Where the worker of ``run_id`` reads its case from (outside the run directory, private to this user)."""
        return self.runs_dir.parent / _CASE_INPUTS / f"{run_id}.json"

    def _fail_launch(self, run_id: str, reason: str) -> None:
        if self.store.transition(run_id, ("queued",), "error", reason=reason, finished_at=now_iso()):
            self._status_event(run_id, "error", reason)

    # ------------------------------------------------------------------ supervision

    async def _supervise(self, run_id: str, proc: asyncio.subprocess.Process) -> None:
        errors: list[str] = []
        reader = asyncio.create_task(self._read_events(run_id, proc, errors))
        try:
            returncode = await proc.wait()
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(reader, _READER_DRAIN_S)
            _signal_group(proc.pid, signal.SIGKILL)  # leftover processes of a crashed worker's own group
            # Chromium runs in its own process group: stop the browser a dead worker left (checked to be that run's browser).
            try:
                await asyncio.to_thread(stop_browser, self.runs_dir / run_id)
            except Exception:  # noqa: BLE001 - the run's outcome stands; say why its browser may still be open
                logger.warning("could not stop the browser of run %s", run_id, exc_info=True)
            self._finish(run_id, returncode, errors)
        finally:
            with contextlib.suppress(OSError):
                self._case_input(run_id).unlink(missing_ok=True)
            reader.cancel()
            frames = self._frame_readers.pop(run_id, None)
            if frames is not None:
                frames.cancel()
            if proc.stdin is not None:
                proc.stdin.close()
            self._procs.pop(run_id, None)
            self._supervisors.pop(run_id, None)
            self._escalations.pop(run_id, None)
            self._cancel_reason.pop(run_id, None)
            self._finished.pop(run_id, None)
            self.wake()

    async def _read_frames(self, run_id: str, fd: int) -> int:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=_MAX_LINE_BYTES)
        pipe = os.fdopen(fd, "rb", buffering=0)
        transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), pipe)
        try:
            return await read_frames(reader, lambda jpeg, width, height, captured: self.screencast.publish(run_id, jpeg, width, height, captured))
        finally:
            transport.close()

    async def _read_events(self, run_id: str, proc: asyncio.subprocess.Process, errors: list[str]) -> None:
        assert proc.stdout is not None
        while True:
            try:
                line = await proc.stdout.readline()
            except ValueError:  # an over-long line was discarded
                continue
            if not line:
                return
            event = _parse_event(line)
            if event is None:
                continue
            kind, data = event
            if kind == "error" and isinstance(data.get("message"), str):
                errors.append(data["message"][:_REASON_CLIP])
            self._record(run_id, kind, data)

    def _record(self, run_id: str, kind: str, data: dict[str, Any]) -> None:
        fields: dict[str, Any] = {}
        step = data.get("step")
        if kind == "run_started" and isinstance(data.get("steps"), int):
            fields["total_steps"] = data["steps"]
        elif kind == "step_started" and isinstance(step, int):
            fields["current_step"] = step
            fields["current_action"] = str(data.get("action", ""))[:_ACTION_CLIP]
        elif kind == "step_finished" and isinstance(step, int):
            # Steps may run out of order (a field that depends on a later field step): count the steps that finished.
            finished = self._finished.setdefault(run_id, set())
            finished.add(step)
            fields["steps_done"] = len(finished)
        if fields:
            self.store.update_run(run_id, **fields)
        self.store.add_event(run_id, kind, data)
        self._follow_state(run_id, kind, data)
        self.hub.notify(run_id)

    def _follow_state(self, run_id: str, kind: str, data: dict[str, Any]) -> None:
        """Move the run between running, paused and needs_approval as its worker reports."""
        step = data.get("step")
        expected: tuple[str, ...]
        if kind == "paused":
            where = f"before step {step}" if data.get("where") == "step" else f"in step {step}, before its next action"
            target, expected, reason = "paused", ("running",), f"paused {where}"
        elif kind == "resumed":
            target, expected, reason = "running", ("paused",), None
        elif kind == "needs_approval":
            target = "needs_plan_approval" if data.get("kind") == "plan" else "needs_approval"
            expected, reason = ("running", "paused"), str(data.get("reason") or "waiting for approval")[:_REASON_CLIP]
        elif kind == "approval_result":
            target = "paused" if data.get("state") == "paused" else "running"
            expected, reason = ("needs_approval", "needs_plan_approval"), ("paused after the approval" if target == "paused" else None)
        else:
            return
        if self.store.transition(run_id, expected, target, reason=reason):
            self._status_event(run_id, target, reason)

    def _finish(self, run_id: str, returncode: int, errors: list[str]) -> None:
        result = read_result(self.runs_dir / run_id)
        cancel_reason = self._cancel_reason.get(run_id)
        reason: str | None = None
        if cancel_reason is not None and (result is None or result.status == "cancelled" or returncode < 0):
            status, reason = "cancelled", cancel_reason
        elif result is not None and result.status in FINAL_STATUSES:
            status = result.status
        elif result is not None:
            status, reason = "error", f"the worker reported an unfinished status ({result.status})"
        else:
            status = "error"
            reason = errors[-1] if errors else describe_exit(returncode)
        if self.store.transition(run_id, _LIVE, status, reason=reason, finished_at=now_iso(), exit_code=returncode, worker_pid=None):
            self._status_event(run_id, status, reason)
        self.screencast.end(run_id, status)

    def _status_event(self, run_id: str, status: str, reason: str | None) -> None:
        data: dict[str, Any] = {"status": status}
        if reason:
            data["reason"] = reason
        self.store.add_event(run_id, "status", data)
        self.hub.notify(run_id)


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` readable by this user only (in a directory only this user can enter)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)


def _matching(run_id: str, request: dict[str, Any] | None, approval_id: str | None, what: str) -> dict[str, Any]:
    """The question the worker waits on, checked against the one the answer is for (when the caller named it)."""
    if request is None:
        missing = f"no {what} waiting for approval" if approval_id is None else f"no question {approval_id!r} waiting (it was answered or timed out)"
        raise RunConflict(f"run {run_id} has {missing}")
    if approval_id is not None and approval_id != request.get("id"):
        raise RunConflict(f"run {run_id} no longer waits on question {approval_id!r}; it now asks {request.get('id')!r}: read it before answering")
    return request


def _parse_event(line: bytes) -> tuple[str, dict[str, Any]] | None:
    if len(line) > _MAX_EVENT_BYTES:
        return None
    try:
        payload = json.loads(line)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    kind, data = payload.get("kind"), payload.get("data", {})
    if not isinstance(kind, str) or not _EVENT_KIND.match(kind) or kind == "status" or not isinstance(data, dict):
        return None
    return kind, data


def _origin_env(source: Any) -> dict[str, str]:
    """The worker's environment entries that tell its telemetry who started the run (the daemon, or a coding agent)."""
    from testzeus_hercules.kernel.telemetry import AGENT_CLIENT_ENV, AGENT_CLIENTS, RUN_ORIGIN_ENV

    source = source if isinstance(source, dict) else {}
    if source.get("origin") == "agent" and source.get("agent") in AGENT_CLIENTS:
        return {RUN_ORIGIN_ENV: "agent", AGENT_CLIENT_ENV: source["agent"]}
    return {RUN_ORIGIN_ENV: "daemon"}
