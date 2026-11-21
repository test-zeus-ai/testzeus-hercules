"""Making a case's plan before the browser starts: reading, coverage audit and repair, lint, the store, review edits.

:func:`make_plan` is what a run (and ``hercules plan``) calls:

1. an approved plan of this case version and model is reused as it is; a plan generated
   for them before comes from the store (no model call);
2. otherwise the local model reads the case (the whole-case reading, part by part and
   cached, see :mod:`testzeus_hercules.engine.intent`) and the reading becomes the plan
   (:func:`~testzeus_hercules.engine.plan.build_plan`);
3. the coverage audit asks the decider whether the plan covers every obligation of every
   step (:mod:`testzeus_hercules.engine.coverage`); gaps are read again in one call and
   audited again; what is still a gap is a lint error;
4. lint adds the model's findings and the facts (:mod:`testzeus_hercules.engine.lint`);
5. the plan is stored under ``.hercules/plans/``.

:func:`apply_edits` checks a reviewer's edited plan against the generated one: same case,
same source steps, valid structure; a plan step whose text the reviewer changed (and nothing
else) is read again by the model, and every change is recorded as an amendment from the user.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from testzeus_hercules.cases.gherkin import VERIFY_ACTION
from testzeus_hercules.cases.models import TestCase
from testzeus_hercules.cases.plan import Amendment, Coverage, CoverageGap, Plan, PlanStep, PlanTiming
from testzeus_hercules.decide.engine import DecisionEngine
from testzeus_hercules.engine.coverage import audit, audited_steps
from testzeus_hercules.engine.intent import CaseItem, CaseReading, Interpreter, case_items, normalise_step_text
from testzeus_hercules.engine.lint import lint_plan
from testzeus_hercules.engine.plan import PlanError, PlanStore, approved, build_plan, case_hash, check_plan, now_iso, plan_key, step_text

logger = logging.getLogger(__name__)

__all__ = ["PlanOptions", "PlanOutcome", "apply_edits", "make_plan", "reading_items"]


@dataclass(frozen=True)
class PlanOptions:
    """How a plan is made and checked: the coverage audit on or off, strict lint, and whether the run asks before risky actions."""

    coverage: bool = True
    strict: bool = False
    confirm_destructive: bool = False
    # Use the store (generated and approved plans); False makes a fresh plan every time.
    reuse: bool = True


@dataclass
class PlanOutcome:
    plan: Plan
    key: str
    # Where the plan came from: made now by the model, from the store, an approved plan, or a plan a caller gave.
    origin: Literal["model", "cache", "approved", "given"]
    reading: CaseReading = field(default_factory=CaseReading)
    records: list[dict[str, Any]] = field(default_factory=list)


def reading_items(case: TestCase, *, service: bool) -> list[CaseItem]:
    """The items a case reading reads: each browser step's action and expected outcome (a check-only step as an outcome);
    API/SQL steps (``service``: the case is tagged ``kind:api``/``kind:sql``) and payload steps are not read before the run."""
    triples: list[tuple[int, str | None, str | None]] = []
    for number, step in enumerate(case.steps, start=1):
        if step.api is not None or step.sql is not None or service or step.quality is not None or step.security is not None:
            continue
        if step.action.strip().lower() == VERIFY_ACTION and step.expected is not None:
            triples.append((number, None, None if step.semantic else step.expected))
        else:
            triples.append((number, step.action, None if step.semantic else step.expected))
    return case_items(triples)


def _merge(base: CaseReading, repaired: CaseReading, steps: Sequence[int]) -> CaseReading:
    """``base`` with the readings of ``steps`` replaced by the repaired ones (where the repair read them)."""
    redone = {step for step, _ in repaired.intents}
    keep = [step for step in steps if step in redone]
    intents = {key: value for key, value in base.intents.items() if key[0] not in keep}
    intents.update({key: value for key, value in repaired.intents.items() if key[0] in keep})
    needs = {step: value for step, value in base.needs.items() if step not in keep}
    needs.update({step: value for step, value in repaired.needs.items() if step in keep})
    extras = {step: value for step, value in base.extras.items() if step not in keep}
    extras.update({step: value for step, value in repaired.extras.items() if step in keep})
    return CaseReading(intents=intents, source="model", needs=needs, extras=extras)


async def make_plan(
    case: TestCase,
    *,
    interpreter: Interpreter,
    decider: DecisionEngine | None,
    model_id: str,
    store: PlanStore | None,
    env: Mapping[str, str],
    names: Sequence[str] = (),
    hidden: Sequence[str] = (),
    service: bool = False,
    options: PlanOptions = PlanOptions(),
    redact: Any = lambda text: text,
) -> PlanOutcome:
    """The plan of ``case`` (see the module docstring); ``case`` is the case as the run uses it (credentials in URLs replaced)."""
    started = time.perf_counter()
    digest = case_hash(case)
    key = plan_key(digest, model_id)
    if store is not None and options.reuse:
        found = store.approved(key)
        if found is not None and _fits(found, case):
            return PlanOutcome(plan=_relinted(found, case, env, hidden, options), key=key, origin="approved")
        found = store.generated(key)
        if found is not None and _fits(found, case):
            timing = found.timing.model_copy(update={"cached": True, "total_ms": _ms(started), "reading_ms": 0, "coverage_ms": 0, "lint_ms": 0, "model_calls": 0, "decider_calls": 0, "cost_usd": None})
            plan = _relinted(found.model_copy(update={"source": "cache", "timing": timing}), case, env, hidden, options)
            return PlanOutcome(plan=plan, key=key, origin="cache")
    items = reading_items(case, service=service)
    calls = 0
    reading_started = time.perf_counter()
    reading = await interpreter.read_case(items, names=names) if items else CaseReading()
    calls += 1 if items and reading.source == "model" else 0
    reading_ms = _ms(reading_started)
    plan = build_plan(case, reading, model=model_id or None, source="cache" if reading.source == "cache" else "model")
    coverage_started = time.perf_counter()
    plan, reading, records, repaired = await _covered(case, plan, reading, items, interpreter, decider, names=names, options=options, redact=redact, model_id=model_id)
    calls += 1 if repaired else 0
    coverage_ms = _ms(coverage_started)
    lint_started = time.perf_counter()
    plan = lint_plan(case, plan, env=env, hidden=hidden, extras=reading.extras, confirm_destructive=options.confirm_destructive)
    timing = PlanTiming(
        reading_ms=reading_ms,
        coverage_ms=coverage_ms,
        lint_ms=_ms(lint_started),
        total_ms=_ms(started),
        model_calls=calls,
        decider_calls=plan.coverage.calls,
        cost_usd=plan.coverage.cost_usd,
    )
    plan = plan.model_copy(update={"timing": timing})
    if store is not None and not any(step.read == "page" and step.kind == "unknown" for step in plan.steps):
        store.save_generated(key, plan)  # a plan with steps the model gave no reading for is made again next time
    return PlanOutcome(plan=plan, key=key, origin="model", reading=reading, records=records)


def _fits(plan: Plan, case: TestCase) -> bool:
    try:
        check_plan(plan, case)
    except PlanError:
        return False
    return True


def _relinted(plan: Plan, case: TestCase, env: Mapping[str, str], hidden: Sequence[str], options: PlanOptions) -> Plan:
    """A stored plan's lint with the facts of now (a secret set since, the run's confirmation setting); the model's findings stay."""
    kept = [finding for finding in plan.lint if finding.source == "model"]
    fresh = lint_plan(case, plan.model_copy(update={"lint": []}), env=env, hidden=hidden, confirm_destructive=options.confirm_destructive)
    findings = [*kept, *fresh.lint]
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda finding: (order[finding.severity], finding.step or 0))
    return plan.model_copy(update={"lint": findings})


async def _covered(
    case: TestCase,
    plan: Plan,
    reading: CaseReading,
    items: Sequence[CaseItem],
    interpreter: Interpreter,
    decider: DecisionEngine | None,
    *,
    names: Sequence[str],
    options: PlanOptions,
    redact: Any,
    model_id: str,
) -> tuple[Plan, CaseReading, list[dict[str, Any]], bool]:
    """The plan after the coverage audit (and one repair of its gaps); the reading it was built from, the audit records and
    whether a repair call was made."""
    steps = audited_steps(plan)
    if not options.coverage or decider is None or not steps:
        note = "coverage audit off" if not options.coverage else "no decider to audit with" if decider is None else "nothing to audit (no step read before the run)"
        return plan.model_copy(update={"coverage": Coverage(checked=False, note=note)}), reading, [], False
    try:
        first = await audit(plan, decider, steps=steps, redact=redact)
    except Exception as exc:  # the audit is a model; when it cannot answer, the plan says so and the run goes on
        logger.warning("the plan's coverage audit failed (%s)", type(exc).__name__)
        return plan.model_copy(update={"coverage": Coverage(checked=False, note=f"the audit could not run ({type(exc).__name__})")}), reading, [], False
    records = list(first.records)
    gaps = first.gaps()
    calls, cost = first.calls, first.cost_usd
    repaired_steps: list[int] = []
    final = dict(first.probabilities)
    repair = getattr(interpreter, "repair_case", None)
    repaired = False
    if gaps and callable(repair):
        reasons = {step: f"the check found the reading may leave out part of this step ({probability:.0%} likely covered)" for step, probability in gaps.items()}
        fixed = await repair(items, reasons, names=names)
        repaired = True
        if fixed.intents:
            reading = _merge(reading, fixed, sorted(gaps))
            plan = build_plan(case, reading, model=model_id or None, source="model")
            repaired_steps = sorted({step for step, _ in fixed.intents if step in gaps})
            try:
                again = await audit(plan, decider, steps=repaired_steps, redact=redact)
            except Exception as exc:
                logger.warning("the coverage audit of the repaired steps failed (%s)", type(exc).__name__)
            else:
                records += again.records
                calls += again.calls
                cost = round((cost or 0.0) + (again.cost_usd or 0.0), 6) if (cost is not None or again.cost_usd is not None) else None
                final.update(again.probabilities)
    gap_list = [CoverageGap(step=step, probability=round(probability, 4), repaired=final.get(step, probability) >= 0.5) for step, probability in sorted(gaps.items())]
    coverage = Coverage(checked=True, engine=first.engine, steps=len(steps), gaps=gap_list, repaired=repaired_steps, calls=calls, cost_usd=cost)
    return plan.model_copy(update={"coverage": coverage}), reading, records, repaired


def _ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


# --------------------------------------------------------------------------- review edits


def _structure(step: PlanStep) -> tuple[Any, ...]:
    return (step.kind, step.target, step.value, step.url, step.role, step.expected)


async def apply_edits(original: Plan, edited: Plan, case: TestCase, *, interpreter: Interpreter | None, by: Literal["user", "agent", "file"] = "user") -> Plan:
    """The edited plan, checked and approved: it must be a plan of ``case`` built from ``original``'s case version. A plan
    step whose text changed while its kind, target and value did not is read again by the model (its structure follows the
    new text); a new plan step with no structure is read the same way. Every change is an amendment from the user."""
    check_plan(edited, case)
    if edited.case_hash != original.case_hash:
        raise PlanError("the edited plan is for another version of the case")
    before = {step.id: step for step in original.steps}
    steps: list[PlanStep] = []
    amendments: list[Amendment] = list(edited.amendments)
    source = "user"
    for step in edited.steps:
        old = before.get(step.id)
        text_changed = old is None or " ".join(step.text.split()) != " ".join(old.text.split())
        if text_changed and (old is None or _structure(step) == _structure(old)) and step.kind not in ("verify", "api", "sql", "quality") and interpreter is not None:
            step = await _reread(step, interpreter)
        if old is None:
            amendments.append(Amendment(step=step.step, plan_step=step.id, kind="edit", before=None, after=step.text, reason="added by the reviewer", source=source, at=now_iso()))  # type: ignore[arg-type]
        elif step != old:
            changes = _changes(old, step)
            amendments.append(Amendment(step=step.step, plan_step=step.id, kind="edit", before=_describe(old), after=_describe(step), reason=f"changed by the reviewer ({changes})", source=source, at=now_iso()))  # type: ignore[arg-type]
        steps.append(step)
    kept = {step.id for step in edited.steps}
    for old in original.steps:
        if old.id not in kept:
            amendments.append(Amendment(step=old.step, plan_step=old.id, kind="edit", before=_describe(old), after=None, reason="removed by the reviewer", source=source, at=now_iso()))  # type: ignore[arg-type]
    if [step.id for step in edited.steps if step.id in before] != [step.id for step in original.steps if step.id in kept] or (edited.order and edited.order != original.order):
        order = edited.order or [step.number for step in edited.source_steps]
        amendments.append(Amendment(step=order[0], kind="reorder", before=",".join(str(n) for n in (original.order or [s.number for s in original.source_steps])), after=",".join(str(n) for n in order), reason="reordered by the reviewer", source=source, at=now_iso()))  # type: ignore[arg-type]
    result = edited.model_copy(update={"steps": steps, "amendments": amendments, "coverage": original.coverage, "timing": original.timing})
    changed = bool(amendments) and amendments != list(edited.amendments)
    return approved(Plan.model_validate(result.model_dump()), by=by, edited=changed)


def _describe(step: PlanStep) -> str:
    criteria = "; ".join(f"{criterion.origin} {criterion.text}" for criterion in step.criteria)
    return step.text + (f" [checks: {criteria}]" if criteria else "")


def _changes(old: PlanStep, new: PlanStep) -> str:
    names = [name for name in ("text", "kind", "target", "value", "url", "role", "expected", "needs", "risk", "criteria") if getattr(old, name) != getattr(new, name)]
    return ", ".join(names) or "fields"


async def _reread(step: PlanStep, interpreter: Interpreter) -> PlanStep:
    """The plan step with its structure read again from its (edited) text; unchanged when the model cannot read it."""
    try:
        intent = await interpreter.interpret(normalise_step_text(step.text))
    except Exception as exc:  # the reviewer's text stays; the step is then read on the page when it runs
        logger.warning("an edited plan step could not be read (%s)", type(exc).__name__)
        return step.model_copy(update={"read": "page"})
    first = intent.parts()[0]
    if first.kind == "unknown" and not first.target:
        return step.model_copy(update={"read": "page"})
    kind = first.kind if first.kind in ("navigate", "click", "fill", "select", "upload", "download", "check", "uncheck", "hover", "press", "wait") else "unknown"
    fields = {"kind": kind, "target": first.target, "value": first.value, "url": first.url, "role": first.role, "expected": first.expected if kind == "wait" else None, "read": "case"}
    updated = step.model_copy(update=fields)
    return updated.model_copy(update={"text": step.text or step_text(updated)})


def approve_plan(plan: Plan, store: PlanStore | None, key: str | None, *, by: Literal["auto", "user", "agent", "file"]) -> Plan:
    """``plan`` approved by ``by`` and saved as the approved plan of its case version and model (when there is a store)."""
    done = plan if plan.review.state in ("approved", "edited") else approved(plan, by=by)
    if store is not None and key:
        store.save_approved(key, done)
    return done
