"""The plan of a run (:class:`Plan`): how the model read a case, made explicit before the browser starts.

For each step of the case (a *source step*) the plan holds one or more plan steps: an
action (navigate, click, fill, select, upload, download, check, uncheck, hover, press, wait), an API, SQL or
quality step, or ``verify`` (the step's expected outcome, checked). Each plan step carries
its target and value as the model read them (placeholders such as ``{{password}}`` kept,
never secret values), the steps whose field must be set first (``needs``), risk flags with a
reason, and success criteria: what the page should show once the plan step is done.

A criterion is ``author`` when the case's own words state it (a step's expected outcome, or
words of the step) and ``inferred`` when the model inferred it ("the New Record dialog is
open" after clicking "New"). Each has a check form the engine evaluates on the settled page
after the plan step: ``text_visible``, ``text_absent``, ``url_matches``, ``field_value``,
``element_state``, ``count`` or ``judged`` (a model judges it by meaning on the page's text
and, when images may be sent, its screenshot). Author criteria failing fail the step;
inferred ones feed recovery and, when still unmet, are warnings (they fail the run only in
strict mode).

The plan also records the coverage audit (an independent check that every obligation of
every source step is covered), the pre-run lint findings, how it was reviewed, and, once the
run is over, every amendment made while it ran (a step read again on the page, a
continuation, a reorder, a value re-entered the way a field asks for it, a person's edit) and
each criterion's result. It is stored as ``plan.json`` beside ``run.json``; its JSON Schema is
published as ``cases/schemas/plan.schema.json``.

This module only describes plans; the engine builds, checks and executes them
(:mod:`testzeus_hercules.engine.plan`).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

PLAN_VERSION = 1

PlanStepKind = Literal["navigate", "click", "fill", "select", "upload", "download", "check", "uncheck", "hover", "press", "wait", "api", "sql", "quality", "security", "verify", "unknown"]
ACTION_KINDS: frozenset[str] = frozenset({"navigate", "click", "fill", "select", "upload", "download", "check", "uncheck", "hover", "press", "wait", "unknown"})
CriterionCheck = Literal[
    "text_visible", "text_absent", "url_matches", "field_value", "element_state", "count", "judged", "file_name", "file_type", "file_text", "file_not_text", "file_rows", "file_column", "file_json"
]
CriterionOrigin = Literal["author", "inferred"]
ElementState = Literal["open", "closed", "visible", "hidden", "enabled", "disabled", "checked", "unchecked", "expanded", "collapsed"]
CountOp = Literal["eq", "gte", "lte"]
RiskFlag = Literal["destructive", "irreversible", "external_side_effect"]
LintSeverity = Literal["error", "warning", "info"]
LintCode = Literal[
    "unsupported_step",
    "suspicious_value",
    "credential_in_url",
    "unresolved_placeholder",
    "missing_secret",
    "ambiguous_reference",
    "unconfirmed_risk",
    "coverage_gap",
    "unclear_outcome",
    "other",
]
AmendmentKind = Literal["reread", "continuation", "recovery", "reorder", "run_first", "reformat", "substitute", "edit", "repair"]
AmendmentSource = Literal["model", "user", "engine"]
CriterionStatus = Literal["passed", "failed", "unverified", "not_run"]
PlanSource = Literal["model", "cache", "approved", "edited", "file"]
ReviewState = Literal["generated", "approved", "edited", "rejected"]
ReviewBy = Literal["auto", "user", "agent", "file"]
SourceKind = Literal["browser", "api", "sql", "quality", "security", "verify"]

PLAN_STEP_ID = r"^[1-9][0-9]{0,2}\.[1-9][0-9]{0,1}$"
CRITERION_ID = r"^[1-9][0-9]{0,2}\.[1-9][0-9]{0,1}\.c[1-9][0-9]{0,1}$"
MAX_PLAN_STEPS = 1200
MAX_CRITERIA = 8
_TEXT = 2000

Text = Annotated[str, StringConstraints(min_length=1, max_length=_TEXT)]
Count = Annotated[int, Field(ge=0)]
StepNumber = Annotated[int, Field(ge=1, le=200)]
# What each check form needs besides its human-readable text.
_REQUIRED: dict[str, tuple[str, ...]] = {
    "text_visible": ("expected",),
    "text_absent": ("expected",),
    "url_matches": ("expected",),
    "field_value": ("target", "value"),
    "element_state": ("state",),
    "count": ("target", "count"),
    "judged": ("expected",),
    "file_name": ("expected",),
    "file_type": ("expected",),
    "file_text": ("expected",),
    "file_not_text": ("expected",),
    "file_rows": ("expected",),
    "file_column": ("expected", "target"),
    "file_json": ("expected", "target"),
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Criterion(_Model):
    """One success criterion of a plan step: what the page should show once the step is done, and how it is checked."""

    id: Annotated[str, StringConstraints(pattern=CRITERION_ID)]
    text: Text = Field(description="The criterion in words (shown to reviewers; a judged criterion is judged by it)")
    origin: CriterionOrigin = Field(description="author: the case's own words state it; inferred: the model inferred it")
    check: CriterionCheck
    expected: Annotated[str, StringConstraints(max_length=_TEXT)] | None = Field(default=None, description="text_visible/text_absent: the text; url_matches: the address part; judged: the expectation")
    target: Annotated[str, StringConstraints(max_length=300)] | None = Field(
        default=None, description="field_value: the field's label; element_state/count: the element's label or text (a dialog's name for open/closed)"
    )
    value: Annotated[str, StringConstraints(max_length=_TEXT)] | None = Field(default=None, description="field_value: the value the field should hold (placeholders kept)")
    state: ElementState | None = None
    count: Count | None = None
    op: CountOp = "eq"
    match: Literal["contains", "equals"] = "contains"
    visual: bool = Field(default=False, description="judged: about how the page looks, judged on the screenshot")
    outcome: bool = Field(default=False, description="One of the step's own checks (its expected outcome, or a check its words state), run by the step's verification")

    @model_validator(mode="after")
    def _fields_for_check(self) -> Criterion:
        missing = [name for name in _REQUIRED[self.check] if getattr(self, name) in (None, "")]
        if missing:
            raise ValueError(f"a {self.check} criterion needs {', '.join(missing)}")
        return self


class Risk(_Model):
    """What a plan step may do that is hard to undo, as the model judged it, with its reason."""

    flags: list[RiskFlag] = Field(default_factory=list, max_length=3)
    reason: Annotated[str, StringConstraints(max_length=500)] | None = None

    @property
    def risky(self) -> bool:
        return bool(self.flags)


class PlanStep(_Model):
    """One action or check the run will carry out for a source step."""

    id: Annotated[str, StringConstraints(pattern=PLAN_STEP_ID)] = Field(description="<source step>.<position>, e.g. 3.1")
    step: StepNumber = Field(description="The source step (1-based) this plan step belongs to")
    item: Literal["action", "expected"] = Field(default="action", description="What it was read from: the step's action, or its expected outcome")
    read: Literal["case", "page", "service"] = Field(
        default="case", description="case: read before the run; page: not read yet, read on the page when it runs; service: an API/SQL/quality step run from its payload or read when it runs"
    )
    kind: PlanStepKind
    text: Text = Field(description="What the plan step does, in words")
    target: Annotated[str, StringConstraints(max_length=500)] | None = None
    value: Annotated[str, StringConstraints(max_length=_TEXT)] | None = Field(default=None, description="fill/select: the value; press: the key; wait: seconds (placeholders kept)")
    url: Annotated[str, StringConstraints(max_length=_TEXT)] | None = None
    role: Annotated[str, StringConstraints(max_length=40)] | None = None
    expected: Annotated[str, StringConstraints(max_length=_TEXT)] | None = Field(default=None, description="wait: a text to wait for")
    needs: list[StepNumber] = Field(default_factory=list, max_length=8, description="Source steps whose field must be set before this step's field")
    complete: bool | None = Field(default=None, description="Whether the step's words name every control it uses (false: it is finished on the page)")
    risk: Risk = Field(default_factory=Risk)
    criteria: list[Criterion] = Field(default_factory=list, max_length=MAX_CRITERIA)


class SourceStep(_Model):
    """A step of the case as written."""

    number: StepNumber
    action: Annotated[str, StringConstraints(max_length=_TEXT)]
    expected: Annotated[str, StringConstraints(max_length=_TEXT)] | None = None
    kind: SourceKind = "browser"
    payload: bool = Field(default=False, description="The step carries a structured api, sql, quality or security payload (run as written)")


class CoverageGap(_Model):
    """A source step the coverage audit found not fully covered by the plan."""

    step: StepNumber
    probability: Annotated[float, Field(ge=0.0, le=1.0)] = Field(description="The audit's probability that the plan covers every obligation of the step")
    repaired: bool = False


class Coverage(_Model):
    """The coverage audit: an independent check that the plan covers every obligation of every source step."""

    checked: bool = False
    engine: str | None = None
    steps: Count = 0
    gaps: list[CoverageGap] = Field(default_factory=list)
    repaired: list[StepNumber] = Field(default_factory=list, description="Steps read again because of a gap")
    calls: Count = 0
    cost_usd: Annotated[float, Field(ge=0.0)] | None = None
    note: str | None = None

    @property
    def open_gaps(self) -> list[CoverageGap]:
        return [gap for gap in self.gaps if not gap.repaired]


class LintFinding(_Model):
    """A problem with the test itself, found before the browser starts."""

    severity: LintSeverity
    code: LintCode = "other"
    step: StepNumber | None = None
    message: Annotated[str, StringConstraints(min_length=1, max_length=1000)]
    suggestion: Annotated[str, StringConstraints(max_length=1000)] | None = None
    source: Literal["model", "fact"] = "fact"


class Amendment(_Model):
    """A change made to the plan while the run ran, with its reason: never silent."""

    step: StepNumber
    plan_step: Annotated[str, StringConstraints(pattern=PLAN_STEP_ID)] | None = None
    kind: AmendmentKind
    before: Annotated[str, StringConstraints(max_length=_TEXT)] | None = None
    after: Annotated[str, StringConstraints(max_length=_TEXT)] | None = None
    reason: Annotated[str, StringConstraints(max_length=_TEXT)] = ""
    source: AmendmentSource
    at: str = ""


class CriterionResult(_Model):
    """How a criterion came out when the run checked it."""

    criterion: Annotated[str, StringConstraints(pattern=CRITERION_ID)]
    plan_step: Annotated[str, StringConstraints(pattern=PLAN_STEP_ID)]
    step: StepNumber
    origin: CriterionOrigin
    check: CriterionCheck
    text: str
    status: CriterionStatus
    actual: Annotated[str, StringConstraints(max_length=_TEXT)] | None = None
    recovered: bool = Field(default=False, description="The criterion was unmet and drove a recovery")


class PlanReview(_Model):
    state: ReviewState = "generated"
    by: ReviewBy | None = None
    at: str | None = None
    note: Annotated[str, StringConstraints(max_length=1000)] | None = None


class PlanTiming(_Model):
    """What making the plan cost: wall time per phase and model calls."""

    reading_ms: Count = 0
    coverage_ms: Count = 0
    lint_ms: Count = 0
    total_ms: Count = 0
    model_calls: Count = 0
    decider_calls: Count = 0
    cost_usd: Annotated[float, Field(ge=0.0)] | None = None
    cached: bool = False


class Plan(_Model):
    """The plan of one case (see the module docstring)."""

    version: Literal[1] = 1
    case_id: str
    case_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    prompt_version: str
    model: str | None = None
    created_at: str = ""
    source: PlanSource = "model"
    source_steps: list[SourceStep] = Field(min_length=1, max_length=200)
    steps: list[PlanStep] = Field(max_length=MAX_PLAN_STEPS)
    order: list[StepNumber] = Field(default_factory=list, description="The order the source steps run in (empty: as written)")
    coverage: Coverage = Field(default_factory=Coverage)
    lint: list[LintFinding] = Field(default_factory=list)
    review: PlanReview = Field(default_factory=PlanReview)
    timing: PlanTiming = Field(default_factory=PlanTiming)
    amendments: list[Amendment] = Field(default_factory=list)
    results: list[CriterionResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Plan:
        numbers = [step.number for step in self.source_steps]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("source_steps must be numbered 1, 2, 3 ... in order")
        known = set(numbers)
        ids: set[str] = set()
        criteria: set[str] = set()
        for step in self.steps:
            if step.step not in known:
                raise ValueError(f"plan step {step.id} names step {step.step}, which the case does not have")
            if not step.id.startswith(f"{step.step}."):
                raise ValueError(f"plan step {step.id} must be numbered {step.step}.<n> (it belongs to step {step.step})")
            if step.id in ids:
                raise ValueError(f"plan step id {step.id} is used twice")
            ids.add(step.id)
            unknown = [number for number in step.needs if number not in known]
            if unknown:
                raise ValueError(f"plan step {step.id} needs step(s) {unknown}, which the case does not have")
            for criterion in step.criteria:
                if not criterion.id.startswith(f"{step.id}.c"):
                    raise ValueError(f"criterion {criterion.id} must be numbered {step.id}.c<n> (it belongs to plan step {step.id})")
                if criterion.id in criteria:
                    raise ValueError(f"criterion id {criterion.id} is used twice")
                criteria.add(criterion.id)
        covered = {step.step for step in self.steps}
        missing = sorted(known - covered)
        if self.steps and missing:
            raise ValueError(f"step(s) {missing} have no plan step")
        if self.order and sorted(self.order) != numbers:
            raise ValueError("order must list every source step exactly once")
        return self

    def steps_of(self, number: int) -> list[PlanStep]:
        """The plan steps of source step ``number``, in plan order."""
        return [step for step in self.steps if step.step == number]

    def criterion(self, criterion_id: str) -> Criterion | None:
        return next((criterion for step in self.steps for criterion in step.criteria if criterion.id == criterion_id), None)

    @property
    def lint_errors(self) -> list[LintFinding]:
        return [finding for finding in self.lint if finding.severity == "error"]

    @property
    def lint_warnings(self) -> list[LintFinding]:
        return [finding for finding in self.lint if finding.severity == "warning"]

    def summary(self, *, strict: bool = False) -> PlanSummary:
        """Counts for ``RunResult.plan``."""
        every = [criterion for step in self.steps for criterion in step.criteria]
        statuses = [result.status for result in self.results]
        return PlanSummary(
            steps=len(self.steps),
            criteria=len(every),
            author=sum(criterion.origin == "author" for criterion in every),
            inferred=sum(criterion.origin == "inferred" for criterion in every),
            passed=statuses.count("passed"),
            failed=statuses.count("failed"),
            unverified=statuses.count("unverified"),
            not_run=statuses.count("not_run"),
            recoveries=sum(result.recovered for result in self.results),
            amendments=len(self.amendments),
            lint_errors=len(self.lint_errors),
            lint_warnings=len(self.lint_warnings),
            coverage_gaps=len(self.coverage.open_gaps),
            strict=strict,
            source=self.source,
            review=self.review.state,
            planning_ms=self.timing.total_ms,
        )


class PlanSummary(_Model):
    """The plan of a run in numbers (``RunResult.plan``); the plan itself is ``plan.json`` beside ``run.json``."""

    steps: Count = 0
    criteria: Count = 0
    author: Count = 0
    inferred: Count = 0
    passed: Count = 0
    failed: Count = 0
    unverified: Count = 0
    not_run: Count = 0
    recoveries: Count = Field(default=0, description="Unmet criteria that drove a recovery")
    amendments: Count = 0
    lint_errors: Count = 0
    lint_warnings: Count = 0
    coverage_gaps: Count = Field(default=0, description="Source steps the coverage audit still found not covered after the repair")
    strict: bool = False
    source: PlanSource = "model"
    review: ReviewState = "generated"
    planning_ms: Count = 0
    file: str | None = Field(default="plan.json", description="The plan's file, relative to the run directory")
