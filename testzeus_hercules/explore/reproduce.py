"""The reproducer: turn a clue into a finding, or leave it a clue.

For one clue, in its own worker run:

1. **Recorded replay** (no model): the clue's turns, ``runs`` times, each in a fresh browser from the start URL.
   At least ``runs - 1`` hits confirm it; 1 hit is *flaky (app)*; none is *not reproduced*.
2. **Minimize** with delta debugging over the recorded replay: the fewest turns that still raise the signature.
3. **Interpreted replay**: this run itself replays the minimized turns through the step runner (a model reads them),
   proving the steps hold up as a Hercules case. Recorded holds but interpreted does not: *flaky (replay)*, a Hercules
   problem, never a product bug.
4. **Classify and propose a severity**: models answer, code sets the floor and the caps (a judged-only clue never above
   P2; a learned promise never above P2 alone).
5. **Emit** the failing case (``status: known_bug``, proposed in ``.hercules/explore/cases/`` until a person keeps it)
   and ``explore/finding.json`` in the run directory.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from testzeus_hercules.cases.models import RunResult, TestCase
from testzeus_hercules.decide.contract import ChoiceQuestion, DecisionRequest, Question
from testzeus_hercules.engine.data import CaseData
from testzeus_hercules.engine.run_graph import RunDeps, parse_viewport
from testzeus_hercules.explore.explorer import EXPLORE_DIR, ExploreJob, read_jsonl, run_exploration
from testzeus_hercules.explore.missions import RUBRIC, floor_for, rank, worse
from testzeus_hercules.explore.models import Charter, Clue, EnvironmentInfo, Finding, Move, Promise, Scope, Turn, now_iso
from testzeus_hercules.explore.replay import Replayer, confirm, minimize, replayable
from testzeus_hercules.kernel.run_context import RunContext

__all__ = ["ReproJob", "finding_id", "run_reproduction"]

SEVERITY_PURPOSE = "explore_severity"
CLASSIFY_PURPOSE = "explore_classify"
FINDING_FILE = "finding.json"
_CATEGORIES = {
    "product_bug": "The app behaves wrongly: the move was a fair use of the app and the outcome is incorrect.",
    "environment": "The app or a service it needs was down or unreachable for everything at that moment, not because of this move.",
    "test_problem": "The move itself was unfair or impossible (it did something no user can do), so the outcome says nothing about the app.",
}


class ReproJob(BaseModel):
    """What one reproducer run is asked to do."""

    model_config = ConfigDict(extra="forbid")

    session: str
    clue: Clue
    runs: int = 3
    base_url: str
    start_url: str
    scope: Scope = Field(default_factory=Scope)
    promises: list[Promise] = Field(default_factory=list)
    mission: str = ""
    rubric: dict[str, str] = Field(default_factory=lambda: dict(RUBRIC))
    environment: EnvironmentInfo = Field(default_factory=EnvironmentInfo)
    setup: list[Turn] = Field(default_factory=list, description="the start case's recorded steps: each replay signs in afresh instead of reusing a session")
    history: list[Turn] = Field(default_factory=list, description="the session's earlier moves (other charters, same signed-in session): tried in front of the clue's own moves when those alone miss")
    # Proposed cases wait outside the project's cases until a person keeps them (see explore/cases.py).
    cases_dir: str = ".hercules/explore/cases"
    minimize_runs: int = 15


def finding_id(signature: str) -> str:
    return "f" + hashlib.sha256(signature.encode()).hexdigest()[:10]


async def run_reproduction(case: TestCase, ctx: RunContext, deps: RunDeps, job: ReproJob) -> RunResult:
    """Reproduce ``job.clue``; the run's result is the interpreted replay (its evidence is the finding's)."""
    clue = job.clue
    notes: list[str] = []
    turns: list[Turn] = list(clue.turns)
    recorded = "not run"
    flaky = "none"
    fresh = bool(job.setup) and replayable(job.setup)
    if fresh:
        case = _without_saved_state(case)
        notes.append("each replay starts in a clean browser and does the start case's steps again")
    data = CaseData.from_case(case, env=deps.env, run_id=ctx.run_id)
    replayer = Replayer(
        open_page=lambda: _open_page(case, ctx, deps),
        data=lambda text: data.resolve(text, captured=False),
        setup=list(job.setup) if fresh else [],
        base_url=job.base_url,
        start_url=clue.start_url or job.start_url,
        scope=job.scope,
        promises=job.promises,
        decider=deps.decider if clue.tier == "judged" else None,
        judge_last=clue.tier == "judged",
        mission=job.mission,
    )
    if replayable(turns):
        hits = await confirm(replayer, turns, clue.signature, job.runs)
        history = [turn for turn in job.history if not turn.skipped and replayable([turn])]
        if hits == 0 and history:
            # The bug may need state the session built earlier (an item another charter put in the cart): try again with
            # those moves first; minimizing below keeps only the ones it needs.
            with_history = [*history, _goto(clue.start_url or job.start_url), *turns]
            hits = await confirm(replayer, with_history, clue.signature, job.runs)
            if hits:
                notes.append(f"it needs state built earlier in the session: replayed with {len(history)} earlier move(s) first")
                turns = with_history
        recorded = f"{hits}/{job.runs}"
        if hits == 0:
            notes.append("the recorded replay did not raise it again: not reproduced")
        elif hits < max(1, job.runs - 1):
            flaky = "app"
            notes.append(f"the recorded replay raised it {hits} of {job.runs} times: flaky (app)")
        else:
            before = len(turns)
            turns, used = await minimize(replayer, turns, clue.signature, max_runs=job.minimize_runs)
            notes.append(f"minimized from {before} to {len(turns)} turn(s) with {used} replay(s)")
    else:
        notes.append("some turns have no recorded actions: confirmed by the interpreted replay only")
    turns = [_without_counts(turn) for turn in turns]  # the interpreted replay and the finding's steps read these texts
    missed = recorded.startswith("0/")
    # A clue the recorded replay never raised is not reproduced; the slower model-driven replay is skipped (the run only
    # opens the start page, for its evidence).
    replay = [_goto(clue.start_url or job.start_url)] if missed else ([*job.setup, _open(clue.start_url or job.start_url), *turns] if fresh else turns)
    explore = ExploreJob(
        session=job.session,
        charter=Charter(id=f"repro-{clue.id}"[:64], title=f"Reproduce: {clue.title}"[:300], kind="dig", timebox_s=600, max_turns=max(1, len(turns))),
        mission=job.mission,
        levers=["inputs", "order", "conditions", "time", "people"],
        scope=job.scope,
        base_url=job.base_url,
        start_url=job.base_url if fresh else clue.start_url or job.start_url,
        promises=job.promises,
        judge=clue.tier == "judged",
        replay=replay,
    )
    result = await run_exploration(case, ctx, deps, explore)
    out = ctx.paths.run_dir(ctx.run_id) / EXPLORE_DIR
    seen = {item.signature for item in read_jsonl(out / "clues.jsonl", Clue)}
    interpreted_hit = clue.signature in seen
    interpreted = "not run" if missed else "1/1" if interpreted_hit else "0/1"
    confirmed = (recorded != "not run" and flaky == "none" and not recorded.startswith("0/")) or (recorded == "not run" and interpreted_hit)
    if confirmed and not interpreted_hit:
        flaky = "replay"
        notes.append("the recorded replay holds but the interpreted replay did not: flaky (replay), a Hercules problem, not a product bug")
    outcome: dict[str, Any] = {"clue": clue.id, "signature": clue.signature, "recorded": recorded, "interpreted": interpreted, "flaky": flaky, "notes": notes, "at": now_iso()}
    if confirmed and flaky == "none":
        category = await _classify(deps, clue, recorded)
        outcome["category"] = category
        if category == "product_bug":
            finding = await _finding(deps, ctx, job, turns, recorded, interpreted, result, test_data=dict(case.test_data))
            outcome["finding"] = json.loads(finding.model_dump_json())
        else:
            notes.append(f"classified {category}: kept as a clue, not a finding")
    out.mkdir(parents=True, exist_ok=True)
    (out / FINDING_FILE).write_text(json.dumps(outcome, indent=1), encoding="utf-8")
    return result


def _without_saved_state(case: TestCase) -> TestCase:
    """The case without the explorer's saved session: the reproducer reaches the start state itself."""
    options = case.environment.browser_options
    if options is None or getattr(options, "storage_state", None) is None:
        return case
    return case.model_copy(update={"environment": case.environment.model_copy(update={"browser_options": options.model_copy(update={"storage_state": None})})})


_COUNTED = re.compile(r'"([^"]*?\S)\s*\((?:-?\d+(?:\.\d+)?)\)"(?= (?:link|button|tab|menu item)\b)')


def _without_counts(turn: Turn) -> Turn:
    """'Click the "Cart (3)" link' as 'Click the "Cart" link': a count in a control's name is state, not its name, and
    a fresh session shows another one."""
    text = _COUNTED.sub(r'"\1"', turn.move.text)
    return turn if text == turn.move.text else turn.model_copy(update={"move": turn.move.model_copy(update={"text": text})})


def _goto(url: str) -> Turn:
    return Turn(n=1, move=Move(lever="order", variant="goto", text=f'Open "{url}"', direct="goto", value=url), status="passed")


def _open(url: str) -> Turn:
    return Turn(n=1, move=Move(lever="order", variant="setup", text=f'Open "{url}"'), status="passed")


@contextlib.asynccontextmanager
async def _open_page(case: TestCase, ctx: RunContext, deps: RunDeps) -> Any:
    environment = case.environment
    extra: dict[str, Any] = {"device": environment.device} if environment.device else {}
    if environment.browser not in (None, "chromium") or environment.browser_options is not None:
        extra.update(browser=environment.browser, options=environment.browser_options, project_dir=ctx.paths.project_dir)
    async with deps.resolve_launcher().open(viewport=parse_viewport(environment.viewport, environment.device), headless=True, **extra) as page:
        yield page


async def _classify(deps: RunDeps, clue: Clue, recorded: str) -> str:
    state = {
        "task": "An exploratory test found an anomaly and reproduced it from a fresh start. Classify it.",
        "anomaly": {"title": clue.title, "oracle": clue.oracle, "tier": clue.tier, "expected": clue.expected, "actual": clue.actual, "detail": clue.detail[:1500]},
        "moves": [turn.move.text[:200] for turn in clue.turns[-8:]],
        "reproduced": recorded,
        "facts": "The app answered other requests of the session normally.",
    }
    request = DecisionRequest(state=state, questions={"category": ChoiceQuestion(instructions="Which explains this anomaly?", criteria=dict(_CATEGORIES))})
    try:
        decision = await deps.decider.decide(request, purpose=CLASSIFY_PURPOSE)
        choice = decision.choice("category").choice
    except Exception:
        choice = "product_bug" if clue.tier == "code" else "test_problem"
    return choice if choice in _CATEGORIES else "product_bug"


async def _severity(deps: RunDeps, job: ReproJob, clue: Clue, steps: Sequence[str]) -> tuple[str, str, str]:
    """(proposed severity, proposed priority, reason): the model's pick, never better than the code floor, and capped."""
    promise = next((item for item in job.promises if item.id == clue.promise), None)
    state = {
        "task": "Propose a severity (how bad) and a priority (how soon) for a reproduced bug, by the rubric.",
        "mission": job.mission[:500],
        "bug": {"title": clue.title, "oracle": clue.oracle, "tier": clue.tier, "route": clue.route, "expected": clue.expected, "actual": clue.actual},
        "steps": list(steps)[:12],
        "promise": promise.model_dump() if promise else None,
    }
    questions: dict[str, Question] = {
        "severity": ChoiceQuestion(instructions="How bad is it, by the rubric?", criteria=dict(job.rubric)),
        "priority": ChoiceQuestion(instructions="How soon should it be fixed (P0 now, P3 when convenient)?", criteria=dict(job.rubric)),
    }
    floor = floor_for(clue.oracle)
    try:
        decision = await deps.decider.decide(DecisionRequest(state=state, questions=questions), purpose=SEVERITY_PURPOSE)
        proposed, priority = decision.choice("severity").choice, decision.choice("priority").choice
    except Exception:
        proposed, priority = floor, floor
    reasons = [f"model proposed {proposed}"]
    severity = worse(proposed, floor) if proposed in RUBRIC else floor
    if severity != proposed:
        reasons.append(f"raised to the {clue.oracle} floor {floor}")
    capped = None
    if clue.tier == "judged":
        capped = "a judged-only finding"
    elif clue.tier == "invariant" and (promise is None or promise.source == "learned"):
        capped = "a learned promise alone"
    if capped and rank(severity) < rank("P2"):
        severity = "P2"
        reasons.append(f"capped at P2: {capped} never backs P0 or P1 without a person's review")
    priority = priority if priority in RUBRIC else severity
    return severity, priority, "; ".join(reasons)


async def _finding(deps: RunDeps, ctx: RunContext, job: ReproJob, turns: Sequence[Turn], recorded: str, interpreted: str, result: RunResult, *, test_data: dict[str, Any] | None = None) -> Finding:
    clue = job.clue
    steps = [turn.move.text for turn in turns]
    severity, priority, reason = await _severity(deps, job, clue, steps)
    identifier = finding_id(clue.signature)
    promise = next((item for item in job.promises if item.id == clue.promise), None)
    case_path = _emit_case(ctx, job, identifier, turns, severity, test_data=test_data)
    run_dir = ctx.paths.run_dir(ctx.run_id)
    evidence = [str(path.relative_to(ctx.paths.project_dir)) for path in sorted(run_dir.glob("steps/*/plain.jpg"))[-3:] if path.is_relative_to(ctx.paths.project_dir)]
    environment = job.environment.model_copy(update={"charter": clue.charter or "", "at": now_iso()})
    return Finding(
        id=identifier,
        title=clue.title,
        signature=clue.signature,
        oracle=clue.oracle,
        tier=clue.tier,
        promise=clue.promise,
        promise_source=promise.source if promise else ("learned" if clue.promise else None),
        requirement=promise.requirement if promise else None,
        proposed_severity=severity,  # type: ignore[arg-type]
        proposed_priority=priority,  # type: ignore[arg-type]
        severity_reason=reason,
        confidence=clue.confidence,
        steps=steps,
        expected=clue.expected,
        actual=clue.actual,
        minimized_from=len(clue.turns),
        reproduced_recorded=recorded,
        reproduced_interpreted=interpreted,
        case_path=case_path,
        run_ids=[run_id for run_id in (clue.run_id, ctx.run_id) if run_id],
        evidence=evidence + ([f"{run_dir.name}/{result.evidence.video}"] if result.evidence.video else []),
        environment=environment,
        sessions=[job.session],
        builds=[job.environment.build],
    )


def _emit_case(ctx: RunContext, job: ReproJob, identifier: str, turns: Sequence[Turn], severity: str, *, test_data: dict[str, Any] | None = None) -> str | None:
    """Write the failing case, quarantined (``known_bug``); returns its path relative to the project.

    A session that began from a start case (signing in, say) reached the bug only after those steps, so the case does
    them first: without them a rerun starts signed out and can never pass, even once the bug is fixed."""
    clue = job.clue
    setup: list[dict[str, Any]] = [{"action": turn.move.text} for turn in job.setup if turn.move.text.strip()]
    steps: list[dict[str, Any]] = [*setup, {"action": f"Open {json.dumps(clue.start_url or job.start_url)}"}]
    conditions: list[str] = []
    previous_url = clue.start_url or job.start_url
    for turn in turns:
        move = turn.move
        if move.direct in ("reload", "back"):
            steps.append({"action": f"Open {json.dumps(previous_url if move.direct == 'reload' else turn.url_after or previous_url)}"})
        elif move.direct is not None:
            steps.append({"action": move.text})
            if move.direct in ("offline_once", "clear_cookies", "viewport", "wait"):
                conditions.append(move.text)
        else:
            steps.append({"action": move.text})
        previous_url = turn.url_after or previous_url
    steps[-1]["expected"] = _expected(clue, turns)
    steps[-1]["semantic"] = clue.oracle not in ("persistence",)
    description = f"Found by exploration session {job.session}: {clue.title}. Expected: {clue.expected}. Actual: {clue.actual}."
    if conditions:
        description += " Needs conditions a case cannot set by itself: " + "; ".join(conditions)
    case = {
        "id": identifier,
        "title": clue.title[:200],
        "description": description[:2000],
        "priority": {"P0": "critical", "P1": "high", "P2": "medium", "P3": "low"}[severity],
        "tags": ["explore", f"finding:{identifier}", f"severity:{severity}"],
        "environment": {"base_url": job.base_url},
        "steps": steps[:200],
        "status": "known_bug",
    }
    if setup and test_data:
        case["test_data"] = test_data
    try:
        TestCase.model_validate(case)
    except ValueError:
        return None
    folder = ctx.paths.project_dir / job.cases_dir
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{identifier}.json"
    path.write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8")
    return str(path.relative_to(ctx.paths.project_dir))


def _expected(clue: Clue, turns: Sequence[Turn]) -> str:
    if clue.oracle == "persistence":
        value = next((turn.move.value for turn in reversed(turns) if turn.move.lever == "inputs" and turn.move.value), "")
        return f'I should see "{(value or "").strip()}"'
    if clue.oracle == "server_error":
        return "The page shows a normal page or a validation message, not a server error"
    if clue.oracle == "dead_action":
        return f"Something happens: {clue.expected}"
    return clue.expected or "The app behaves correctly"


def load_job(path: Path) -> ExploreJob | ReproJob:
    """The job a worker was given: an explorer's or a reproducer's."""
    data = json.loads(path.read_text(encoding="utf-8"))
    kind = data.pop("kind", "explore")
    return ReproJob.model_validate(data) if kind == "reproduce" else ExploreJob.model_validate(data)
