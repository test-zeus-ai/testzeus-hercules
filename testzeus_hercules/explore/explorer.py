"""One explorer: a run that explores the app for one charter, turn by turn, instead of running fixed steps.

The explorer reuses the run engine's ``_Run``: the browser, the step runner (interpret, perceive, decide, act, verify),
evidence, approvals and the live view. Each turn it perceives the page, asks the lever library for the legal moves,
lets a model choose one (purpose ``explore_next``), does it, and lets the oracles look. Moves the step runner can
express run as real steps (with recorded locators, for a model-free replay); the rest are direct browser moves.

It writes, in ``<run>/explore/``: ``turns.jsonl``, ``clues.jsonl``, ``coverage.json``, ``states.json`` (what it saw:
the app model's input) and ``debrief.json`` (the charter's debrief).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from testzeus_hercules.cases.models import RunResult, Step, TestCase
from testzeus_hercules.decide.contract import ChoiceQuestion, DecisionRequest
from testzeus_hercules.engine.run_graph import RunDeps, _Run
from testzeus_hercules.explore.levers import candidate_moves, elements_of
from testzeus_hercules.explore.models import Charter, Clue, Move, Promise, Scope, Turn, now_iso
from testzeus_hercules.explore.oracles import ClueDraft, Oracles, normalize_route
from testzeus_hercules.explore.scope import ScopeGuard
from testzeus_hercules.kernel.run_context import RunContext

__all__ = ["EXPLORE_DIR", "ExploreJob", "do_direct", "run_exploration"]

EXPLORE_DIR = "explore"
NEXT_PURPOSE = "explore_next"
_TRIED_MARK = " (tried already)"
_DONE = "done"
_MIN_TURNS_BEFORE_DONE = 4
_STATE_JS = "() => [location.pathname, document.title, [...document.querySelectorAll('h1,h2,[role=dialog] h2')].map(e => e.textContent.trim()).join('|'), [...document.querySelectorAll('input,textarea,select,button')].map(e => (e.name || e.id || e.textContent || '').trim()).sort().join(',')].join('\\n')"
_HISTORY = 8


class ExploreJob(BaseModel):
    """What one explorer run is asked to do (written by the coordinator, read by the worker)."""

    model_config = ConfigDict(extra="forbid")

    session: str
    charter: Charter
    mission: str
    levers: list[str]
    scope: Scope = Field(default_factory=Scope)
    base_url: str
    start_url: str
    promises: list[Promise] = Field(default_factory=list)
    baseline: list[str] = Field(default_factory=list)
    tried: list[list[str]] = Field(default_factory=list, description="(state, element, lever, variant) the session already tried")
    judge: bool = True
    judge_bar: float = 0.9
    ask_before_forbidden: bool = False
    replay: list[Turn] = Field(default_factory=list, description="reproducer jobs: the turns to replay instead of exploring")
    save_state_to: str | None = Field(default=None, description="setup jobs: write the storage state the case's steps reach to this file (mode 0600)")


async def run_exploration(case: TestCase, ctx: RunContext, deps: RunDeps, job: ExploreJob) -> RunResult:
    """Explore for ``job.charter`` and return the run's result (its steps are the turns that ran as steps)."""
    if deps.control is not None:
        deps.control.bind(asyncio.get_running_loop(), cancel=ctx.cancel)
    async with contextlib.AsyncExitStack() as stack:
        run = _Explorer(case, ctx, deps, stack, job)
        await run.explore()
    assert run.result is not None
    return run.result


class _Explorer(_Run):
    def __init__(self, case: TestCase, ctx: RunContext, deps: RunDeps, stack: contextlib.AsyncExitStack, job: ExploreJob) -> None:
        super().__init__(case, ctx, deps, stack)
        self.job = job
        self.out = self.evidence.run_dir / EXPLORE_DIR
        self.turns: list[Turn] = []
        self.clues: list[Clue] = []
        self.coverage: dict[str, dict[str, list[str]]] = {}  # state -> lever -> variants
        self.tried: set[tuple[str, str, str]] = set()
        self.states: dict[str, dict[str, Any]] = {}
        self.guard: ScopeGuard | None = None
        self.oracles: Oracles | None = None
        self.timing = {"setup_s": 0.0, "testing_s": 0.0, "investigating_s": 0.0}
        self.state: dict[str, Any] = {
            "next_index": 0,
            "results": (),
            "ledger": (),
            "stopped": False,
            "cancelled": False,
            "setup_error": None,
            "edits": {},
            "notes": (),
            "page": None,
            "quality": (),
            "quality_seen": (),
            "quality_urls": {},
            "security": (),
            "automation": (),
            "unreadable": (),
            "test_data": (),
            "order": (),
            "deferred": (),
            "step_notes": {},
            "plan_results": (),
            "amendments": (),
        }
        self.steps: list[Step] = []
        self.done_reason = ""
        # A setup job runs the start case's own steps (with their expected outcomes); the case its run reports is that.
        self.setup_steps: list[Step] = list(case.steps) if job.charter.kind == "setup" else []

    # ------------------------------------------------------------------ the loop

    async def explore(self) -> None:
        ctx, job = self.ctx, self.job
        began = time.monotonic()
        self.evidence.run_dir.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        ctx.telemetry.emit("run_started", step_count=0)
        ctx.events.emit("run_started", run_id=ctx.run_id, case_id=self.case.id, steps=0, explore=job.charter.id)
        self._build_runner()
        problem = await self._open()
        if problem:
            self.state["setup_error"] = problem
            await self._finish(began)
            return
        self.guard = ScopeGuard(job.scope, job.base_url, on_block=lambda what, why: ctx.events.emit("explore_blocked", what=what, reason=why))
        await self.guard.install(self.page.context)
        self.oracles = Oracles(
            base_url=job.base_url,
            allow_hosts=job.scope.allow_hosts,
            promises=job.promises,
            baseline=set(job.baseline),
            decider=self.decider,
            judge=job.judge,
            judge_bar=job.judge_bar,
            mission=job.mission,
            redact=self.data.redact,
        )
        if self.page_log is not None:
            self.page_log.subscribe(self.oracles.listen)
        self.tried = {(item[1], item[2], item[3]) for item in job.tried if len(item) == 4}
        if job.charter.kind == "setup":
            await self._setup()
            await self._finish(began)
            return
        opened = await self._step_text(f"Open {json.dumps(job.start_url)}")
        self.timing["setup_s"] = time.monotonic() - began
        if opened != "passed":
            self.state["setup_error"] = f"the starting page {job.start_url} could not be opened"
            await self._finish(began)
            return
        if job.replay:
            await self._replay(job.replay)
        else:
            await self._loop(began)
        await self._finish(began)

    async def _setup(self) -> None:
        """Setup jobs: the start case's own steps, then its end state saved as the session's starting state."""
        from testzeus_hercules.engine.page_state import save_storage_state

        recorded: list[Turn] = []
        for step in self.setup_steps:
            status = await self._run(step)
            if status != "passed":
                self.state["setup_error"] = f"the start case did not reach its state: step {len(self.steps)} ({step.action[:120]}) {status}"
                return
            move = Move(lever="order", variant="setup", text=step.action[:4000])
            recorded.append(Turn(n=len(recorded) + 1, move=move, status=status, automation=self._last_automation()))
        if self.job.save_state_to:
            target = Path(self.job.save_state_to)
            if await save_storage_state(self.page, target):
                target.with_suffix(".url").write_text(str(self.page.url), encoding="utf-8")
                # How the start case reached the state, so reproducers can reach it again in a clean browser (a fresh
                # server-side session), not by reusing this one's cookies and whatever explorers left in it.
                target.with_suffix(".setup.jsonl").write_text("".join(turn.model_dump_json() + "\n" for turn in recorded), encoding="utf-8")

    async def _loop(self, began: float) -> None:
        charter, ctx = self.job.charter, self.ctx
        deadline = began + charter.timebox_s
        while len(self.turns) < charter.max_turns:
            if ctx.cancel.is_cancelled:
                self.done_reason = "cancelled"
                return
            if time.monotonic() >= deadline:
                self.done_reason = "timebox"
                return
            snapshot = await self._snapshot()
            state_key = await self._state_key(snapshot)
            tried_here = {(element, lever, variant) for (element, lever, variant) in self.tried}
            levers = charter.levers or self.job.levers
            moves = candidate_moves(snapshot, levers=levers, tried=tried_here, persona=charter.persona) if snapshot is not None else []
            if charter.kind == "recon":
                moves = [move for move in moves if move.variant in ("visit", "press")] or moves[:5]
            if not moves:
                self.done_reason = "no moves left on this page"
                return
            move = await self._choose(moves, snapshot)
            if move is None:
                self.done_reason = "the model judged the charter covered"
                return
            await self._turn(move, state_key)

    async def _turn(self, move: Move, state_key: str) -> None:
        assert self.oracles is not None and self.guard is not None
        number = len(self.turns) + 1
        self.tried.add((move.element or "", move.lever, move.variant))
        self.coverage.setdefault(state_key, {}).setdefault(move.lever, [])
        if move.variant not in self.coverage[state_key][move.lever]:
            self.coverage[state_key][move.lever].append(move.variant)
        forbidden = self.guard.forbidden_move(move)
        url_before = str(self.page.url)
        if forbidden is not None and not await self._allowed_by_person(move, forbidden):
            self.guard.note(move.text, f"forbidden action ({forbidden})")
            self.turns.append(Turn(n=number, move=move, status="skipped", url_before=url_before, url_after=url_before, state=state_key, skipped=f"forbidden: {forbidden}"))
            return
        started = time.monotonic()
        start = await self.oracles.before(self.page)
        automation: dict[str, Any] | None = None
        if move.direct is not None:
            status = await do_direct(self.page, move)
        else:
            status = await self._step_text(move.text)
            automation = self._last_automation()
        self.timing["testing_s"] += time.monotonic() - started
        checking = time.monotonic()
        drafts, text = await self.oracles.after(self.page, move, start, status=status, charter=self.job.charter.title)
        turn = Turn(
            n=number, move=move, status=status, url_before=url_before, url_after=str(self.page.url), state=state_key, duration_ms=int((time.monotonic() - started) * 1000), automation=automation
        )
        self.turns.append(turn)
        drafts += await self._persistence_probe(move, status, text)
        self.timing["investigating_s"] += time.monotonic() - checking
        for draft in drafts:
            self._clue(draft)
        self.ctx.events.emit("explore_turn", n=number, lever=move.lever, variant=move.variant, status=status, clues=len(drafts))

    async def _persistence_probe(self, move: Move, status: str, text: str) -> list[ClueDraft]:
        """After a save that shows the typed value, reload: the value must still be there (a learned persist promise)."""
        assert self.oracles is not None
        value = (move.value or "").strip()
        if move.lever != "inputs" or not move.writes or status != "passed" or len(value) < 3 or value not in text:
            return []
        route = normalize_route(str(self.page.url))
        reload = Move(lever="order", variant="reload_check", text="Reload the page", direct="reload")
        start = await self.oracles.before(self.page)
        reload_status = await do_direct(self.page, reload)
        self.turns.append(Turn(n=len(self.turns) + 1, move=reload, status=reload_status, url_before=start.url, url_after=str(self.page.url)))
        after = await self.oracles.page_text(self.page)
        if value in after:
            return []
        return [
            ClueDraft(
                "persistence",
                "invariant",
                f"persistence|{route}|{(move.clicks or move.element or '').lower()}",
                f"Saved value is gone after a reload on {route}",
                f"after: {move.text}; the page showed the value, a reload lost it",
                route,
                "a saved value is still there after a reload",
                "the value is gone",
                confidence=0.9,
                promise="learned-persist",
            )
        ]

    def _clue(self, draft: ClueDraft) -> None:
        if any(clue.signature == draft.signature for clue in self.clues):
            return
        clue = Clue(
            id=f"c{hashlib.sha256((self.ctx.run_id + draft.signature).encode()).hexdigest()[:10]}",
            oracle=draft.oracle,  # type: ignore[arg-type]
            tier=draft.tier,  # type: ignore[arg-type]
            signature=draft.signature[:300],
            title=draft.title[:300],
            detail=draft.detail[:4000],
            route=draft.route,
            expected=draft.expected[:1000],
            actual=draft.actual[:2000],
            confidence=draft.confidence,
            charter=self.job.charter.id,
            run_id=self.ctx.run_id,
            start_url=self.job.start_url,
            turns=[turn for turn in self.turns if turn.status != "skipped"],
            promise=draft.promise,
            opinion=draft.opinion,
        )
        self.clues.append(clue)
        with (self.out / "clues.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(clue.model_dump_json() + "\n")
        self.ctx.events.emit("explore_clue", clue=clue.id, oracle=clue.oracle, tier=clue.tier, title=clue.title)

    # ------------------------------------------------------------------ choosing

    async def _choose(self, moves: Sequence[Move], snapshot: Any) -> Move | None:
        options: dict[str, Any] = {
            f"m{index}": f"[{move.lever}/{move.variant}] {move.text}" + (_TRIED_MARK if (move.element or "", move.lever, move.variant) in self.tried else "")
            for index, move in enumerate(moves, start=1)
        }
        # "Covered" is offered once the charter has had a fair try: a few moves, or every offered move already tried.
        # (The first real runs ended most charters after 0 to 2 moves and missed bugs one move away.)
        untried = any((move.element or "", move.lever, move.variant) not in self.tried for move in moves)
        if self.job.charter.kind == "recon" or len(self.turns) >= _MIN_TURNS_BEFORE_DONE or not untried:
            options[_DONE] = "the charter's area is covered: nothing worth trying is left here"
        recent = [f"{turn.move.text} -> {turn.status}" for turn in self.turns[-_HISTORY:]]
        observation = snapshot.observation_text(max_elements=60, max_tokens=1200) if hasattr(snapshot, "observation_text") else ""
        state = {
            "task": "You are an exploratory tester. Pick the next move most likely to expose a bug for the mission and charter. Prefer moves not tried yet; do not repeat a move that just ran.",
            "mission": self.job.mission[:800],
            "charter": self.job.charter.title,
            "levers": self.job.charter.levers or self.job.levers,
            "page": {"url": self.data.redact(str(self.page.url)), "observation": self.data.redact(observation)[:5000]},
            "recent_turns": [self.data.redact(item)[:200] for item in recent],
        }
        request = DecisionRequest(state=state, questions={"move": ChoiceQuestion(instructions="Which move next?", criteria=options)})
        try:
            decision = await self.decider.decide(request, purpose=NEXT_PURPOSE)
            picked = decision.choice("move").choice
        except Exception as exc:  # no model answer: take the first offered move (they are ordered untried first)
            self.ctx.events.emit("error", message=f"explore: the move choice failed ({type(exc).__name__}); taking the first offered move")
            picked = "m1"
        if picked == _DONE:
            return None
        index = int(picked[1:]) - 1 if picked.startswith("m") and picked[1:].isdigit() else 0
        return moves[min(max(index, 0), len(moves) - 1)]

    async def _allowed_by_person(self, move: Move, term: str) -> bool:
        """With ``ask_before_forbidden``, a person approves a forbidden move (a timeout or no person is a no)."""
        control = self.deps.control
        if not self.job.ask_before_forbidden or control is None:
            return False
        try:
            answer = await control.ask(
                "destructive", step=len(self.steps) + 1, reason=f"the move clicks “{move.clicks}”, a forbidden action ({term})", action=move.text, target=move.clicks, allow_edit=False
            )
        except Exception:
            return False
        return bool(answer.decision == "approve")

    # ------------------------------------------------------------------ steps and the page

    async def _step_text(self, text: str) -> str:
        """Run ``text`` as the next real step through the run engine; returns its status."""
        return await self._run(Step(action=text))

    async def _run(self, step: Step) -> str:
        self.steps.append(step)
        self.case = self.case.model_copy(update={"steps": list(self.steps)})
        assert self.runner is not None
        self.runner.deps.steps = tuple(step.action for step in self.steps)
        update = await self.step(self.state)  # type: ignore[arg-type]
        self.state.update(update)
        self.state["stopped"] = False  # a failed move is an observation, not the end of exploring
        results = self.state.get("results") or ()
        return str(results[-1].status) if results else "error"

    def _last_automation(self) -> dict[str, Any] | None:
        automation = self.state.get("automation") or ()
        if not automation:
            return None
        last = automation[-1]
        return last.model_dump(mode="json") if last.index == len(self.steps) else None

    async def _snapshot(self) -> Any:
        try:
            return await self.deps.perceiver.snapshot(self.page, 10_000 + len(self.turns))
        except Exception:
            return None

    async def _state_key(self, snapshot: Any) -> str:
        try:
            raw = str(await self.page.evaluate(_STATE_JS))
        except Exception:
            raw = str(getattr(self.page, "url", ""))
        key = hashlib.sha256(raw.encode()).hexdigest()[:12]
        if key not in self.states:
            fields = [element.name for element in elements_of(snapshot)] if snapshot is not None else []
            self.states[key] = {"route": normalize_route(str(self.page.url)), "title": raw.split("\n")[1] if "\n" in raw else "", "elements": fields[:60]}
        return key

    async def _replay(self, turns: Sequence[Turn]) -> None:
        """Reproducer jobs: do the turns again, in order, as recorded (interpreted: through the step runner)."""
        assert self.oracles is not None
        for turn in turns:
            if self.ctx.cancel.is_cancelled:
                return
            start = await self.oracles.before(self.page)
            status = await do_direct(self.page, turn.move) if turn.move.direct is not None else await self._step_text(turn.move.text)
            drafts, text = await self.oracles.after(self.page, turn.move, start, status=status, charter=self.job.charter.title)
            self.turns.append(Turn(n=len(self.turns) + 1, move=turn.move, status=status, url_before=start.url, url_after=str(self.page.url)))
            drafts += await self._persistence_probe(turn.move, status, text)
            for draft in drafts:
                self._clue(draft)

    # ------------------------------------------------------------------ the end

    async def _finish(self, began: float) -> None:
        if not self.steps:
            self.steps.append(Step(action=f"Explore: {self.job.charter.title}"))
            self.case = self.case.model_copy(update={"steps": list(self.steps)})
        await self.finalize(self.state)  # type: ignore[arg-type]
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "turns.jsonl").write_text("".join(turn.model_dump_json() + "\n" for turn in self.turns), encoding="utf-8")
        (self.out / "coverage.json").write_text(json.dumps(self.coverage, indent=1), encoding="utf-8")
        (self.out / "states.json").write_text(json.dumps(self.states, indent=1), encoding="utf-8")
        blocked = [{"what": item.what, "reason": item.reason} for item in (self.guard.blocked if self.guard else [])]
        (self.out / "debrief.json").write_text(json.dumps(self._debrief(began, blocked), indent=1), encoding="utf-8")

    def _debrief(self, began: float, blocked: list[dict[str, str]]) -> dict[str, Any]:
        """The charter's debrief, in SBTM shape."""
        by_lever: dict[str, int] = {}
        for turn in self.turns:
            by_lever[turn.move.lever] = by_lever.get(turn.move.lever, 0) + 1
        total = max(time.monotonic() - began, 0.001)
        elements = {name for state in self.states.values() for name in state["elements"]}
        touched = {(turn.move.element or "").split(" ", 1)[-1] for turn in self.turns if turn.move.element}
        share = len(touched & elements) / len(elements) if elements else 0.0
        return {
            "charter": self.job.charter.title,
            "charter_id": self.job.charter.id,
            "run_id": self.ctx.run_id,
            "ended": self.done_reason or ("setup failed" if self.state.get("setup_error") else "done"),
            "setup_error": self.state.get("setup_error"),
            "turns": len(self.turns),
            "by_lever": by_lever,
            "states": len(self.states),
            "clues": [clue.title for clue in self.clues],
            "blocked": blocked[:50],
            "time_split": {name: round(value / total, 2) for name, value in self.timing.items()},
            "elements_touched": f"{len(touched & elements)} of {len(elements)}",
            "confidence": "high" if share >= 0.7 else "medium" if share >= 0.35 else "low",
            "confidence_reason": f"touched {share:.0%} of the elements seen in this charter's states",
            "not_reached": sorted(elements - touched)[:20],
            "at": now_iso(),
        }


async def do_direct(page: Any, move: Move) -> str:
    """Do a direct move with Playwright; returns ``passed`` or ``error``."""
    try:
        if move.direct == "back":
            await page.go_back(wait_until="load", timeout=15_000)
        elif move.direct == "reload":
            await page.reload(wait_until="load", timeout=15_000)
        elif move.direct == "clear_cookies":
            await page.context.clear_cookies()
            await page.reload(wait_until="load", timeout=15_000)
        elif move.direct == "wait":
            await asyncio.sleep(min(float(move.value or 5), 30))
        elif move.direct == "viewport":
            width, _, height = (move.value or "360x740").partition("x")
            await page.set_viewport_size({"width": int(width), "height": int(height or 740)})
        elif move.direct == "resubmit":
            locator = page.get_by_role("button", name=move.clicks or "", exact=True).first
            await locator.evaluate("(el) => { el.click(); el.click(); }", timeout=10_000)
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("load", timeout=10_000)
            await asyncio.sleep(0.5)
        elif move.direct == "offline_once":
            await page.context.set_offline(True)
            try:
                with contextlib.suppress(Exception):
                    await page.get_by_role("button", name=move.clicks or "", exact=True).first.click(timeout=5_000, no_wait_after=True)
                await asyncio.sleep(1.0)
            finally:
                await page.context.set_offline(False)
        elif move.direct == "goto":  # stitches a session's earlier moves together for a reproduction (never offered as a lever)
            await page.goto(move.value or "about:blank", wait_until="load", timeout=20_000)
        elif move.direct == "second_tab":
            other = await page.context.new_page()
            await other.goto(page.url, wait_until="load", timeout=15_000)
            await other.close()
        else:
            return "error"
    except Exception:
        return "error"
    return "passed"


def read_jsonl(path: Path, model: type[BaseModel]) -> list[Any]:
    """Records of ``model`` from a JSON-lines file (a missing file is empty; a bad line is skipped)."""
    if not path.is_file():
        return []
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            with contextlib.suppress(ValueError):
                found.append(model.model_validate_json(line))
    return found
