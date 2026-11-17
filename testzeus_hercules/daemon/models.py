"""Request and response bodies of the daemon API (pydantic; also the source of the OpenAPI schemas)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from testzeus_hercules.cases.browser_options import BrowserOptions
from testzeus_hercules.cases.models import (
    BASE_URL_PATTERN,
    BUILTIN_PLACEHOLDERS,
    SECRET_NAME_PATTERN,
    VIEWPORT_PATTERN,
    Browser,
    DataKey,
    EnvironmentRef,
    MobileApp,
    RunResult,
    SecretRef,
    TestCase,
    TestDataValue,
    check_web_settings,
    placeholder_names_in,
)
from testzeus_hercules.cases.plan import Plan
from testzeus_hercules.quality.findings import Impact

RunMode = Literal["auto", "pure", "hybrid"]
Egress = Literal["local", "hybrid-text", "hybrid"]
TestDecider = Literal["stub"]
RunStatus = Literal["queued", "running", "paused", "needs_approval", "needs_plan_approval", "passed", "failed", "error", "cancelled"]
PlanReview = Literal["auto", "ask"]
DESTRUCTIVE_TERM = Annotated[str, StringConstraints(min_length=1, max_length=60, pattern=r"^[^,\n]+$")]
ENV_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
MAX_TEXT = 200_000

EnvName = Annotated[str, StringConstraints(pattern=ENV_NAME_PATTERN)]
BaseUrl = Annotated[str, StringConstraints(pattern=BASE_URL_PATTERN, max_length=2000)]
FolderPath = Annotated[str, StringConstraints(max_length=1000)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunSettings(_Body):
    """The options of a run: where it runs (``environment``, ``base_url``) and how. ``POST /api/runs`` takes them
    with the case; a re-run inherits them from its parent run and takes changes to them.

    ``decider`` (``stub``) is a test double: no model is used and no step is read.
    """

    environment: EnvName | None = None
    base_url: BaseUrl | None = None
    mode: RunMode = "auto"
    egress: Egress | None = None
    decider: TestDecider | None = None
    headed: bool = False
    max_steps: Annotated[int, Field(ge=1, le=200)] | None = None
    step_timeout_s: Annotated[float, Field(gt=0, le=3600)] | None = None
    # Human in the loop: ask before destructive clicks (names suggesting delete, pay, transfer...),
    # and when a step's recovery is exhausted; an unanswered question fails the step after the timeout.
    confirm_destructive: bool = False
    destructive_terms: list[DESTRUCTIVE_TERM] | None = Field(default=None, max_length=50)
    ask_on_failure: bool = False
    approval_timeout_s: Annotated[float, Field(gt=0, le=86_400)] | None = None
    # Quality audits of every page the run reaches: accessibility (a11y) and visual baselines plus
    # layout checks (visual). Findings go to the result's quality section; they fail the run only
    # when fail_on is set and a finding is at or above that impact.
    a11y: bool = False
    visual: bool = False
    fail_on: Impact | None = None
    # The plan phase: ``ask`` waits in needs_plan_approval for the plan's approval before the browser starts (default: ask
    # for a drafted case, else plan.review from the settings, auto); ``strict`` stops a run whose plan has lint errors and
    # fails steps whose inferred criteria stay unmet.
    plan_review: PlanReview | None = None
    strict: bool = False

    @model_validator(mode="after")
    def _fail_on_needs_an_audit(self) -> RunSettings:
        if self.fail_on is not None and not (self.a11y or self.visual):
            raise ValueError("fail_on needs a11y or visual: it gates the findings of those audits")
        return self

    def run_options(self) -> dict[str, Any]:
        """Flags passed to the worker (``environment`` and ``base_url`` are applied to the case instead)."""
        return self.model_dump(mode="json", include=_WORKER_OPTIONS)


SETTING_NAMES = frozenset(RunSettings.model_fields)
_WORKER_OPTIONS = set(SETTING_NAMES - {"environment", "base_url"})


class RunCreate(RunSettings):
    """Start a run. Give exactly one of ``test_case``, ``gherkin``, ``case_path`` or ``description``.

    ``description`` (plain language, one step per line) becomes a draft that waits in
    ``needs_approval`` until approved. ``decider`` (``stub``) is a test double: no model is used and no step is read.
    """

    test_case: TestCase | None = None
    gherkin: Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT)] | None = None
    test_data: dict[DataKey, TestDataValue] | None = Field(default=None, description="Test data of the Gherkin (a saved .feature keeps it in <name>.data.json)")
    case_path: Annotated[str, StringConstraints(min_length=1, max_length=1000)] | None = None
    description: Annotated[str, StringConstraints(min_length=1, max_length=20_000)] | None = None
    auto_approve: bool = False
    plan: Plan | None = Field(default=None, description="A plan to run (from POST /api/plans or hercules_plan, possibly edited); it must be a plan of this case")

    @model_validator(mode="after")
    def _one_source(self) -> RunCreate:
        given = [name for name in ("test_case", "gherkin", "case_path", "description") if getattr(self, name) is not None]
        if len(given) != 1:
            raise ValueError(f"give exactly one of test_case, gherkin, case_path or description (got {', '.join(given) or 'none'})")
        if self.test_data is not None and self.gherkin is None:
            raise ValueError("test_data goes with gherkin; a TestCase carries its own test_data")
        return self


class PlanCreate(_Body):
    """Make a case's plan without running it: give exactly one of ``test_case``, ``gherkin`` or ``case_path``."""

    test_case: TestCase | None = None
    gherkin: Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT)] | None = None
    test_data: dict[DataKey, TestDataValue] | None = Field(default=None, description="Test data of the Gherkin (a saved .feature keeps it in <name>.data.json)")
    case_path: Annotated[str, StringConstraints(min_length=1, max_length=1000)] | None = None
    environment: EnvName | None = None
    base_url: BaseUrl | None = None
    mode: RunMode = "auto"
    egress: Egress | None = None
    decider: TestDecider | None = None
    strict: bool = False
    approve: bool = Field(default=False, description="Store the plan as approved for this case version and model (runs with plan_review ask reuse it)")

    @model_validator(mode="after")
    def _one_source(self) -> PlanCreate:
        given = [name for name in ("test_case", "gherkin", "case_path") if getattr(self, name) is not None]
        if len(given) != 1:
            raise ValueError(f"give exactly one of test_case, gherkin or case_path (got {', '.join(given) or 'none'})")
        if self.test_data is not None and self.gherkin is None:
            raise ValueError("test_data goes with gherkin; a TestCase carries its own test_data")
        return self


class PlanView(BaseModel):
    """A plan and its lint, as made (``POST /api/plans``) or as a run used it (``GET /api/runs/{id}/plan``)."""

    plan: Plan
    lint_errors: int = 0
    lint_warnings: int = 0
    run_id: str | None = None
    approved: bool = False
    summary: str = ""


class ControlRequest(_Body):
    """Debug a running run: ``pause`` before the next step or action, ``resume``, ``step`` (run one step,
    then pause), and while paused ``skip`` the next step or ``retry`` the previous one."""

    action: Literal["pause", "resume", "step", "skip", "retry"]


class ApprovalRequest(_Body):
    """Answer a run in ``needs_approval``: ``approve``, ``reject``, or ``edit`` (replace the step's text and run it again).
    A run in ``needs_plan_approval``: ``approve`` or ``reject`` its plan, or ``edit`` with ``plan`` (the edited plan).
    ``approval_id`` (the ``id`` of the run's ``approval``) binds the answer to that question: when it no longer waits (answered,
    timed out, or replaced by a later one) the answer is refused with 409 instead of answering the next question."""

    decision: Literal["approve", "reject", "edit"] = "approve"
    approval_id: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    step_text: Annotated[str, StringConstraints(min_length=1, max_length=2000)] | None = None
    plan: Plan | None = None

    @model_validator(mode="after")
    def _text_for_edit(self) -> ApprovalRequest:
        if self.decision == "edit" and not (self.step_text or "").strip() and self.plan is None:
            raise ValueError("edit needs step_text (the step's new text) or plan (the edited plan)")
        return self


class InputRequest(_Body):
    """Drive a paused run's browser (take over): coordinates are fractions (0-1) of the live view."""

    kind: Literal["click", "type", "key", "scroll"]
    x: Annotated[float, Field(ge=0, le=1)] | None = None
    y: Annotated[float, Field(ge=0, le=1)] | None = None
    text: Annotated[str, StringConstraints(min_length=1, max_length=1000)] | None = None
    key: Literal["Enter", "Tab", "Backspace", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"] | None = None
    dy: Annotated[int, Field(ge=-5000, le=5000)] | None = None

    @model_validator(mode="after")
    def _fields_for_kind(self) -> InputRequest:
        needs = {"click": ("x", "y"), "scroll": ("x", "y", "dy"), "type": ("text",), "key": ("key",)}[self.kind]
        missing = [name for name in needs if getattr(self, name) is None]
        if missing:
            raise ValueError(f"{self.kind} needs {', '.join(missing)}")
        return self


class ApprovalView(BaseModel):
    """The question a running run waits on."""

    id: str | None = None
    kind: str | None = None
    step: int | None = None
    reason: str | None = None
    action: str | None = None
    target: str | None = None
    screenshot: str | None = None
    timeout_s: float | None = None
    allow_edit: bool = True
    asked_at: str | None = None


class PauseView(BaseModel):
    step: int | None = None
    where: str | None = None
    action: str | None = None
    since: str | None = None


class RerunRequest(_Body):
    """``whole`` re-runs the case; ``failed_only`` re-runs it only if the run did not pass.

    Any option of ``POST /api/runs`` may be given to change it for the new run (``null`` clears a nullable
    one); the options not given are inherited from the run. The result is validated like ``POST /api/runs``.
    """

    scope: Literal["whole", "failed_only"] = "whole"
    environment: EnvName | None = None
    base_url: BaseUrl | None = None
    mode: RunMode | None = None
    egress: Egress | None = None
    decider: TestDecider | None = None
    headed: bool | None = None
    max_steps: Annotated[int, Field(ge=1, le=200)] | None = None
    step_timeout_s: Annotated[float, Field(gt=0, le=3600)] | None = None
    confirm_destructive: bool | None = None
    destructive_terms: list[DESTRUCTIVE_TERM] | None = Field(default=None, max_length=50)
    ask_on_failure: bool | None = None
    approval_timeout_s: Annotated[float, Field(gt=0, le=86_400)] | None = None
    a11y: bool | None = None
    visual: bool | None = None
    fail_on: Impact | None = None
    plan_review: PlanReview | None = None
    strict: bool | None = None

    def overrides(self) -> dict[str, Any]:
        """The options given in the request (``null`` included), to apply over the parent run's."""
        return {name: getattr(self, name) for name in self.model_fields_set if name in SETTING_NAMES}

    def settings(self, inherited: dict[str, Any]) -> RunSettings:
        """The new run's options: the inherited ones with the given ones applied, validated like ``POST /api/runs``.

        An inherited ``fail_on`` is dropped when the request turns both audits off (it would gate nothing).
        """
        given = self.overrides()
        merged = {name: value for name, value in inherited.items() if name in SETTING_NAMES} | given
        if merged.get("strict") is None:
            merged.pop("strict", None)
        if "fail_on" not in given and not (merged.get("a11y") or merged.get("visual")):
            merged.pop("fail_on", None)
        return RunSettings.model_validate(merged)


class CaseSave(_Body):
    """Save a case under ``.hercules/cases``: a TestCase (as ``<id>.json``) or Gherkin (as ``<scenario>.feature``)."""

    test_case: TestCase | None = None
    gherkin: Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT)] | None = None
    test_data: dict[DataKey, TestDataValue] | None = Field(default=None, description="Test data of the Gherkin (a saved .feature keeps it in <name>.data.json)")
    overwrite: bool = False
    folder: FolderPath = Field(default="", description="The folder to save into, such as Checkout/Payments (empty: the top level); it must exist")

    @model_validator(mode="after")
    def _one_source(self) -> CaseSave:
        if (self.test_case is None) == (self.gherkin is None):
            raise ValueError("give exactly one of test_case or gherkin")
        if self.test_data is not None and self.gherkin is None:
            raise ValueError("test_data goes with gherkin; a TestCase carries its own test_data")
        return self


class CaseValidate(_Body):
    """Check Gherkin or a TestCase without saving it; the answer carries the compiled TestCase or the problem."""

    test_case: dict[str, Any] | None = None
    gherkin: Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT)] | None = None
    test_data: dict[DataKey, TestDataValue] | None = Field(default=None, description="Test data of the Gherkin (a saved .feature keeps it in <name>.data.json)")

    @model_validator(mode="after")
    def _one_source(self) -> CaseValidate:
        if (self.test_case is None) == (self.gherkin is None):
            raise ValueError("give exactly one of test_case or gherkin")
        if self.test_data is not None and self.gherkin is None:
            raise ValueError("test_data goes with gherkin; a TestCase carries its own test_data")
        return self


class FolderCreate(_Body):
    """Make a folder for cases: ``name`` inside ``parent`` (empty: the top level)."""

    parent: FolderPath = ""
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class FolderUpdate(_Body):
    """Rename a folder (``name``) or move it into another folder (``parent``, empty: the top level); give exactly one."""

    name: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    parent: FolderPath | None = None

    @model_validator(mode="after")
    def _one_change(self) -> FolderUpdate:
        if (self.name is None) == (self.parent is None):
            raise ValueError("give exactly one of name or parent")
        return self


class CasesMove(_Body):
    """Move saved cases (by name, such as Checkout/login.feature) into ``folder`` (empty: the top level)."""

    names: list[Annotated[str, StringConstraints(min_length=1, max_length=1000)]] = Field(min_length=1, max_length=500)
    folder: FolderPath = ""


class RunBatchCreate(RunSettings):
    """Run every ready case of a folder: one queued run per case, sharing a batch. Drafts and cases that do not load are
    skipped. ``environment`` and ``base_url``, when given, apply to every case; else each runs in its own."""

    folder: FolderPath = Field(description="The folder, such as Checkout (empty: every saved case)")
    subfolders: bool = True


class CaseCheck(BaseModel):
    valid: bool
    error: str | None = None
    test_case: dict[str, Any] | None = None


class OptionView(BaseModel):
    id: str
    label: str | None = None
    probability: float | None = None


class QuestionView(BaseModel):
    id: str
    type: str
    instructions: str | None = None
    options: list[OptionView] = Field(default_factory=list)
    answer: str | None = None
    confidence: float | None = None


class DecisionView(BaseModel):
    """One decision a step asked for, as recorded in the step's ``decisions.jsonl``."""

    purpose: str | None = None
    attempt: int = 0
    engine: str | None = None
    model: str | None = None
    latency_ms: float | None = None
    wall_ms: float | None = None
    cost_usd: float | None = None
    cached: bool = False
    choice: str | None = None
    confidence: float | None = None
    rows: int | None = None
    prompt_tokens_est: int | None = None
    fallback: str | None = None
    answered_by: str | None = None
    calls: list[dict[str, Any]] = Field(default_factory=list)
    page: dict[str, Any] | None = None
    step: dict[str, Any] | None = None
    questions: list[QuestionView] = Field(default_factory=list)


class ExportEnvVar(BaseModel):
    name: str
    required: bool
    purpose: str


class ExportView(BaseModel):
    """A passed run exported as code (or its automation spec): the text, where to save it, what it needs, what to review."""

    format: Literal["spec", "playwright-ts", "playwright-py", "appium-py"]
    run_id: str
    case_id: str
    run_status: str
    filename: str = Field(description="Suggested path for the file, relative to the project")
    code: str = Field(description="The test file (or, for format spec, the automation spec as JSON)")
    spec: dict[str, Any] = Field(description="The exported part of the run's automation spec")
    env: list[ExportEnvVar] = Field(default_factory=list, description="Environment variables the code reads")
    notes: list[str] = Field(default_factory=list, description="What to review: steps or checks that could not be exported faithfully")
    steps_exported: int = 0
    steps_total: int = 0
    partial: bool = Field(default=False, description="Exported from a run that did not pass, up to its last passed step")
    guidance: str = ""


class StepDecisions(BaseModel):
    run_id: str
    step: int
    decisions: list[DecisionView]


class Environment(_Body):
    """A named environment: where cases run. Secrets are referenced by environment-variable name only.

    A web environment has a base URL, and may emulate a phone or tablet (``device``); a native app environment
    names the app and device (``mobile``) and needs a base URL only for its API steps.
    """

    name: EnvName
    base_url: BaseUrl | None = None
    viewport: Annotated[str, StringConstraints(pattern=VIEWPORT_PATTERN)] | None = None
    browser: Browser | None = Field(default=None, description="chromium (default), chrome, msedge, firefox or webkit")
    browser_options: BrowserOptions | None = Field(default=None, description="Playwright launch and context options (see the case schema's environment.browser_options)")
    device: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = Field(default=None, description="Mobile web emulation: a Playwright device profile")
    mobile: MobileApp | None = Field(default=None, description="A native app on a device (Appium)")
    secret_names: list[Annotated[str, StringConstraints(pattern=SECRET_NAME_PATTERN)]] = Field(default_factory=list, max_length=50)
    variables: dict[DataKey, TestDataValue] = Field(
        default_factory=dict, max_length=100, description="Values and secrets every case run with this environment can use as {{name}}; a case's own test_data wins"
    )

    _known_device = field_validator("device")(EnvironmentRef._known_device.__func__)  # type: ignore[attr-defined]

    @model_validator(mode="after")
    def _where(self) -> Environment:
        if self.mobile is None and self.base_url is None:
            raise ValueError("a web environment needs base_url (a native app environment sets mobile instead)")
        check_web_settings(self.mobile, self.device, self.viewport, self.browser, self.browser_options)
        reserved = sorted(BUILTIN_PLACEHOLDERS.intersection(self.variables))
        if reserved:
            raise ValueError(f"variable(s) {', '.join(reserved)} are reserved for built-in placeholders")
        unknown = sorted(set(placeholder_names_in(self.base_url or "")) - set(self.data()) - BUILTIN_PLACEHOLDERS)
        if unknown:
            raise ValueError(f"base_url uses {', '.join('{{' + name + '}}' for name in unknown)}, which this environment does not define: add it as a variable or secret")
        return self

    def data(self) -> dict[str, TestDataValue]:
        found: dict[str, TestDataValue] = {name: SecretRef(secret=name) for name in self.secret_names}
        found.update(self.variables)
        return found


class EnvironmentSave(Environment):
    overwrite: bool = False


class RunView(BaseModel):
    """A run's live status; ``result`` is the RunResult once the run has finished and the worker wrote one."""

    run_id: str
    status: RunStatus
    finished: bool
    case_id: str | None = None
    title: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    total_steps: int | None = None
    steps_done: int = 0
    current_step: int | None = None
    current_action: str | None = None
    reason: str | None = None
    parent_run_id: str | None = None
    started_by: Literal["daemon", "agent"] | None = Field(default=None, description="daemon: the tester UI or HTTP API; agent: a coding agent over MCP")
    agent: str | None = Field(default=None, description="With started_by agent: claude-code, codex, cursor, copilot, gemini or other")
    batch: str | None = Field(default=None, description="The batch of a folder run (POST /api/runs/batch) this run belongs to")
    folder: str | None = Field(default=None, description="With batch: the folder that was run")
    worker_pid: int | None = None
    last_seq: int = 0
    mode: str | None = None
    egress: str | None = None
    settings: RunSettings | None = Field(default=None, description="The options the run was started with (a re-run inherits them)")
    run_dir: str
    ui_url: str
    summary: str
    draft_steps: list[str] | None = None
    approval: ApprovalView | None = None
    paused: PauseView | None = None
    resumable: bool = False
    result: RunResult | None = None


class RunList(BaseModel):
    runs: list[RunView]


class RunCreated(BaseModel):
    run_id: str
    status: RunStatus
    case_id: str | None = None
    ui_url: str


class BatchSkip(BaseModel):
    name: str
    reason: str


class RunBatchCreated(BaseModel):
    batch: str
    folder: str
    runs: list[RunCreated]
    skipped: list[BatchSkip]


class ErrorBody(BaseModel):
    error: str
    detail: Any = None
