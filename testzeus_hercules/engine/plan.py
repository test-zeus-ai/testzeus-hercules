"""Plans in code: a case reading as a :class:`~testzeus_hercules.cases.plan.Plan`, a plan back as the reading a run
executes, the plan store under ``.hercules/plans/`` and the diff of two plans.

Nothing here decides what a step means: the model's case reading (see
:mod:`testzeus_hercules.engine.intent`) does. This module converts it, checks a plan's
structure against its case and keeps plans on disk:

* :func:`build_plan` turns the reading into plan steps: one per action the model read for a
  step (``item: action``), then one ``verify`` plan step holding the step's expected outcome
  as author criteria (``item: expected``). The model's criteria for each action are attached
  to the action they follow (``after``); an ``author`` criterion is kept as author only when
  its text is words of the step, else it is ``inferred``. A step the model could not read is
  a plan step read on the page (``read: page``).
* :func:`plan_reading` turns a plan (generated, approved or edited) into the
  :class:`~testzeus_hercules.engine.intent.CaseReading` the step graph runs, with each action
  carrying its plan step id, criteria and risk flags.
* :class:`PlanStore` keeps generated and approved plans keyed by the SHA-256 of the case,
  the reading prompt's version and the model id, so an approved plan is reused until the
  case or the model changes.
* :func:`diff_plans` compares two plans step by step (and :func:`as_run` gives a plan with
  its amendments applied, for a plan-versus-run diff).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from pydantic import ValidationError

from testzeus_hercules.cases.gherkin import VERIFY_ACTION
from testzeus_hercules.cases.models import Step, TestCase
from testzeus_hercules.cases.plan import ACTION_KINDS, Criterion, Plan, PlanReview, PlanStep, Risk, SourceStep
from testzeus_hercules.engine.intent import PROMPT_VERSION, CaseReading, ItemRole, StepIntent, normalise_step_text

logger = logging.getLogger(__name__)

__all__ = [
    "PlanStore",
    "PlanError",
    "as_run",
    "build_plan",
    "case_hash",
    "criterion_text",
    "diff_plans",
    "now_iso",
    "plan_key",
    "plan_reading",
    "render_plan",
    "source_steps",
    "step_text",
]

_ASSERT_TO_CHECK = {"assert_text": "text_visible", "assert_not_text": "text_absent", "assert_url": "url_matches"}
_CHECK_TO_ASSERT = {value: key for key, value in _ASSERT_TO_CHECK.items()}
_SERVICE = frozenset({"api", "sql", "quality"})


class PlanError(ValueError):
    """A plan does not fit its case (another case, missing or unknown steps, a broken structure)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def case_hash(case: TestCase) -> str:
    """The SHA-256 of the case's content (every field that changes what runs)."""
    text = json.dumps(case.model_dump(mode="json", exclude_none=True), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan_key(digest: str, model_id: str) -> str:
    """The key of a case's plans: the case's hash, the reading prompt's version and the model that read it."""
    return hashlib.sha256(json.dumps([digest, PROMPT_VERSION, model_id or ""]).encode("utf-8")).hexdigest()


def _flat(text: str | None) -> str:
    return " ".join((text or "").replace("“", '"').replace("”", '"').split()).casefold()


def _clip(text: str, limit: int = 2000) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------- texts


def step_text(step: PlanStep | StepIntent) -> str:
    """A plan step (or action) in words: what it does, with its target and value as read (placeholders kept)."""
    kind = step.kind
    target = f' "{step.target}"' if step.target else ""
    if kind == "navigate":
        return f"open {step.url or ''}".strip()
    if kind in ("fill", "select"):
        return f"{kind}{target} with \"{step.value or ''}\""
    if kind == "press":
        return f"press {step.value or ''}{' on' + target if target else ''}".strip()
    if kind == "wait":
        return f'wait for "{step.expected}"' if step.expected else f"wait {step.value or ''} s".strip()
    if kind in ("api", "sql", "quality", "security"):
        return f"{kind} step"
    if kind == "verify":
        return "check the step's outcome"
    role = f" {step.role}" if step.role and step.role != "textbox" else ""
    return f"{kind}{target}{role}".strip() if kind != "unknown" else f"do{target}".strip()


def criterion_text(
    check: str,
    *,
    expected: str | None = None,
    target: str | None = None,
    value: str | None = None,
    state: str | None = None,
    count: int | None = None,
    op: str = "eq",
    match: str = "contains",
    visual: bool = False,
) -> str:
    """A criterion in words, from its check form."""
    if check == "text_visible":
        return f'the page shows "{expected}"'
    if check == "text_absent":
        return f'the page does not show "{expected}"'
    if check == "url_matches":
        return f'the address {"is" if match == "equals" else "contains"} "{expected}"'
    if check == "field_value":
        return f'the "{target}" field holds "{value}"'
    if check == "element_state":
        if state in ("open", "closed"):
            return f'the "{target}" dialog is {state}' if target else f"a dialog is {state}"
        return f'"{target}" is {state}' if target else f"the element is {state}"
    if check == "count":
        how = {"eq": "", "gte": "at least ", "lte": "at most "}.get(op, "")
        return f'{how}{count} element(s) show "{target}"'
    return f"{'looks: ' if visual else ''}{expected or ''}".strip()


# --------------------------------------------------------------------------- building


def _service_hint(case: TestCase) -> bool:
    tags = {tag.strip().lower() for tag in case.tags}
    return "kind:api" in tags or "kind:sql" in tags


def is_check_only(step: Step) -> bool:
    return step.action.strip().lower() == VERIFY_ACTION and step.expected is not None


def source_steps(case: TestCase) -> list[SourceStep]:
    """The case's steps as the plan lists them, with their kind as written (payloads, check-only steps)."""
    hint = _service_hint(case)
    found: list[SourceStep] = []
    for number, step in enumerate(case.steps, start=1):
        if step.api is not None:
            kind, payload = "api", True
        elif step.sql is not None:
            kind, payload = "sql", True
        elif step.quality is not None:
            kind, payload = "quality", True
        elif step.security is not None:
            kind, payload = "security", True
        elif hint:
            kind, payload = ("sql" if "kind:sql" in {tag.strip().lower() for tag in case.tags} else "api"), False
        elif is_check_only(step):
            kind, payload = "verify", False
        else:
            kind, payload = "browser", False
        found.append(SourceStep(number=number, action=_clip(step.action), expected=_clip(step.expected) if step.expected else None, kind=kind, payload=payload))  # type: ignore[arg-type]
    return found


def _criterion(identifier: str, check: str, origin: str, *, outcome: bool = False, **fields: Any) -> Criterion | None:
    clean = {name: value for name, value in fields.items() if value is not None}
    text = criterion_text(
        check, **{name: clean.get(name) for name in ("expected", "target", "value", "state", "count")}, op=clean.get("op", "eq"), match=clean.get("match", "contains"), visual=bool(clean.get("visual"))
    )
    try:
        return Criterion(id=identifier, text=_clip(text or check), origin=origin, check=check, outcome=outcome, **clean)  # type: ignore[arg-type]
    except ValidationError as exc:
        logger.info("a criterion of %s was left out (%s)", identifier.rsplit(".", 1)[0], exc.errors()[0]["msg"] if exc.errors() else "invalid")
        return None


def _outcome_criterion(identifier: str, part: StepIntent, expected_text: str) -> Criterion | None:
    """An assertion the model read as a criterion checked by the step's own verification."""
    if part.kind in _ASSERT_TO_CHECK and part.expected:
        return _criterion(identifier, _ASSERT_TO_CHECK[part.kind], "author", outcome=True, expected=part.expected, match=part.match or "contains")
    if part.kind == "assert_visible" and part.target:
        return _criterion(identifier, "element_state", "author", outcome=True, target=part.target, state="visible")
    if part.kind == "assert_looks" and part.expected:
        return _criterion(identifier, "judged", "author", outcome=True, expected=part.expected, visual=True)
    if part.kind == "unknown":
        return _criterion(identifier, "judged", "author", outcome=True, expected=expected_text)
    return None


def _in_words(text: str | None, words: str) -> bool:
    flat = _flat(text)
    return len(flat) >= 2 and flat in _flat(words)


def _model_criteria(raw: Iterable[Mapping[str, Any]], step_id: Callable[[int], str | None], words: str, entered: set[str], outcomes: set[tuple[Any, str]]) -> dict[str, list[Criterion]]:
    """The model's criteria of a step, per plan step id: checked for structure, author only when the step's words state them,
    without values the step enters (checked as entered) or repeats of the step's outcome checks."""
    found: dict[str, list[Criterion]] = {}
    for reply in raw:
        if not isinstance(reply, Mapping):
            continue
        check = str(reply.get("check") or "")
        try:
            after = int(reply.get("after") or 1)
        except (TypeError, ValueError):
            after = 1
        identifier = step_id(after)
        if identifier is None or check not in ("text_visible", "text_absent", "url_matches", "field_value", "element_state", "count", "judged"):
            continue
        fields = {name: reply.get(name) for name in ("expected", "target", "value", "state", "count")}
        fields = {name: (" ".join(value.split()) if isinstance(value, str) else value) for name, value in fields.items() if value not in (None, "")}
        if check == "field_value" and _flat(fields.get("target")) in entered:
            continue
        if (check, _flat(fields.get("expected"))) in outcomes:
            continue
        literal = fields.get("expected") if check in ("text_visible", "text_absent", "url_matches", "judged") else fields.get("value") if check == "field_value" else fields.get("target")
        origin = "author" if reply.get("origin") == "author" and isinstance(literal, str) and _in_words(literal, words) else "inferred"
        bucket = found.setdefault(identifier, [])
        criterion = _criterion(f"{identifier}.c{len(bucket) + 1}", check, origin, **fields)  # type: ignore[arg-type]
        if criterion is not None and len(bucket) < 8:
            bucket.append(criterion)
    return found


def _risk(raw: Any) -> Risk:
    if not isinstance(raw, Mapping):
        return Risk()
    flags = [flag for flag in raw.get("flags") or () if flag in ("destructive", "irreversible", "external_side_effect")]
    reason = raw.get("reason")
    try:
        return Risk(flags=list(dict.fromkeys(flags))[:3], reason=_clip(str(reason), 500) if flags and reason else None)
    except ValidationError:
        return Risk()


def _action_step(identifier: str, number: int, part: StepIntent) -> PlanStep:
    kind = part.kind if part.kind in ACTION_KINDS or part.kind in _SERVICE else "unknown"
    fields = {"target": part.target, "value": part.value, "url": part.url, "role": part.role, "expected": part.expected if kind == "wait" else None}
    step = PlanStep(id=identifier, step=number, kind=kind, text="-", **{name: value for name, value in fields.items() if value is not None})  # type: ignore[arg-type]
    return step.model_copy(update={"text": _clip(step_text(step))})


def build_plan(case: TestCase, reading: CaseReading, *, model: str | None, source: Literal["model", "cache"] = "model") -> Plan:
    """The plan of ``case`` from its case reading (see the module docstring); every step gets at least one plan step."""
    sources = source_steps(case)
    steps: list[PlanStep] = []
    for number, (step, listed) in enumerate(zip(case.steps, sources), start=1):
        steps.extend(_plan_steps(number, step, listed, reading))
    return Plan(
        case_id=case.id,
        case_hash=case_hash(case),
        prompt_version=PROMPT_VERSION,
        model=model or None,
        created_at=now_iso(),
        source=source,
        source_steps=sources,
        steps=steps,
    )


def _plan_steps(number: int, step: Step, listed: SourceStep, reading: CaseReading) -> list[PlanStep]:
    if listed.payload or listed.kind in ("api", "sql"):
        return [PlanStep(id=f"{number}.1", step=number, kind=listed.kind, read="service", text=_clip(f"{listed.kind} step: {normalise_step_text(step.action)}"))]  # type: ignore[arg-type]
    extras = reading.extras.get(number) or {}
    plan: list[PlanStep] = []
    if listed.kind != "verify":
        plan.extend(_action_steps(number, step, reading, extras))
    if step.expected is not None:
        plan.extend(_expected_steps(number, step, reading, start=len(plan) + 1))
    for item in plan[:1]:
        needs = [other for other in reading.needs.get(number, ()) if 1 <= other <= 200 and other != number]
        if needs:
            plan[0] = item.model_copy(update={"needs": needs[:8]})
    return plan


def _action_steps(number: int, step: Step, reading: CaseReading, extras: Mapping[str, Any]) -> list[PlanStep]:
    raw = reading.get(number, "action", step.action)
    if raw is None:
        return [PlanStep(id=f"{number}.1", step=number, kind="unknown", text=_clip(normalise_step_text(step.action)), read="page")]
    parts = raw.parts()
    plan: list[PlanStep] = []
    risk = _risk(extras.get("risk"))
    action_ids: list[str] = []
    for position, part in enumerate(parts, start=1):
        identifier = f"{number}.{position}"
        if part.kind in _ASSERT_TO_CHECK or part.kind in ("assert_visible", "assert_looks"):
            criterion = _outcome_criterion(f"{identifier}.c1", part, step.action)
            if criterion is None:
                continue
            plan.append(PlanStep(id=identifier, step=number, kind="verify", text=_clip(criterion.text), criteria=[criterion]))
            continue
        item = _action_step(identifier, number, part)
        if item.kind in ACTION_KINDS:
            item = item.model_copy(update={"risk": risk})
            action_ids.append(identifier)
        plan.append(item)
    if plan:
        plan[0] = plan[0].model_copy(update={"complete": raw.complete})
    entered = {_flat(item.target) for item in plan if item.kind in ("fill", "select") and item.target}
    outcomes = {(criterion.check, _flat(criterion.expected)) for item in plan for criterion in item.criteria}
    by_position = {position: f"{number}.{position}" for position in range(1, len(parts) + 1)}

    def step_id(after: int) -> str | None:
        if not action_ids:
            return None
        wanted = by_position.get(min(max(after, 1), len(parts)))
        return wanted if wanted in action_ids else action_ids[-1]

    found = _model_criteria(extras.get("criteria") or (), step_id, normalise_step_text(step.action), entered, outcomes)
    return [item.model_copy(update={"criteria": [*item.criteria, *found.get(item.id, [])]}) for item in plan] or [
        PlanStep(id=f"{number}.1", step=number, kind="unknown", text=_clip(normalise_step_text(step.action)), read="page")
    ]


def _expected_steps(number: int, step: Step, reading: CaseReading, *, start: int) -> list[PlanStep]:
    expected = step.expected or ""
    identifier = f"{number}.{start}"
    if step.semantic:
        criterion = _criterion(f"{identifier}.c1", "judged", "author", outcome=True, expected=expected)
        return [PlanStep(id=identifier, step=number, kind="verify", item="expected", text=_clip(f"check by meaning: {normalise_step_text(expected)}"), criteria=[criterion] if criterion else [])]
    raw = reading.get(number, "expected", expected)
    if raw is None:
        return [PlanStep(id=identifier, step=number, kind="verify", item="expected", text=_clip(f"check: {normalise_step_text(expected)}"), read="page")]
    plan: list[PlanStep] = []
    criteria: list[Criterion] = []
    position = start
    for part in raw.parts():
        if part.kind == "wait":
            plan.append(_action_step(f"{number}.{position}", number, part).model_copy(update={"item": "expected"}))
            position += 1
    verify_id = f"{number}.{position}"
    for part in raw.parts():
        if part.kind == "wait":
            continue
        criterion = _outcome_criterion(f"{verify_id}.c{len(criteria) + 1}", part, expected)
        if criterion is not None and len(criteria) < 8:
            criteria.append(criterion)
    if criteria or not plan:
        plan.append(PlanStep(id=verify_id, step=number, kind="verify", item="expected", text=_clip(f"check: {normalise_step_text(expected)}"), criteria=criteria))
    return plan


# --------------------------------------------------------------------------- plan -> reading


def plan_reading(plan: Plan, case: TestCase, *, source: Literal["model", "cache", "plan"] = "plan") -> CaseReading:
    """The case reading a run executes for ``plan``: per step, its actions (with plan step ids, criteria and risk flags) and
    its expected outcome's checks; steps the plan marks ``read: page`` are left to be read on the page."""
    intents: dict[tuple[int, ItemRole], tuple[str, StepIntent]] = {}
    needs: dict[int, tuple[int, ...]] = {}
    for number, step in enumerate(case.steps, start=1):
        planned = plan.steps_of(number)
        actions = [item for item in planned if item.item == "action" and item.read == "case"]
        if actions and not is_check_only(step):
            intent = _joined(actions)
            if intent is not None:
                intents[(number, "action")] = (normalise_step_text(step.action), intent)
        outcome = [item for item in planned if item.item == "expected" and item.read == "case"]
        if outcome and step.expected is not None and not step.semantic:
            intent = _joined_outcome(outcome, number)
            if intent is not None:
                intents[(number, "expected")] = (normalise_step_text(step.expected), intent)
        if planned and planned[0].needs:
            needs[number] = tuple(planned[0].needs)
    return CaseReading(intents=intents, source=source, needs=needs)  # type: ignore[arg-type]


def _intent(item: PlanStep) -> StepIntent:
    criteria = tuple(criterion for criterion in item.criteria if not criterion.outcome)
    fields = {"target": item.target, "value": item.value, "url": item.url, "role": item.role, "expected": item.expected}
    return StepIntent(kind=item.kind if item.kind in ACTION_KINDS or item.kind in _SERVICE else "unknown", source="case", plan_step=item.id, criteria=criteria, risk=tuple(item.risk.flags), **fields)  # type: ignore[arg-type]


def _assert_intents(item: PlanStep) -> list[StepIntent]:
    found: list[StepIntent] = []
    for criterion in item.criteria:
        if not criterion.outcome:
            continue
        if criterion.check in _CHECK_TO_ASSERT:
            found.append(StepIntent(kind=_CHECK_TO_ASSERT[criterion.check], expected=criterion.expected, match=criterion.match if criterion.check == "url_matches" else None, source="case", plan_step=item.id))  # type: ignore[arg-type]
        elif criterion.check == "element_state" and criterion.state == "visible" and criterion.target:
            found.append(StepIntent(kind="assert_visible", target=criterion.target, source="case", plan_step=item.id))
        elif criterion.check == "judged" and criterion.visual:
            found.append(StepIntent(kind="assert_looks", expected=criterion.expected, source="case", plan_step=item.id))
        else:  # judged by meaning (or a check form only the plan can evaluate): the step's outcome is judged by its words
            found.append(StepIntent(kind="unknown", source="case", plan_step=item.id))
    return found


def _joined(items: Sequence[PlanStep]) -> StepIntent | None:
    parts: list[StepIntent] = []
    pending: tuple[Criterion, ...] = ()
    for item in items:
        if item.kind == "verify":
            parts.extend(_assert_intents(item))
            pending += tuple(criterion for criterion in item.criteria if not criterion.outcome)
            continue
        intent = _intent(item)
        if pending and parts:  # criteria a reviewer put on a check go with the action before it
            parts[-1] = parts[-1].model_copy(update={"criteria": parts[-1].criteria + pending})
            pending = ()
        parts.append(intent)
    if pending and parts:
        parts[-1] = parts[-1].model_copy(update={"criteria": parts[-1].criteria + pending})
    if not parts:
        return None
    first, *rest = parts
    return first.model_copy(update={"then": tuple(rest), "complete": items[0].complete})


def _joined_outcome(items: Sequence[PlanStep], number: int) -> StepIntent | None:
    parts: list[StepIntent] = []
    for item in items:
        parts.extend(_assert_intents(item) if item.kind == "verify" else [_intent(item)])
    if not parts:
        return None
    first, *rest = parts
    return first.model_copy(update={"then": tuple(rest)})


def outcome_extras(plan: Plan, number: int) -> tuple[Criterion, ...]:
    """Criteria of a step's outcome that no outcome check can run (a reviewer's field value, count or element state): the
    plan evaluates them at the end of the step."""
    return tuple(
        criterion
        for item in plan.steps_of(number)
        if item.item == "expected"
        for criterion in item.criteria
        if criterion.outcome and not (criterion.check in _CHECK_TO_ASSERT or criterion.check == "judged" or (criterion.check == "element_state" and criterion.state == "visible"))
    )


def check_plan(plan: Plan, case: TestCase) -> Plan:
    """``plan`` when it is a plan of ``case`` (same content, same steps), else :class:`PlanError` saying why."""
    if plan.case_hash != case_hash(case):
        raise PlanError(f"the plan was made for another version of case {plan.case_id!r} (its steps or data changed); make a new plan")
    if len(plan.source_steps) != len(case.steps):
        raise PlanError(f"the plan lists {len(plan.source_steps)} steps; the case has {len(case.steps)}")
    for listed, step in zip(plan.source_steps, case.steps):
        if listed.action != _clip(step.action):
            raise PlanError(f"step {listed.number} of the plan does not match the case's step {listed.number}")
    if not plan.steps:
        raise PlanError("the plan has no steps")
    return plan


# --------------------------------------------------------------------------- store


class PlanStore:
    """Plans on disk under ``<project>/.hercules/plans/``: the generated plan and the approved plan of each case version and
    model (``plan-<key>.json``, ``approved-<key>.json``) and the latest plan of each case (``case-<id>.json``)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, kind: str, name: str) -> Path:
        return self.directory / f"{kind}-{name}.json"

    def _load(self, path: Path) -> Plan | None:
        if path.is_symlink() or not path.is_file():
            return None
        try:
            return Plan.model_validate_json(path.read_bytes())
        except (OSError, ValidationError, ValueError):
            logger.warning("ignoring an unreadable plan file %s", path.name)
            return None

    def _save(self, path: Path, plan: Plan) -> Path | None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(plan.model_dump_json(indent=1), encoding="utf-8")
            temporary.replace(path)
            return path
        except OSError as exc:  # the store only saves time and keeps approvals; a run never fails on it
            logger.warning("could not save the plan (%s)", type(exc).__name__)
            return None

    def generated(self, key: str) -> Plan | None:
        return self._load(self._path("plan", key))

    def approved(self, key: str) -> Plan | None:
        return self._load(self._path("approved", key))

    def latest(self, case_id: str) -> Plan | None:
        return self._load(self._path("case", case_id))

    def save_generated(self, key: str, plan: Plan) -> None:
        self._save(self._path("plan", key), plan)
        self._save(self._path("case", plan.case_id), plan)

    def save_approved(self, key: str, plan: Plan) -> Path | None:
        self._save(self._path("case", plan.case_id), plan)
        return self._save(self._path("approved", key), plan)


def approved(plan: Plan, *, by: Literal["auto", "user", "agent", "file"], edited: bool = False, note: str | None = None) -> Plan:
    """``plan`` marked approved (or edited and approved) by ``by``, now."""
    return plan.model_copy(update={"review": PlanReview(state="edited" if edited else "approved", by=by, at=now_iso(), note=note), "source": "edited" if edited else "approved"})


# --------------------------------------------------------------------------- diff


def _step_view(step: PlanStep) -> dict[str, Any]:
    return {
        "kind": step.kind,
        "text": step.text,
        "target": step.target,
        "value": step.value,
        "url": step.url,
        "role": step.role,
        "criteria": [f"{criterion.origin}: {criterion.text}" for criterion in step.criteria],
        "risk": list(step.risk.flags),
    }


def diff_plans(before: Plan, after: Plan) -> dict[str, Any]:
    """What changed from ``before`` to ``after``: plan steps added, removed and changed (by id), criteria added and removed,
    and whether the source steps' order changed."""
    old = {step.id: step for step in before.steps}
    new = {step.id: step for step in after.steps}
    changed: list[dict[str, Any]] = []
    for identifier in [step.id for step in after.steps if step.id in old]:
        a, b = _step_view(old[identifier]), _step_view(new[identifier])
        if a == b:
            continue
        fields = {name: {"before": a[name], "after": b[name]} for name in a if a[name] != b[name] and name != "criteria"}
        criteria_added = [text for text in b["criteria"] if text not in a["criteria"]]
        criteria_removed = [text for text in a["criteria"] if text not in b["criteria"]]
        changed.append({"id": identifier, "step": new[identifier].step, "fields": fields, "criteria_added": criteria_added, "criteria_removed": criteria_removed})
    added = [{"id": step.id, "step": step.step, **_step_view(step)} for step in after.steps if step.id not in old]
    removed = [{"id": step.id, "step": step.step, **_step_view(step)} for step in before.steps if step.id not in new]
    order_before = before.order or [step.number for step in before.source_steps]
    order_after = after.order or [step.number for step in after.source_steps]
    return {
        "same_case": before.case_hash == after.case_hash,
        "added": added,
        "removed": removed,
        "changed": changed,
        "order": {"before": order_before, "after": order_after} if order_before != order_after else None,
        "unchanged": sum(1 for step in after.steps if step.id in old and _step_view(old[step.id]) == _step_view(step)),
    }


def as_run(plan: Plan) -> Plan:
    """The plan as the run carried it out: each amended source step's last amendment shown as a plan step of its own
    (``<step>.<n>``), so a diff against the plan lists what the run changed."""
    extra: list[PlanStep] = []
    counters: dict[int, int] = {}
    for amendment in plan.amendments:
        if amendment.after is None or amendment.kind in ("reorder", "run_first"):
            continue
        base = max((int(step.id.split(".")[1]) for step in plan.steps_of(amendment.step)), default=0)
        counters[amendment.step] = counters.get(amendment.step, base) + 1
        if counters[amendment.step] > 99:
            continue
        extra.append(PlanStep(id=f"{amendment.step}.{counters[amendment.step]}", step=amendment.step, kind="unknown", text=_clip(f"{amendment.kind}: {amendment.after}")))
    order = list(plan.order)
    for amendment in plan.amendments:
        if amendment.kind in ("reorder", "run_first") and amendment.after:
            try:
                order = [int(part) for part in amendment.after.split(",")]
            except ValueError:
                continue
    if order and sorted(order) != [step.number for step in plan.source_steps]:
        order = list(plan.order)
    return plan.model_copy(update={"steps": [*plan.steps, *extra], "order": order})


_ORIGIN = {"author": "author", "inferred": "inferred"}


def render_plan(plan: Plan) -> str:
    """The plan as text: per source step, its plan steps with criteria and risks; then coverage and lint."""
    lines = [f"Plan of {plan.case_id} ({len(plan.steps)} plan steps; {plan.source}, {plan.review.state}; model {plan.model or 'none'}, prompt {plan.prompt_version})"]
    for listed in plan.source_steps:
        expected = f"  -> {listed.expected}" if listed.expected else ""
        lines.append(f"{listed.number}. {listed.action}{expected}")
        for item in plan.steps_of(listed.number):
            flags = f"  [risk: {', '.join(item.risk.flags)}{' - ' + item.risk.reason if item.risk.reason else ''}]" if item.risk.flags else ""
            needs = f"  (after step {', '.join(map(str, item.needs))})" if item.needs else ""
            read = "  (read on the page when it runs)" if item.read == "page" else ""
            lines.append(f"   {item.id} {item.kind}: {item.text}{needs}{flags}{read}")
            for criterion in item.criteria:
                lines.append(f"        {'✓' if criterion.origin == 'author' else '~'} [{_ORIGIN[criterion.origin]}] {criterion.text} ({criterion.check})")
    coverage = plan.coverage
    if coverage.checked:
        gaps = ", ".join(f"step {gap.step} ({gap.probability:.0%}{', repaired' if gap.repaired else ''})" for gap in coverage.gaps) or "none"
        lines.append(f"Coverage ({coverage.engine}): {coverage.steps} step(s) audited; gaps: {gaps}")
    else:
        lines.append(f"Coverage: not audited ({coverage.note or 'off'})")
    if plan.lint:
        lines.append(f"Lint: {len(plan.lint_errors)} error(s), {len(plan.lint_warnings)} warning(s)")
        for finding in plan.lint:
            where = f"step {finding.step}: " if finding.step else ""
            hint = f" -> {finding.suggestion}" if finding.suggestion else ""
            lines.append(f"  {finding.severity.upper()} {finding.code} ({finding.source}) {where}{finding.message}{hint}")
    else:
        lines.append("Lint: no findings")
    return "\n".join(lines)
