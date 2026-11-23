"""A run's plan phase: make (or reuse, or take) the plan, review it, block in strict mode, and keep ``plan.json``.

The run graph (:mod:`testzeus_hercules.engine.run_graph`) uses one :class:`PlanRun` per run:

* :meth:`PlanRun.prepare` makes the plan before the browser starts (see
  :func:`~testzeus_hercules.engine.planning.make_plan`), or checks a plan a caller gave
  (``--plan FILE``, an agent's edited plan), and writes it to ``plan.json`` in the run directory;
  the run then executes the plan's reading (:func:`~testzeus_hercules.engine.plan.plan_reading`);
* :meth:`PlanRun.review` asks for approval when the run's review is ``ask`` and no approved plan
  of this case version and model exists: approve, edit (the edited plan is read from
  ``plan.edited.json`` and checked, see :func:`~testzeus_hercules.engine.planning.apply_edits`)
  or reject; an approved plan is stored and reused until the case or the model changes;
* :meth:`PlanRun.blocked` stops a strict run whose plan has lint errors;
* :meth:`PlanRun.outcome_results` maps a step's outcome checks (run by the step's verification)
  to the plan's criteria; :meth:`PlanRun.finish` writes ``plan.json`` again with every criterion's
  result and every amendment, and returns the summary for ``RunResult.plan``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import ValidationError

from testzeus_hercules.cases.models import TestCase
from testzeus_hercules.cases.plan import Amendment, Criterion, CriterionResult, Plan, PlanSummary
from testzeus_hercules.decide.engine import DecisionEngine
from testzeus_hercules.engine.intent import CaseReading, Interpreter
from testzeus_hercules.engine.masking import masked_json
from testzeus_hercules.engine.plan import PlanError, PlanStore, approved, case_hash, check_plan, kept_criteria, outcome_extras, plan_key, plan_reading
from testzeus_hercules.engine.planning import PlanOptions, apply_edits, approve_plan, make_plan

logger = logging.getLogger(__name__)

__all__ = ["PLAN_FILE", "EDITED_PLAN_FILE", "PlanRun", "read_plan"]

PLAN_FILE = "plan.json"
EDITED_PLAN_FILE = "plan.edited.json"
# A step's outcome checks as the ledger names them, per criterion check form.
_LEDGER_CHECKS = {
    # A step's 'the "Name" field contains "x"' is planned as page text and checked on the field itself (verify.field_check).
    "text_visible": ("text_contains", "field_contains", "field_equals"),
    "text_absent": ("text_absent",),
    "url_matches": ("url_contains", "url_equals"),
    "element_state": ("visible",),
    "judged": ("semantic", "visual"),
    "file_name": ("file_name_equals", "file_name_contains"),
    "file_type": ("file_type",),
    "file_text": ("file_text",),
    "file_not_text": ("file_not_text",),
    "file_rows": ("file_rows",),
    "file_column": ("file_column",),
    "file_json": ("file_json",),
}


def read_plan(run_dir: Path, name: str = PLAN_FILE) -> Plan | None:
    """A run's plan (``plan.json``), or None when it is missing or invalid."""
    path = run_dir / name
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return Plan.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError):
        return None


class PlanRun:
    """One run's plan (see the module docstring)."""

    def __init__(
        self,
        case: TestCase,
        *,
        run_dir: Path,
        plans_dir: Path | None,
        interpreter: Interpreter | None,
        decider: DecisionEngine,
        env: Mapping[str, str],
        names: Sequence[str],
        hidden: Sequence[str],
        service: bool,
        options: PlanOptions,
        redact: Any,
        given: Plan | None = None,
        resolve: Any = None,
        given_by: str = "file",
    ) -> None:
        self.case = case
        self.run_dir = run_dir
        self.interpreter = interpreter
        self.decider = decider
        self.env = env
        self.names = names
        self.hidden = hidden
        self.service = service
        self.options = options
        self.redact = redact
        self.resolve = resolve or (lambda text: text)
        self.given = given
        self.given_by = given_by
        self.model_id = str(getattr(interpreter, "model_id", "") or "")
        # Plans are stored (and reused) only for a real model: a test double's readings are not worth keeping.
        self.store = PlanStore(plans_dir) if plans_dir is not None and self.model_id else None
        self.key = plan_key(case_hash(case), self.model_id)
        self.plan: Plan | None = None
        self.origin: str = "none"
        self.records: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ making

    async def prepare(self) -> CaseReading:
        """Make, reuse or take the plan and write ``plan.json``; returns the reading the run executes (empty: none)."""
        if self.given is not None:
            try:
                plan = check_plan(self.given, self.case)
            except PlanError as exc:
                raise PlanError(f"the given plan cannot run: {exc}") from None
            self.plan = plan if plan.review.state in ("approved", "edited") else approved(plan, by=self.given_by)  # type: ignore[arg-type]
            self.origin = "given"
        elif self.interpreter is None:
            return CaseReading()
        else:
            outcome = await make_plan(
                self.case,
                interpreter=self.interpreter,
                decider=self.decider,
                model_id=self.model_id,
                store=self.store,
                env=self.env,
                names=self.names,
                hidden=self.hidden,
                service=self.service,
                options=self.options,
                redact=self.redact,
            )
            self.plan, self.origin, self.records = outcome.plan, outcome.origin, outcome.records
        if self.plan is not None:
            self.plan = kept_criteria(self.plan)
        self.write()
        return self.reading()

    def reading(self) -> CaseReading:
        if self.plan is None:
            return CaseReading()
        source = {"model": "model", "cache": "cache"}.get(self.origin, "plan")
        return plan_reading(self.plan, self.case, source=source)  # type: ignore[arg-type]

    def resume(self) -> CaseReading:
        """On resume: the plan the run started with (``plan.json``), or a new one when it is missing."""
        found = read_plan(self.run_dir)
        if found is not None:
            try:
                self.plan = kept_criteria(check_plan(found, self.case))
                self.origin = "given"
            except PlanError:
                self.plan = None
        return self.reading() if self.plan is not None else CaseReading()

    def write(self, plan: Plan | None = None) -> None:
        plan = plan or self.plan
        if plan is None:
            return
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            (self.run_dir / PLAN_FILE).write_text(masked_json(plan, self.redact, indent=2), encoding="utf-8")
        except OSError as exc:  # evidence only: the run goes on
            logger.warning("could not write the run's plan (%s)", type(exc).__name__)

    # ------------------------------------------------------------------ review and strict mode

    @property
    def needs_review(self) -> bool:
        return self.plan is not None and self.plan.review.state not in ("approved", "edited") and self.origin != "given"

    async def review(self, control: Any) -> tuple[bool, str | None]:
        """Ask for the plan's approval (``review: ask``). Returns (go on, why not); an edit is checked and becomes the plan."""
        assert self.plan is not None
        answer = await control.approve_plan(len(self.plan.steps), errors=len(self.plan.lint_errors), warnings=len(self.plan.lint_warnings))
        if answer.decision == "reject":
            self.plan = self.plan.model_copy(update={"review": self.plan.review.model_copy(update={"state": "rejected", "by": "user"})})
            self.write()
            return False, "the plan was not approved" + (" (no answer in time)" if answer.timed_out else "")
        if getattr(answer, "plan_edited", False):
            edited = read_plan(self.run_dir, EDITED_PLAN_FILE)
            if edited is None:
                return False, f"the edited plan ({EDITED_PLAN_FILE}) is missing or not a valid plan"
            try:
                self.plan = await apply_edits(self.plan, edited, self.case, interpreter=self.interpreter, by="user")
            except PlanError as exc:
                return False, f"the edited plan cannot run: {exc}"
        if self.store is not None:
            self.plan = approve_plan(self.plan, self.store, self.key, by="user")
        elif self.plan.review.state not in ("approved", "edited"):
            self.plan = approved(self.plan, by="user")
        self.origin = "given"
        self.write()
        return True, None

    def blocked(self) -> str | None:
        """Why a strict run cannot start (its plan has lint errors), or None."""
        if not self.options.strict or self.plan is None or not self.plan.lint_errors:
            return None
        first = self.plan.lint_errors[0]
        where = f"step {first.step}: " if first.step else ""
        more = f" (and {len(self.plan.lint_errors) - 1} more)" if len(self.plan.lint_errors) > 1 else ""
        return self.redact(f"strict mode: the plan has {len(self.plan.lint_errors)} lint error(s); {where}{first.message}{more}. See plan.json or `hercules plan`")

    # ------------------------------------------------------------------ during the run

    def order(self) -> tuple[int, ...] | None:
        """The order the plan says the steps run in (a reviewer's order), or None (as written, or by the steps' needs)."""
        if self.plan is None or not self.plan.order:
            return None
        order = tuple(self.plan.order)
        return order if order != tuple(range(1, len(self.case.steps) + 1)) else None

    def end_criteria(self) -> dict[int, tuple[Criterion, ...]]:
        if self.plan is None:
            return {}
        found = {number: outcome_extras(self.plan, number) for number in range(1, len(self.case.steps) + 1)}
        return {number: criteria for number, criteria in found.items() if criteria}

    def outcome_results(self, number: int, ledger: Sequence[Any], *, ran: bool = True) -> tuple[CriterionResult, ...]:
        """The results of step ``number``'s outcome criteria, from its verification's ledger entries (in order)."""
        if self.plan is None:
            return ()
        entries = [entry for entry in ledger if getattr(entry, "step", None) == number and getattr(entry, "check", "") not in ("plan_criterion", "plan_criterion_inferred")]
        used: set[int] = set()
        results: list[CriterionResult] = []
        for item in self.plan.steps_of(number):
            for criterion in item.criteria:
                if not criterion.outcome or criterion.check not in _LEDGER_CHECKS:
                    continue
                labels = _LEDGER_CHECKS[criterion.check]
                literal = str(criterion.expected or criterion.target or "")
                wanted = " ".join(self.redact(self._resolved(literal)).split()).casefold()
                match = next(
                    (
                        position
                        for position, entry in enumerate(entries)
                        if position not in used and entry.check in labels and (criterion.check == "judged" or " ".join(entry.expected.split()).casefold() == wanted)
                    ),
                    None,
                )
                if match is None and ran:  # the same check of the step's outcome, in order (its text written another way)
                    match = next((position for position, entry in enumerate(entries) if position not in used and entry.check in labels), None)
                if match is None and ran:
                    match = next((position for position, entry in enumerate(entries) if position not in used and entry.check in ("semantic",)), None)
                if match is None:
                    status, actual = "not_run", ("checked by meaning instead" if ran else "the step did not run")
                else:
                    used.add(match)
                    status, actual = entries[match].verdict, entries[match].actual
                results.append(
                    CriterionResult(criterion=criterion.id, plan_step=item.id, step=number, origin=criterion.origin, check=criterion.check, text=self.redact(criterion.text), status=status, actual=actual)  # type: ignore[arg-type]
                )
        return tuple(results)

    def _resolved(self, text: str) -> str:
        try:
            return str(self.resolve(text))
        except Exception:  # a value that cannot be resolved (a secret not set) is compared as written
            return text

    def finish(self, results: Sequence[CriterionResult], amendments: Sequence[Amendment]) -> PlanSummary | None:
        """Write ``plan.json`` with the criteria results (the last one per criterion; unchecked ones as not run) and the
        amendments; the summary for ``RunResult.plan``."""
        if self.plan is None:
            return None
        latest: dict[str, CriterionResult] = {}
        for result in results:
            latest[result.criterion] = result
        for item in self.plan.steps:
            for criterion in item.criteria:
                if criterion.id not in latest:
                    latest[criterion.id] = CriterionResult(
                        criterion=criterion.id,
                        plan_step=item.id,
                        step=item.step,
                        origin=criterion.origin,
                        check=criterion.check,
                        text=self.redact(criterion.text),
                        status="not_run",
                        actual="not reached",
                    )
        order = {criterion.id: position for position, criterion in enumerate(criterion for item in self.plan.steps for criterion in item.criteria)}
        ordered = sorted(latest.values(), key=lambda result: order.get(result.criterion, len(order)))
        final = self.plan.model_copy(update={"results": ordered, "amendments": [*self.plan.amendments, *amendments]})
        self.plan = final
        self.write(final)
        return final.summary(strict=self.options.strict)
