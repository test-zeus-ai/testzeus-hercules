"""The session coordinator: plans, schedules and gathers; never drives a browser itself.

Phases: **prepare** (mission, scope, production guard, the swarm size against this machine, the starting state),
**setup** (the start case, once per account in ``accounts`` isolation), **recon** (one explorer learns the app and the
baseline noise), **explore** (charters to explorers, at most ``workers`` at a time; clues to reproducers, at most
``reproducers`` at a time; promising clues get dig charters), then **wrap up** (a grace period for reproductions, the
teardown case, the report).

The swarm size is the user's: the coordinator schedules within it and never changes it. A size change comes only from
a person (:meth:`Session.set_size`), and is logged. Explorers and reproducers are child runs started through a
:class:`ChildRunner` (the daemon's run manager, or a test double).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from testzeus_hercules.cases.models import EnvironmentRef, RunResult, SecretRef, TestCase
from testzeus_hercules.explore.charters import candidate_charters, choose_charter, dig_charter
from testzeus_hercules.explore.explorer import EXPLORE_DIR, ExploreJob, read_jsonl
from testzeus_hercules.explore.ledger import Ledger
from testzeus_hercules.explore.missions import Mission, load_mission, load_rubric, rank
from testzeus_hercules.explore.models import Charter, Clue, EnvironmentInfo, ExploreSpec, Finding, Move, Promise, SessionView, Turn, now_iso
from testzeus_hercules.explore.reproduce import FINDING_FILE, ReproJob
from testzeus_hercules.explore.scope import production_host

__all__ = ["ChildRunner", "Limits", "Session", "size_problems"]

FINAL = frozenset({"passed", "failed", "error", "cancelled"})
BASELINE_ORACLES = frozenset({"console_error", "request_failed"})
_MAX_DIGS_PER_ROUTE = 2
_HISTORY_KEPT = 60  # earlier moves a reproduction may put in front of a clue (minimizing then keeps the needed ones)
_TIER_ORDER = {"code": 0, "invariant": 1, "judged": 2}
_SETUP_TIMEOUT_S = 900  # the start case, once per account
_TEARDOWN_TIMEOUT_S = 600
_BYTES_PER_BROWSER = 400 * 1024 * 1024


class ChildRunner(Protocol):
    async def start(self, *, case: TestCase, job: Mapping[str, Any], label: str) -> str: ...
    def status(self, run_id: str) -> str: ...
    def run_dir(self, run_id: str) -> Path: ...
    async def cancel(self, run_id: str) -> None: ...


@dataclass(frozen=True)
class Limits:
    max_workers: int = 8
    parallel_cap: int = 8
    memory_bytes: int | None = None


def size_problems(spec: ExploreSpec, limits: Limits) -> list[str]:
    """What the chosen swarm size exceeds on this machine, in numbers (never a silent cut)."""
    problems = []
    total = spec.workers + spec.reproducers
    if spec.workers > limits.max_workers:
        problems.append(f"{spec.workers} explorers is more than explore.max_workers ({limits.max_workers})")
    if total > limits.parallel_cap:
        problems.append(f"{spec.workers} explorers + {spec.reproducers} reproducers = {total} browsers at once, more than the daemon's limit of {limits.parallel_cap}")
    if limits.memory_bytes:
        need = total * _BYTES_PER_BROWSER
        if need > limits.memory_bytes * 0.7:
            problems.append(f"{total} browsers need about {need / 2**30:.1f} GiB; this machine has {limits.memory_bytes / 2**30:.1f} GiB")
    return problems


def recon_timebox(time_s: int, share: float) -> int:
    """Recon's time: its share of the session, at least 90 s (a real site takes seconds per move, and the charters are
    planned from what recon saw), never more than 40% of the session, at most 10 minutes."""
    return int(min(600, max(90, time_s * share), max(30, time_s * 0.4)))


def estimate(spec: ExploreSpec, *, local_model: bool, cost_per_decision_usd: float = 0.0005) -> dict[str, Any]:
    """A pre-flight guess of the session's model use and cost for the chosen size (shown before it starts)."""
    turns = spec.workers * spec.budget.time_s / 8.0  # about one turn every 8 s per explorer
    decisions = int(turns * 3)  # choose a move, the step's target, the judge
    return {
        "workers": spec.workers,
        "reproducers": spec.reproducers,
        "time_s": spec.budget.time_s,
        "decisions": decisions,
        "cost_usd": 0.0 if local_model else round(decisions * cost_per_decision_usd, 2),
        "note": "a local model answers one decision at a time: explorers wait on each other" if local_model and spec.workers > 1 else "",
    }


@dataclass
class _Child:
    run_id: str
    kind: str  # setup | recon | explore | reproduce
    charter: Charter | None = None
    clue: Clue | None = None
    seen_clues: int = 0


@dataclass
class Session:
    id: str
    spec: ExploreSpec
    project_dir: Path
    runner: ChildRunner
    decider: Any = None
    emit: Callable[[str, dict[str, Any]], None] = lambda kind, data: None
    limits: Limits = field(default_factory=Limits)
    hercules_version: str = ""
    llm: str = ""
    decider_name: str = ""
    poll_s: float = 1.0
    clock: Callable[[], float] = time.monotonic
    # The price of one call to a hosted model that bills no dollars into the run (explore.cost_per_decision_usd).
    cost_per_hosted_call_usd: float = 0.0

    def __post_init__(self) -> None:
        self.dir = self.project_dir / ".hercules" / "explore" / self.id
        self.ledger = Ledger(self.project_dir / ".hercules" / "explore")
        self.view = SessionView(id=self.id, status="starting", spec=self.spec, workers=self.spec.workers, reproducers=self.spec.reproducers)
        self.queue: list[Charter] = []
        self.children: dict[str, _Child] = {}
        self.clue_queue: list[Clue] = []
        self.signatures: set[str] = set()
        self.baseline: dict[str, str] = {}
        self.states: dict[str, dict[str, Any]] = {}
        # Charters planned from the states seen so far: every page state seen after the last plan earns a new plan
        # when the queue runs dry, and a charter that already ran (same page, lever and persona) is not planned again.
        self.planned_states = -1
        self.ran: set[str] = set()
        self.coverage: dict[str, dict[str, list[str]]] = {}
        self.tried: list[list[str]] = []
        # Each signed-in session's finished moves, in the order their charters started: what a reproducer may need in
        # front of a clue whose bug depends on state an earlier charter built.
        self.history: dict[int, list[tuple[str, list[Turn]]]] = {}
        self.debriefs: list[dict[str, Any]] = []
        self.outcomes: dict[str, str] = {}  # clue id -> new | seen_again | regression | suppressed | flaky | not_reproduced
        self.finding_outcomes: dict[str, str] = {}  # finding id -> the ledger's outcome
        self.flaky: list[dict[str, Any]] = []
        self.not_reproduced: list[dict[str, Any]] = []
        self.digs: dict[str, int] = {}
        self.stall = 0
        self.stop_reason = ""
        self._stopping = False
        self.started = self.clock()
        self.deadline = self.started + self.spec.budget.time_s
        self.start_files: list[str] = []
        self.start_url = ""
        self.base_url = ""
        self.case_env: dict[str, Any] = {}
        self.test_data: dict[str, Any] = {}
        self.promises: list[Promise] = []
        self.mission: Mission | None = None
        self.environment = EnvironmentInfo()
        self.blocked: list[dict[str, str]] = []

    # ------------------------------------------------------------------ people steer

    def set_size(self, workers: int | None, reproducers: int | None, *, by: str, allow_more: bool = False) -> None:
        """A person's change of the swarm size (logged). Like a start, a size over this machine's guard rail needs ``allow_more``."""
        wanted = self.spec.model_copy(update={"workers": workers or self.view.workers, "reproducers": reproducers or self.view.reproducers})
        problems = size_problems(wanted, self.limits)
        if problems and not (allow_more or self.spec.allow_more_workers):
            raise ValueError("; ".join(problems) + " (allow it explicitly to go over the guard rail)")
        change: dict[str, Any] = {"at": now_iso(), "by": by}
        if workers is not None:
            if not 1 <= workers <= 64:
                raise ValueError("workers must be 1 to 64")
            change["workers"] = [self.view.workers, workers]
            self.view.workers = workers
        if reproducers is not None:
            if not 1 <= reproducers <= 16:
                raise ValueError("reproducers must be 1 to 16")
            change["reproducers"] = [self.view.reproducers, reproducers]
            self.view.reproducers = reproducers
        self.view.size_changes.append(change)
        self._event("size", change)
        self._save()

    def add_charter(self, title: str, *, area: str = "/", levers: list[str] | None = None, timebox_s: int = 300, pinned: bool = False, by: str = "person") -> Charter:
        charter = Charter(
            id=f"ch-p{len(self.view.charters) + len(self.queue) + 1:03d}",
            title=title,
            area=area,
            levers=list(levers or self.spec.levers),  # type: ignore[arg-type]
            timebox_s=timebox_s,
            priority=1.0,
            origin="person",
            pinned=pinned,
            notes=[f"added by {by}"],
        )
        self.queue.insert(0, charter)
        self._event("charter_added", {"charter": charter.id, "by": by})
        self._save()
        return charter

    def steer(self, charter_id: str, *, action: str, by: str, note: str = "") -> Charter:
        """``veto``, ``pin`` or ``note`` a charter (a running one can only take notes)."""
        for index, charter in enumerate(self.queue):
            if charter.id == charter_id:
                if action == "veto":
                    vetoed = charter.model_copy(update={"status": "vetoed", "notes": [*charter.notes, f"vetoed by {by}: {note}".strip(": ")]})
                    self.queue.pop(index)
                    self.view.charters.append(vetoed)
                    self._save()
                    return vetoed
                if action == "pin":
                    self.queue[index] = charter.model_copy(update={"pinned": True, "notes": [*charter.notes, f"pinned by {by}"]})
                elif action == "note":
                    self.queue[index] = charter.model_copy(update={"notes": [*charter.notes, f"{by}: {note}"]})
                self._save()
                return self.queue[index]
        for child in self.children.values():
            if child.charter is not None and child.charter.id == charter_id and action == "note":
                child.charter = child.charter.model_copy(update={"notes": [*child.charter.notes, f"{by}: {note}"]})
                self._save()
                return child.charter
        raise ValueError(f"no queued charter {charter_id!r} (a running charter can only take a note)")

    def stop(self, *, by: str) -> None:
        self._stopping = True
        self.stop_reason = f"stopped by {by}"

    # ------------------------------------------------------------------ the session

    async def run(self) -> SessionView:
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            problem = await self._prepare()
            if problem:
                return self._end("failed", problem, exit_code=2)
            if self.spec.start_case:
                self.view.status = "starting"
                problem = await self._setup()
                if problem:
                    return self._end("failed", problem, exit_code=2)
            self.view.status = "recon"
            self._save()
            finished = await self._recon()
            if not finished:
                return await self._wrap_up(exit_code=3, reason=self.stop_reason or "the budget ran out before recon finished")
            self._plan()
            if self.spec.dry_run:
                return self._end("finished", "dry run: recon only; the charters it would run are in the report", exit_code=0)
            self.view.status = "exploring"
            self._save()
            await self._explore()
            return await self._wrap_up(exit_code=None, reason=self.stop_reason)
        except asyncio.CancelledError:
            await self._cancel_children()
            self._end("stopped", "the session was cancelled", exit_code=2)
            raise
        except Exception as exc:  # report, never vanish: the session is the user's record
            await self._cancel_children()
            return self._end("failed", f"the session failed ({type(exc).__name__}): {str(exc)[:300]}", exit_code=2)

    async def _prepare(self) -> str | None:
        spec = self.spec
        self.mission = load_mission(spec.mission, spec.prompt, self.project_dir)
        start_case = self._start_case()
        saved: Any = None
        if spec.environment:
            from testzeus_hercules.daemon.cases import load_environment

            saved = load_environment(self.project_dir / ".hercules" / "envs", spec.environment)
            if saved is None:
                return f"unknown environment {spec.environment!r}: save it first (hercules UI → Environments, or .hercules/envs/{spec.environment}.json)"
        base = spec.base_url or (saved.base_url if saved is not None else None) or (start_case.environment.base_url if start_case is not None else None)
        if not base:
            return "no base URL: give --base-url, an environment with one, or a start case with one"
        self.base_url = base.rstrip("/")
        host = urlsplit(self.base_url).hostname or ""
        if production_host(host) and not spec.production_ok:
            return f"{host} does not look like a test environment (no staging/test/qa/dev/local label). Explore only apps you own in test environments; pass --i-own-this-environment to go ahead"
        problems = size_problems(spec, self.limits)
        if problems and not spec.allow_more_workers:
            return "the swarm size you chose exceeds this machine: " + "; ".join(problems) + ". Lower --workers/--reproducers, or pass --allow-more-workers"
        env: dict[str, Any] = {"base_url": self.base_url}
        source = saved or (start_case.environment if start_case is not None else None)
        if source is not None:
            for key in ("browser", "viewport", "device"):
                if getattr(source, key, None):
                    env[key] = getattr(source, key)
            if getattr(source, "browser_options", None) is not None:
                env["browser_options"] = source.browser_options.model_dump(mode="json", exclude_defaults=True)  # type: ignore[union-attr]
        self.case_env = env
        self.test_data = dict(start_case.test_data) if start_case is not None else {}
        self.start_url = urljoin(self.base_url + "/", spec.start_url) if spec.start_url else self.base_url
        self.promises = list(spec.promises) + _project_promises(self.project_dir)
        self.environment = EnvironmentInfo(
            build=await _build_id(self.base_url, spec),
            base_url=self.base_url,
            browser=str(env.get("browser") or "chromium"),
            viewport=str(env.get("viewport") or ""),
            locale=str((env.get("browser_options") or {}).get("locale") or ""),
            timezone=str((env.get("browser_options") or {}).get("timezone_id") or ""),
            account=", ".join(spec.accounts) if spec.accounts else "",
            hercules_version=self.hercules_version,
            llm=self.llm,
            decider=self.decider_name,
            session=self.id,
        )
        self._save()
        return None

    def _start_case(self) -> TestCase | None:
        if not self.spec.start_case:
            return None
        from testzeus_hercules.cli.run import load_case

        return load_case(self._inside_project(self.spec.start_case))

    def _inside_project(self, name: str) -> Path:
        """A case path given in the spec, which must be a file inside the project."""
        root = self.project_dir.resolve()
        path = (self.project_dir / name).resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"{name} is outside the project")
        return path

    async def _setup(self) -> str | None:
        start_case = self._start_case()
        assert start_case is not None
        accounts = self.spec.accounts[: self.view.workers] if self.spec.isolation == "accounts" else [""]
        jobs = []
        for index, account in enumerate(accounts):
            state_file = self.dir / f"start-{index}.json"
            data: dict[str, Any] = dict(start_case.test_data)
            if account:
                data["explore_account"] = SecretRef(secret=account)
            case = start_case.model_copy(update={"test_data": data, "environment": start_case.environment.model_copy(update={"base_url": self.base_url})})
            charter = Charter(id=f"setup-{index}", title=f"Reach the starting state{f' as {account}' if account else ''}", kind="setup", timebox_s=900)
            job = ExploreJob(
                session=self.id, charter=charter, mission="", levers=list(self.spec.levers), scope=self.spec.scope, base_url=self.base_url, start_url=self.start_url, save_state_to=str(state_file)
            )
            run_id = await self.runner.start(case=case, job={"kind": "explore", **json.loads(job.model_dump_json())}, label=f"explore {self.id} setup {index}")
            self.children[run_id] = _Child(run_id, "setup", charter)
            jobs.append((run_id, state_file))
        setup_deadline = min(self.deadline, self.clock() + _SETUP_TIMEOUT_S)
        for run_id, state_file in jobs:
            status = await self._wait(run_id, deadline=setup_deadline)
            if status != "passed" or not state_file.is_file():
                for other, _ in jobs:
                    if self.runner.status(other) not in FINAL:
                        with contextlib.suppress(Exception):
                            await self.runner.cancel(other)
                return f"the start case did not reach its state (run {run_id} {status}); fix it and start again"
        self.start_files = [str(state_file.relative_to(self.project_dir)) for _, state_file in jobs]
        url_file = jobs[0][1].with_suffix(".url")
        if url_file.is_file() and not self.spec.start_url:
            self.start_url = url_file.read_text(encoding="utf-8").strip() or self.start_url
        return None

    async def _recon(self) -> bool:
        timebox = recon_timebox(self.spec.budget.time_s, self.spec.budget.recon_share)
        charter = Charter(id="recon", title="Recon: walk the happy paths, learn the pages, forms and promises", kind="recon", timebox_s=timebox, max_turns=25, priority=1.0)
        problem = ""
        for _ in range(2):  # a real site's start page can time out once; a second failure ends the session, with the reason
            run_id = await self._start_explorer(charter, explorer=0, judge=False)
            status = await self._wait(run_id, deadline=self.deadline)
            self._collect_explorer(self.children[run_id])
            problem = str((self.debriefs[-1] if self.debriefs else {}).get("setup_error") or "")
            if not problem or self._stopping or self.clock() >= self.deadline:
                break
        if problem:
            self.stop_reason = f"recon could not start: {problem}"
            return False
        return status in FINAL and not self._stopping and self.clock() < self.deadline

    def _plan(self) -> None:
        assert self.mission is not None
        charters = candidate_charters(self.states, self.mission, self.spec.levers, changed=self.spec.changed_paths, personas=self.spec.personas)
        known = {charter.id for charter in self.queue} | self.ran
        self.queue.extend(charter for charter in charters if charter.id not in known)
        self.planned_states = len(self.states)
        self._event("planned", {"charters": len(self.queue)})
        self._save()

    def _needs_plan(self) -> bool:
        """The queue ran dry and explorers have seen page states the last plan did not know: plan again from them."""
        return not self.queue and len(self.states) > self.planned_states

    async def _explore(self) -> None:
        while True:
            now = self.clock()
            if self._stopping:
                break
            if now >= self.deadline:
                self.stop_reason = "the session's time is up"
                break
            if self.spec.budget.max_cost_usd is not None and self.view.cost_usd >= self.spec.budget.max_cost_usd:
                self.stop_reason = f"the cost cap of ${self.spec.budget.max_cost_usd:.2f} is reached"
                break
            if self.spec.budget.target_findings is not None and self._target_reached():
                self.stop_reason = f"the target of {self.spec.budget.target_findings} finding(s) at or above {self.spec.severity_floor} is reached"
                break
            if self.stall >= self.spec.budget.stall_charters and not self._running("explore"):
                self.stop_reason = f"coverage stopped growing for {self.stall} charters in a row"
                break
            if self._needs_plan() and self.deadline - self.clock() >= 30:
                self._plan()
            await self._schedule()
            await self._poll()
            if not self.queue and not self._running("explore") and not self._running("reproduce") and not self.clue_queue:
                self.stop_reason = "every charter ran and every clue was reproduced"
                break
            if self.deadline - self.clock() < 30 and not self._running("explore"):
                self.stop_reason = "the session's time is up (too little left to start another charter)"
                break
            await asyncio.sleep(self.poll_s)

    async def _schedule(self, *, explorers: bool = True) -> None:
        running = [child.charter for child in self.children.values() if child.kind == "explore" and child.charter is not None and self.runner.status(child.run_id) not in FINAL]
        while explorers and len(running) < self.view.workers and self.queue:
            remaining = int(self.deadline - self.clock())
            if remaining < 30:
                break  # too little time left to start a charter; the queued ones are listed as next charters
            writing = any(charter.writes for charter in running)
            allowed = self.queue
            if self.spec.isolation == "serial-writes" and writing:
                allowed = [charter for charter in self.queue if not charter.writes]
            elif self.spec.isolation == "single" and running:
                allowed = []
            if not allowed:
                break
            charter = await choose_charter(self.decider, allowed, mission=self.mission.target if self.mission else "", covered=self._covered_by_area(), running=running)
            if charter is None:
                break
            self.queue.remove(charter)
            charter = charter.model_copy(update={"timebox_s": max(30, min(charter.timebox_s, remaining))})
            await self._start_explorer(charter, explorer=len(running) % max(1, len(self.start_files) or 1))
            running.append(charter)
        reproducing = [child for child in self.children.values() if child.kind == "reproduce" and self.runner.status(child.run_id) not in FINAL]
        while len(reproducing) < self.view.reproducers and self.clue_queue:
            clue = self.clue_queue.pop(min(range(len(self.clue_queue)), key=lambda index: _reproduce_order(self.clue_queue[index], index)))
            reproducing.append(await self._start_reproducer(clue))

    async def _poll(self) -> None:
        for child in list(self.children.values()):
            status = self.runner.status(child.run_id)
            if child.kind == "explore":
                self._collect_clues(child)
                if status in FINAL and child.charter is not None and child.charter.status == "running":
                    self._collect_explorer(child)
            elif child.kind == "reproduce" and status in FINAL and child.clue is not None and child.clue.id not in self.outcomes:
                self._collect_reproduction(child, status)
        self._save()

    # ------------------------------------------------------------------ children

    def _session_of(self, explorer: int) -> int:
        return min(explorer, len(self.start_files) - 1) if self.start_files else 0

    def _remember(self, child: _Child, turns: list[Turn]) -> None:
        """Keep a finished charter's moves (from its start page) as part of its session's history."""
        if child.charter is None or not turns:
            return
        session = self._session_of(int(child.charter.explorer or 0))
        start = turns[0].url_before or self.start_url
        goto = Turn(n=1, move=Move(lever="order", variant="goto", text=f'Open "{start}"', direct="goto", value=start), status="passed")
        self.history.setdefault(session, []).append((child.run_id, [goto, *turns]))

    def _history_for(self, clue: Clue) -> list[Turn]:
        """The session's moves from charters that finished before this one, oldest first (the clue's own charter's moves
        are already its turns), at most ``_HISTORY_KEPT`` of the latest."""
        moves = [turn for run_id, turns in self.history.get(self._session_of(self._explorer_of(clue)), []) if run_id != clue.run_id for turn in turns]
        return moves[-_HISTORY_KEPT:]

    def _setup_turns(self, explorer: int) -> list[Turn]:
        """The start case's recorded steps for this explorer's account (empty without a start case)."""
        if not self.start_files:
            return []
        state = self.project_dir / self.start_files[min(explorer, len(self.start_files) - 1)]
        return read_jsonl(state.with_suffix(".setup.jsonl"), Turn)

    def _explorer_of(self, clue: Clue) -> int:
        """The explorer (account) that raised a clue: its reproducer starts in the same state."""
        child = self.children.get(clue.run_id or "")
        return int(child.charter.explorer or 0) if child is not None and child.charter is not None else 0

    def _data_for(self, explorer: int) -> dict[str, Any]:
        data: dict[str, Any] = dict(self.test_data)
        if self.spec.isolation == "accounts" and self.spec.accounts:
            data["explore_account"] = SecretRef(secret=self.spec.accounts[min(explorer, len(self.spec.accounts) - 1)])
        return data

    def _child_case(self, label: str, explorer: int) -> TestCase:
        env = json.loads(json.dumps(self.case_env))
        options = dict(env.get("browser_options") or {})
        # Service workers can make requests the scope guard's routing does not see: explorers run without them.
        options["service_workers"] = "block"
        if self.start_files:
            options["storage_state"] = self.start_files[min(explorer, len(self.start_files) - 1)]
        env["browser_options"] = options
        return TestCase.model_validate(
            {
                "id": f"explore-{label}"[:100],
                "title": f"Explore: {label}"[:200],
                "steps": [{"action": "explore"}],
                "environment": EnvironmentRef.model_validate(env).model_dump(mode="json", exclude_none=True),
                "test_data": {key: (value.model_dump() if hasattr(value, "model_dump") else value) for key, value in self._data_for(explorer).items()},
            }
        )

    async def _start_explorer(self, charter: Charter, *, explorer: int, judge: bool = True) -> str:
        assert self.mission is not None
        self.ran.add(charter.id)
        start = urljoin(self.base_url + "/", charter.area.lstrip("/")) if charter.kind in ("explore", "dig") and charter.area not in ("", "/") else self.start_url
        job = ExploreJob(
            session=self.id,
            charter=charter.model_copy(update={"status": "running", "explorer": explorer}),
            mission=self.mission.target,
            levers=list(self.spec.levers),
            scope=self.spec.scope,
            base_url=self.base_url,
            start_url=start,
            promises=self.promises,
            baseline=sorted(self.baseline),
            tried=self.tried[-2000:],
            judge=judge,
            ask_before_forbidden=self.spec.ask_before_forbidden,
        )
        run_id = await self.runner.start(
            case=self._child_case(f"{self.id}-{charter.id}", explorer), job={"kind": "explore", **json.loads(job.model_dump_json())}, label=f"explore {self.id} {charter.id}"
        )
        kind = "recon" if charter.kind == "recon" else "explore"
        self.children[run_id] = _Child(run_id, kind, job.charter.model_copy(update={"run_id": run_id}))
        self.view.children.append(run_id)
        self._event("charter_started", {"charter": charter.id, "title": charter.title, "run_id": run_id})
        return run_id

    async def _start_reproducer(self, clue: Clue) -> _Child:
        assert self.mission is not None
        job = ReproJob(
            session=self.id,
            clue=clue,
            runs=self.spec.reproduce_runs,
            base_url=self.base_url,
            start_url=clue.start_url or self.start_url,
            scope=self.spec.scope,
            promises=self.promises,
            mission=self.mission.target,
            rubric=load_rubric(self.project_dir),
            environment=self.environment,
            setup=self._setup_turns(self._explorer_of(clue)),
            history=self._history_for(clue),
        )
        run_id = await self.runner.start(
            case=self._child_case(f"{self.id}-repro-{clue.id}", self._explorer_of(clue)), job={"kind": "reproduce", **json.loads(job.model_dump_json())}, label=f"explore {self.id} reproduce {clue.id}"
        )
        child = _Child(run_id, "reproduce", clue=clue)
        self.children[run_id] = child
        self.view.children.append(run_id)
        self.view.status = "exploring" if self.view.status != "reproducing" else self.view.status
        self._event("reproducing", {"clue": clue.id, "title": clue.title, "run_id": run_id})
        return child

    def _collect_clues(self, child: _Child) -> None:
        clues = read_jsonl(self.runner.run_dir(child.run_id) / EXPLORE_DIR / "clues.jsonl", Clue)
        for clue in clues[child.seen_clues :]:
            self._consider(clue, recon=child.kind == "recon")
        child.seen_clues = len(clues)

    def _consider(self, clue: Clue, *, recon: bool) -> None:
        if clue.signature in self.signatures or clue.signature in self.baseline:
            return
        self.signatures.add(clue.signature)
        self.view.clues += 1
        if recon and clue.oracle in BASELINE_ORACLES:
            self.baseline[clue.signature] = clue.title
            return
        if clue.opinion:
            self.view.ux_notes.append(f"{clue.title} ({clue.route})")
            return
        if self.ledger.suppressed(clue.signature):
            self._event("suppressed", {"clue": clue.id, "title": clue.title})
            return
        self.clue_queue.append(clue)
        self._event("clue", {"clue": clue.id, "oracle": clue.oracle, "tier": clue.tier, "title": clue.title})
        if clue.tier != "judged" and clue.confidence >= 0.8 and self.digs.get(clue.route, 0) < _MAX_DIGS_PER_ROUTE and not recon:
            self.digs[clue.route] = self.digs.get(clue.route, 0) + 1
            lever = clue.turns[-1].move.lever if clue.turns else ""
            dig = dig_charter(clue, lever)
            if all(charter.id != dig.id for charter in self.queue):
                self.queue.insert(0, dig)

    def _collect_explorer(self, child: _Child) -> None:
        out = self.runner.run_dir(child.run_id) / EXPLORE_DIR
        self._collect_clues(child)
        before = sum(len(variants) for levers in self.coverage.values() for variants in levers.values())
        with contextlib.suppress(OSError, ValueError):
            for state, levers in (json.loads((out / "coverage.json").read_text(encoding="utf-8")) if child.kind != "recon" else {}).items():
                for lever, variants in levers.items():
                    bucket = self.coverage.setdefault(state, {}).setdefault(lever, [])
                    bucket.extend(variant for variant in variants if variant not in bucket)
        with contextlib.suppress(OSError, ValueError):
            for key, state in json.loads((out / "states.json").read_text(encoding="utf-8")).items():
                self.states.setdefault(key, state)
        finished = read_jsonl(out / "turns.jsonl", Turn)
        for turn in finished:
            self.tried.append([turn.state or "", turn.move.element or "", turn.move.lever, turn.move.variant])
        self._remember(child, finished)
        debrief: dict[str, Any] = {}
        with contextlib.suppress(OSError, ValueError):
            debrief = json.loads((out / "debrief.json").read_text(encoding="utf-8"))
            self.debriefs.append(debrief)
            self.blocked.extend(debrief.get("blocked") or [])
        after = sum(len(variants) for levers in self.coverage.values() for variants in levers.values())
        if child.kind != "recon":
            self.stall = 0 if after > before else self.stall + 1
        self._add_cost(child)
        if child.charter is not None:
            status = "failed" if debrief.get("setup_error") else "done"
            done = child.charter.model_copy(update={"status": status, "debrief": debrief or None})
            child.charter = done
            self.view.charters.append(done)
            self._event("charter_finished", {"charter": done.id, "turns": debrief.get("turns", 0), "clues": len(debrief.get("clues") or [])})

    def _add_cost(self, child: _Child) -> None:
        usd, estimated = run_cost(self.runner.run_dir(child.run_id), per_hosted_call_usd=self.cost_per_hosted_call_usd)
        self.view.cost_usd = round(self.view.cost_usd + usd, 4)
        self.view.cost_estimated = self.view.cost_estimated or estimated

    def _collect_reproduction(self, child: _Child, status: str) -> None:
        assert child.clue is not None
        outcome: dict[str, Any] = {}
        with contextlib.suppress(OSError, ValueError):
            outcome = json.loads((self.runner.run_dir(child.run_id) / EXPLORE_DIR / FINDING_FILE).read_text(encoding="utf-8"))
        self._add_cost(child)
        finding_data = outcome.get("finding")
        if finding_data:
            finding = Finding.model_validate(finding_data)
            recorded = self.ledger.record(finding)
            self.outcomes[child.clue.id] = recorded.outcome
            self.finding_outcomes[recorded.finding.id] = recorded.outcome
            if recorded.outcome != "suppressed":
                self.view.findings.append(recorded.finding)
            self._event("finding", {"finding": recorded.finding.id, "title": recorded.finding.title, "severity": recorded.finding.proposed_severity, "outcome": recorded.outcome})
            return
        self.outcomes[child.clue.id] = "flaky" if outcome.get("flaky") in ("app", "replay") else "not_reproduced"
        record = {
            "clue": child.clue.id,
            "title": child.clue.title,
            "recorded": outcome.get("recorded"),
            "interpreted": outcome.get("interpreted"),
            "flaky": outcome.get("flaky"),
            "notes": outcome.get("notes") or [f"reproducer run {status}"],
        }
        (self.flaky if record["flaky"] in ("app", "replay") else self.not_reproduced).append(record)

    async def _wait(self, run_id: str, *, deadline: float | None = None, ignore_stop: bool = False) -> str:
        while True:
            status = self.runner.status(run_id)
            if status in FINAL:
                return status
            if (self._stopping and not ignore_stop) or (deadline is not None and self.clock() >= deadline):
                await self.runner.cancel(run_id)
                return "cancelled"
            await asyncio.sleep(self.poll_s)

    def _running(self, kind: str) -> list[_Child]:
        return [child for child in self.children.values() if child.kind == kind and self.runner.status(child.run_id) not in FINAL]

    async def _cancel_children(self) -> None:
        for child in self.children.values():
            if self.runner.status(child.run_id) not in FINAL:
                with contextlib.suppress(Exception):
                    await self.runner.cancel(child.run_id)

    # ------------------------------------------------------------------ the end

    async def _wrap_up(self, *, exit_code: int | None, reason: str) -> SessionView:
        self.view.status = "reproducing"
        self._save()
        for child in self._running("explore") + self._running("recon"):
            await self.runner.cancel(child.run_id)
        grace = self.clock() + self.spec.budget.reproduce_grace_s
        while (self.clue_queue or self._running("reproduce")) and self.clock() < grace and not self._stopping:
            await self._schedule(explorers=False)  # no new charters once wrap-up began: only reproductions
            await self._poll()
            await asyncio.sleep(self.poll_s)
        for child in self._running("reproduce"):
            await self.runner.cancel(child.run_id)
        await self._poll()
        for clue in self.clue_queue:
            self.not_reproduced.append({"clue": clue.id, "title": clue.title, "notes": ["not reproduced before the session ended: still a clue"]})
        self.clue_queue.clear()
        for child in self.children.values():
            if child.kind in ("explore", "recon") and child.charter is not None and child.charter.status == "running":
                self._collect_explorer(child)
        if self.spec.teardown_case:
            await self._teardown()
        code = exit_code if exit_code is not None else (1 if self._gate_findings() else 0)
        status = "stopped" if self._stopping else "finished"
        return self._end(status, reason or "finished", exit_code=code)

    async def _teardown(self) -> None:
        from testzeus_hercules.cli.run import load_case

        case = load_case(self._inside_project(self.spec.teardown_case or ""))
        created = self.dir / "created.jsonl"
        data = dict(case.test_data)
        data["explore_created"] = created.read_text(encoding="utf-8")[:4000] if created.is_file() else ""
        run_id = await self.runner.start(case=case.model_copy(update={"test_data": data}), job={}, label=f"explore {self.id} teardown")
        # Teardown cleans up what explorers created: it runs even after a stop, but never longer than its own limit.
        await self._wait(run_id, deadline=self.clock() + _TEARDOWN_TIMEOUT_S, ignore_stop=True)

    def _gate_findings(self) -> list[Finding]:
        """New or regressed findings at or above the severity floor that may gate CI (see ``gate_on``)."""
        gated = []
        for finding in self.view.findings:
            if self.finding_outcomes.get(finding.id) == "seen_again":
                continue
            severity = finding.severity or (finding.proposed_severity if self.spec.gate_on == "proposed-hard" and (finding.tier == "code" or finding.promise_source == "specified") else None)
            if severity is not None and rank(severity) <= rank(self.spec.severity_floor):
                gated.append(finding)
        return gated

    def _target_reached(self) -> bool:
        target = self.spec.budget.target_findings or 0
        return sum(rank(finding.effective_severity) <= rank(self.spec.severity_floor) for finding in self.view.findings) >= target

    def _covered_by_area(self) -> dict[str, int]:
        found: dict[str, int] = {}
        for key, levers in self.coverage.items():
            route = str(self.states.get(key, {}).get("route") or "/")
            found[route] = found.get(route, 0) + sum(len(variants) for variants in levers.values())
        return found

    def _end(self, status: str, message: str, *, exit_code: int) -> SessionView:
        from testzeus_hercules.explore.report import write_report

        self.view.status = status  # type: ignore[assignment]
        self.view.message = message
        self.view.exit_code = exit_code
        self.view.finished_at = now_iso()
        self.view.elapsed_s = round(self.clock() - self.started, 1)
        self.view.coverage = self._coverage_summary()
        self._save()
        with contextlib.suppress(Exception):
            write_report(self)
        self._event("finished", {"status": status, "exit_code": exit_code, "message": message})
        return self.view

    def _coverage_summary(self) -> dict[str, Any]:
        routes: dict[str, dict[str, Any]] = {}
        for key, state in self.states.items():
            entry = routes.setdefault(str(state.get("route") or "/"), {"states": 0, "levers": set(), "elements": set()})
            entry["states"] += 1
            entry["elements"].update(state.get("elements") or [])
            for lever in self.coverage.get(key, {}):
                entry["levers"].add(lever)
        gaps = []
        for route, entry in sorted(routes.items()):
            missing = [lever for lever in self.spec.levers if lever not in entry["levers"]]
            if missing:
                gaps.append({"route": route, "levers_not_used": missing})
        return {
            "routes_seen": sorted(routes),
            "states_seen": len(self.states),
            "moves_tried": sum(len(variants) for levers in self.coverage.values() for variants in levers.values()),
            "gaps": gaps,
            "charters_not_run": [charter.title for charter in self.queue],
        }

    def _save(self) -> None:
        with contextlib.suppress(OSError):
            self.dir.mkdir(parents=True, exist_ok=True)
            temporary = self.dir / "session.json.tmp"
            temporary.write_text(self.snapshot().model_dump_json(indent=1), encoding="utf-8")
            temporary.replace(self.dir / "session.json")

    def snapshot(self) -> SessionView:
        """The session as it is now, with the running charters, for the API."""
        running = [child.charter for child in self.children.values() if child.charter is not None and child.charter.status == "running" and child.kind in ("explore", "recon")]
        return self.view.model_copy(
            update={
                "charters": [*[charter for charter in self.view.charters if charter.status != "queued"], *running, *[charter.model_copy(update={"status": "queued"}) for charter in self.queue]],
                "elapsed_s": self.view.elapsed_s if self.view.finished_at else round(self.clock() - self.started, 1),
            }
        )

    def _event(self, kind: str, data: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            self.emit(kind, {"session": self.id, **data})


def run_cost(run_dir: Path, *, per_hosted_call_usd: float) -> tuple[float, bool]:
    """A child run's model cost and whether it is an estimate.

    Billed dollars (hosted decisions) count as they are. Calls to a hosted endpoint configured as the local model record
    tokens but no dollars, so they are priced at ``explore.cost_per_decision_usd`` each, the same price the pre-flight
    estimate assumes, and the cost is marked as estimated. A model on this machine is free."""
    with contextlib.suppress(OSError, ValueError):
        result = RunResult.model_validate_json((run_dir / "run.json").read_bytes())
        hosted = result.engines.hosted_llm_calls
        return round(float(result.cost.usd or 0.0) + hosted * per_hosted_call_usd, 6), hosted > 0
    return 0.0, False


def _project_promises(project_dir: Path) -> list[Promise]:
    """Specified promises from ``.hercules/explore/promises.yaml`` (a list of promises)."""
    path = project_dir / ".hercules" / "explore" / "promises.yaml"
    if not path.is_file():
        return []
    import yaml  # type: ignore[import-untyped]

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    items = data.get("promises", []) if isinstance(data, dict) else data
    return [Promise.model_validate({**item, "source": item.get("source", "specified")}) for item in items]


async def _build_id(base_url: str, spec: ExploreSpec) -> str:
    """The app's build: ``--build``, else the configured response header, else ETag or Last-Modified, else unknown."""
    if spec.build:
        return spec.build
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(base_url)
    except httpx.HTTPError:
        return "unknown"
    if spec.build_header and response.headers.get(spec.build_header):
        return response.headers[spec.build_header][:200]
    for header in ("etag", "last-modified"):
        if response.headers.get(header):
            return f"unknown ({header} {response.headers[header][:80]})"
    return "unknown"


def _reproduce_order(clue: Clue, index: int) -> tuple[int, float, int]:
    """Which queued clue a free reproducer takes: code checks first, then promises, then the judge's; within a tier, the
    most confident; then the oldest. Reproducer time is scarce, and judged clues are the least often real."""
    return (_TIER_ORDER.get(clue.tier, 3), -clue.confidence, index)
