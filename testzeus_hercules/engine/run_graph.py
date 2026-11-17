"""The run graph: prepare (browser, evidence dir) → steps in order → finalize (RunResult).

Steps run sequentially and the run stops at the first step that does not pass;
the remaining steps are reported as ``skipped``. Steps run in the written order, except
inside a block of field entries whose case reading says a field depends on a field a later
step sets (see :mod:`testzeus_hercules.engine.ordering`), and when a step's field turns out to
depend on a later field step at run time (the step graph's ``run_first``): that step then
runs first and the step runs again. A step keeps its number; its result says when it ran
(``sequence``) and why (``notes``), and the reorder is recorded in its ``plan.jsonl``. Verdicts are strict: the run
passes only if every step passed. A case whose steps all carry an ``api``/``sql``/``security``
payload (or that is tagged ``kind:api``/``kind:sql``) runs without a browser; any
other case opens one before its first step. ``run.json`` and per-step evidence are written
under ``<project>/.hercules/runs/<run_id>/``, with ``junit.xml`` (the steps as JUnit XML),
and, for browser runs, ``console.jsonl`` and ``network.jsonl`` (the page's console messages
and network traffic, redacted); ``run.json``'s ``evidence`` names them.

Before step 1 the run makes its plan (see :mod:`testzeus_hercules.engine.plan_run`): a
model reads the whole case (see
:meth:`~testzeus_hercules.engine.intent.LLMInterpreter.read_case`; cached on disk), the
decider audits the plan's coverage and lint checks it; with ``RunPlanOptions.review`` ``ask``
the plan is approved, edited or rejected before the browser starts, and in strict mode
lint errors end the run there. The steps then run the plan's reading, its criteria checked
after each action, and ``plan.json`` records the results and every amendment. Then
the case's ``preconditions`` are judged by a model on the page as the run starts (the
case's base URL is opened first when the browser is still blank): one that clearly does
not hold ends the run with ``error`` and names it; one the model cannot confirm either way
is noted in the hints. A run that does not pass gets a ``failure`` classification from the
decider (``unclassified`` when it is not sure) and a factual summary.

With a :class:`~testzeus_hercules.engine.control.RunControl` the run can be paused,
stepped, skipped and retried at step boundaries, and stops for a person's approval
(destructive clicks, exhausted recovery, drafted cases); a step the tester edited
runs again with the new text. With a checkpointer the state is saved after every
step (with the page's URL, cookies and local storage), and ``run_case(...,
resume=True)`` continues a run whose worker died from the last completed step.

With ``RunDeps.quality`` (:class:`~testzeus_hercules.engine.quality.RunQualityOptions`:
``a11y``, ``visual``, ``fail_on``) every page state a browser step leaves behind is
audited once (see :mod:`testzeus_hercules.engine.quality`); the audits and the quality
steps' results are reported in ``RunResult.quality``. The audits fail the run only when
``fail_on`` is set and a finding reaches it.

A case whose environment names a phone or tablet (``environment.device``, a Playwright device profile) opens
the browser with that device's viewport, user agent, scale and touch. A case whose environment names a
native app (``environment.mobile``) opens no browser: the run starts a device session (see
:mod:`testzeus_hercules.mobile`) and its steps run on the device; preconditions are judged on the device's
screen, and the session ends (a device cloud is told the result) when the run ends.

Security steps' scans and findings are reported
in ``RunResult.security`` (a failing finding fails its own step).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncContextManager, AsyncIterator, Awaitable, Callable, Literal, Mapping, Protocol, TypedDict, TypeVar

from langgraph.constants import END, START
from langgraph.graph.state import StateGraph

from testzeus_hercules.act.browser import BrowserActions
from testzeus_hercules.cases.devices import context_options, viewport_of
from testzeus_hercules.cases.gherkin import VERIFY_ACTION
from testzeus_hercules.cases.models import AssertionCounts, CostInfo, EngineInfo, EvidencePaths, FailureInfo, QualityPage, RunMode, RunResult, RunStatus, Step, StepResult, TestCase
from testzeus_hercules.cases.automation import AutomationCheck, AutomationSpec, AutomationStep
from testzeus_hercules.decide.engine import DecisionEngine
from testzeus_hercules.engine.automation import build_spec
from testzeus_hercules.cases.browser_options import BrowserOptions
from testzeus_hercules.cases.step_files import step_file_roots
from testzeus_hercules.engine.browser_launch import VIDEO_DIRNAME, BrowserSetupError, browser_launch
from testzeus_hercules.engine.browser_process import forget_browser, record_browser
from testzeus_hercules.engine.control import RetryStep, RunControl, SkipStep, StepBlocked
from testzeus_hercules.engine.data import CaseData, MissingSecretError, protect_case
from testzeus_hercules.engine.evidence import EvidenceStore
from testzeus_hercules.engine.gate import threshold_for
from testzeus_hercules.engine.failure import classify_failure, failure_summary
from testzeus_hercules.cases.plan import Amendment, CriterionResult, Plan
from testzeus_hercules.cases.security import SecurityScanResult, SecuritySection
from testzeus_hercules.engine.intent import CaseReading, Interpreter
from testzeus_hercules.engine.plan import PlanError, now_iso
from testzeus_hercules.engine.plan_run import PlanRun
from testzeus_hercules.engine.planning import PlanOptions
from testzeus_hercules.kernel.child_env import BROWSER_KEEP, child_environment
from testzeus_hercules.engine.judge import PRECONDITION_PURPOSE, judge
from testzeus_hercules.engine.junit import JUNIT_FILE, write_junit
from testzeus_hercules.engine.ordering import entry_blocks, plan_order
from testzeus_hercules.engine.masking import MaskingDecider, masked_json
from testzeus_hercules.engine.page_log import PageLog
from testzeus_hercules.engine.page_state import capture_page_state, restore_page_state
from testzeus_hercules.engine.ports import DefaultPerceiver, Perceiver
from testzeus_hercules.engine.quality import QualitySession, RunQualityOptions, baseline_dir
from testzeus_hercules.engine.quality_intent import QualityInterpreter
from testzeus_hercules.engine.service import ServiceSession
from testzeus_hercules.engine.service_intent import ServiceInterpreter, case_hint
from testzeus_hercules.engine.step_graph import StepDeps, StepLimits, StepOutcome, StepRunner
from testzeus_hercules.engine.verify import LedgerEntry
from testzeus_hercules.kernel.run_context import CancelToken, RunCancelledError, RunContext
from testzeus_hercules.mobile.session import MobileSession, MobileSetupError

T = TypeVar("T")
logger = logging.getLogger(__name__)
CHROMIUM_ENV = "HERCULES_CHROMIUM"
DEFAULT_VIEWPORT = (1280, 800)
_VIEWPORT = re.compile(r"^(\d+)x(\d+)$")
_CANCEL_POLL_S = 0.1
# Step results kept in the run state: enough for pauses, retries and edits without an unbounded graph.
_RECURSION_PER_STEP = 8
_RECURSION_EXTRA = 50
_MAX_HUMAN_RETRIES = 20
# Time a security step gets beyond its scan's own limit, to stop the scanner and write the evidence.
_SCAN_MARGIN_S = 30
# The error class of a step whose page could not be read (see engine.ports.PerceptionFailed).
_UNREADABLE = "PerceptionFailed"
# The error class of a step whose value is longer than its field accepts: the test's data, judged without a model.
_TOO_LONG = "ValueTooLong"


class ResumeError(RuntimeError):
    """There is nothing to resume: no checkpoint for the run, or it already finished."""


class PageLauncher(Protocol):
    """Opens a browser page for one run and closes everything on exit (``device``: a Playwright device profile, passed
    only when the case names one)."""

    def open(self, *, viewport: tuple[int, int], headless: bool) -> AsyncContextManager[Any]: ...


class PlaywrightLauncher:
    """Launches the case's browser (Chromium by default) with Playwright and its ``browser_options``.

    ``executable_path`` (``HERCULES_CHROMIUM``) replaces Playwright's own Chromium; ``env`` is where secrets named in the
    options are read (the run's environment), at launch only.
    """

    def __init__(self, executable_path: str | None = None, env: Mapping[str, str] | None = None) -> None:
        self.executable_path = executable_path
        self.env: Mapping[str, str] = env if env is not None else os.environ

    @contextlib.asynccontextmanager
    async def open(
        self,
        *,
        viewport: tuple[int, int],
        headless: bool,
        device: str | None = None,
        browser: str | None = None,
        options: BrowserOptions | None = None,
        project_dir: Path | None = None,
        video_dir: Path | None = None,
    ) -> AsyncIterator[Any]:
        from playwright.async_api import async_playwright

        setup = browser_launch(browser, options, env=self.env, headless=headless, project_dir=project_dir, video_dir=video_dir, executable_path=self.executable_path)
        context_kwargs: dict[str, Any] = context_options(device) if device else {}
        context_kwargs["viewport"] = {"width": viewport[0], "height": viewport[1]}
        context_kwargs.update(setup.context)
        async with async_playwright() as playwright:
            # The browser gets no keys or tokens: a page it renders must not be able to read them.
            env = child_environment(os.environ, keep=BROWSER_KEEP, prefixes=("PLAYWRIGHT_",))
            launched = await getattr(playwright, setup.engine).launch(env=env, **setup.launch)
            try:
                context = await launched.new_context(**context_kwargs)
                touch = _Touch(context) if context_kwargs.get("has_touch") and setup.engine == "chromium" else None
                if touch is not None:
                    context.on("page", touch.schedule)  # pages the app opens later (popups, new tabs)
                page = await context.new_page()
                if touch is not None:
                    await touch.ready(page)
                yield page
                await context.close()  # writes a recorded video before the browser goes
            finally:
                await launched.close()


ChromiumLauncher = PlaywrightLauncher  # the earlier name


class _Touch:
    """Turns touch on for every page of a touch device's context, once per page.

    Chromium sometimes opens such a context with no touch at all (``maxTouchPoints`` 0, no coarse pointer),
    which a page that adapts to touch then sees as a desktop; setting it on the page itself always holds.
    ``maxTouchPoints`` is 1, as Playwright's device profiles report. The session stays attached: Chromium drops
    an emulation when the session that set it detaches.
    """

    def __init__(self, context: Any) -> None:
        self.context = context
        self.tasks: dict[int, asyncio.Task[None]] = {}  # by page; kept so a task is not collected while it runs

    def schedule(self, page: Any) -> None:
        if id(page) not in self.tasks:
            self.tasks[id(page)] = asyncio.ensure_future(self._enable(page))

    async def ready(self, page: Any) -> None:
        """Wait until touch is on for ``page`` (turned on now when its page event has not come yet)."""
        self.schedule(page)
        await self.tasks[id(page)]

    async def _enable(self, page: Any) -> None:
        try:
            session = await self.context.new_cdp_session(page)
            await session.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
        except Exception as exc:  # a page closed before this ran; the run goes on with Playwright's own emulation
            logger.debug("touch emulation not set on a page (%s: %s)", type(exc).__name__, exc)


def _install_hint(browser: str | None) -> str:
    if browser in ("chrome", "msedge"):
        return f"Install {'Google Chrome' if browser == 'chrome' else 'Microsoft Edge'}, or run `python -m playwright install {browser}`."
    if browser in ("firefox", "webkit"):
        return f"Run `python -m playwright install {browser}`."
    return f"Set {CHROMIUM_ENV} to a Chromium executable or run `python -m playwright install chromium`."


def parse_viewport(value: str | None, device: str | None = None) -> tuple[int, int]:
    """``WIDTHxHEIGHT``, else the device profile's viewport, else the default."""
    found = _VIEWPORT.match(value or "")
    if found:
        return int(found.group(1)), int(found.group(2))
    return (viewport_of(device) if device else None) or DEFAULT_VIEWPORT


@dataclass(frozen=True)
class RunPlanOptions:
    """How a run treats its plan (see :mod:`testzeus_hercules.engine.planning`).

    ``review``: ``auto`` runs the plan as made; ``ask`` waits for a person (or an agent) to approve, edit or reject it
    before the browser starts, unless an approved plan of this case version and model exists; ``errors`` (the default)
    asks only when the plan has lint errors, and runs a clean plan as made. ``strict``: lint errors
    stop the run before the browser starts, and an inferred criterion still unmet after recovery makes its step
    unverified. ``given``: a plan to run (``--plan FILE``, or an agent's edited plan), checked against the case.
    """

    review: Literal["auto", "ask", "errors"] = "errors"
    strict: bool = False
    coverage: bool = True
    given: Plan | None = None
    given_by: Literal["user", "agent", "file"] = "file"
    confirm_destructive: bool = False


@dataclass
class RunDeps:
    """Engines and seams for one run; defaults are the offline walking skeleton."""

    decider: DecisionEngine
    perceiver: Perceiver = field(default_factory=DefaultPerceiver)
    # Reads the steps (a model); None only when every step carries a structured payload.
    interpreter: Interpreter | None = None
    launcher: PageLauncher | None = None
    actions: Any = field(default_factory=BrowserActions)
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    headless: bool = True
    images: bool = False
    limits: StepLimits = field(default_factory=StepLimits)
    llm: str | None = None
    max_steps: int | None = None
    mode: RunMode | None = None
    decider_model: str | None = None
    # Called once at the end: the run's cost and extra engine counters (jev_calls, fallbacks).
    usage: Callable[[], tuple[CostInfo, Mapping[str, int]]] | None = None
    # Human in the loop (pause, step, approvals); None runs straight through.
    control: RunControl | None = None
    # LangGraph checkpoint saver (see engine.checkpoint); None keeps no checkpoints.
    checkpointer: Any = None
    # Called with the page once it is open (a live view attaches its screencast here).
    on_page: Callable[[Any], Awaitable[None]] | None = None
    # httpx transport for API steps (tests); None sends real requests.
    http_transport: Any = None
    # The run's own accessibility / visual audits of every page it reaches (off by default).
    quality: RunQualityOptions = field(default_factory=RunQualityOptions)
    # The plan phase: review (auto or ask), strict lint, the coverage audit, and a plan given by a caller (an edited plan).
    plan: RunPlanOptions = field(default_factory=lambda: RunPlanOptions())
    # Native app runs: makes the HTTP clients that talk to the Appium server or device cloud (tests); None: real ones.
    mobile_client_factory: Any = None

    def resolve_launcher(self) -> PageLauncher:
        return self.launcher or PlaywrightLauncher(self.env.get(CHROMIUM_ENV) or None, env=self.env)


class RunState(TypedDict, total=False):
    next_index: int
    results: tuple[StepResult, ...]
    ledger: tuple[LedgerEntry, ...]
    stopped: bool
    cancelled: bool
    setup_error: str | None
    # Step texts a person replaced (step number as text → new action).
    edits: dict[str, str]
    notes: tuple[str, ...]
    # The page after the last completed step, for resuming in a new browser.
    page: dict[str, Any] | None
    draft_approved: bool
    # Quality results so far (quality steps and the run's audits), the audited page states and URL visit counts.
    quality: tuple[QualityPage, ...]
    quality_seen: tuple[str, ...]
    quality_urls: dict[str, int]
    # Security scans of the security steps so far.
    security: tuple[SecurityScanResult, ...]
    # What each finished step did, for exporting the run as code (RunResult.automation).
    automation: tuple[AutomationStep, ...]
    # Steps that ended because the page could not be read (perception failed).
    unreadable: tuple[int, ...]
    # Steps whose value did not fit their field (longer than it accepts).
    test_data: tuple[int, ...]
    # The order steps run in (step numbers); ``next_index`` is the position in it. Empty: the written order.
    order: tuple[int, ...]
    # Steps that were run again after a later field step they depend on ran first (at most once each).
    deferred: tuple[int, ...]
    # Per step number (as text): notes about its order.
    step_notes: dict[str, list[str]]
    # The plan: each criterion's result so far and every amendment made while the run ran.
    plan_results: tuple[CriterionResult, ...]
    amendments: tuple[Amendment, ...]


def _resolved_base_url(template: str | None, data: CaseData) -> str | None:
    if not template:
        return None
    try:
        return data.resolve(template, captured=False)
    except MissingSecretError:
        return template


class _Run:
    """One run's resources and graph nodes; the browser lives in an exit stack closed by ``run_case``."""

    def __init__(self, case: TestCase, ctx: RunContext, deps: RunDeps, stack: contextlib.AsyncExitStack) -> None:
        # Credentials in the case's URLs become placeholders: models, events and evidence see the placeholder,
        # the browser gets the value (see engine.data.protect_case).
        case, hidden = protect_case(case)
        self.case, self.ctx, self.deps, self.stack = case, ctx, deps, stack
        self.data = CaseData.from_case(case, env=deps.env, run_id=ctx.run_id, hidden=hidden)
        self.base_url = _resolved_base_url(case.environment.base_url, self.data)
        # Every decision request leaves with the run's secrets and any URL credential masked.
        self.decider = MaskingDecider(deps.decider, self.data.redact)
        self.evidence = EvidenceStore(ctx.paths.run_dir(ctx.run_id), ctx.run_id, redact=self.data.redact)
        self.service = ServiceSession(
            data=self.data,
            evidence=self.evidence,
            project_dir=ctx.paths.project_dir,
            base_url=self.base_url,
            interpreter=ServiceInterpreter(llm=deps.interpreter.model if deps.interpreter is not None else None),
            hint=case_hint(case.tags),
            events=ctx.events,
            timeout_s=deps.limits.request_timeout_s,
            transport=deps.http_transport,
            env=deps.env,
            cache_dir=ctx.paths.cache_dir,
        )
        self.quality = QualitySession(
            evidence=self.evidence,
            decider=self.decider,
            baselines=baseline_dir(ctx.paths.project_hercules_dir),
            case_id=case.id,
            llm=deps.interpreter.model if deps.interpreter is not None else None,
            images=deps.images,
            interpreter=QualityInterpreter(llm=deps.interpreter.model if deps.interpreter is not None else None),
            options=deps.quality,
            files_changed=tuple(case.source.files_changed) if case.source is not None else (),
            project_dir=ctx.paths.project_dir,
            redact=self.data.redact,
            events=ctx.events,
        )
        self.runner: StepRunner | None = None
        self.reading = CaseReading()
        plan = deps.plan
        self.planner = PlanRun(
            case,
            run_dir=self.evidence.run_dir,
            plans_dir=ctx.paths.project_hercules_dir / "plans",
            # A native app's steps are read on the device, step by step: no plan of browser actions is made for them.
            interpreter=deps.interpreter if case.environment.mobile is None else None,
            decider=self.decider,
            env=deps.env,
            names=sorted(set(case.test_data) | set(self.data.hidden)),
            hidden=sorted(self.data.hidden),
            service=case_hint(case.tags) is not None,
            options=PlanOptions(coverage=plan.coverage, strict=plan.strict, confirm_destructive=plan.confirm_destructive),
            redact=self.data.redact,
            given=plan.given,
            given_by=plan.given_by,
            resolve=lambda text: self.data.resolve(text, captured=False),
        )
        self.page_log: PageLog | None = None
        self.page: Any = None
        self.mobile: MobileSession | None = None
        self.result: RunResult | None = None
        self.started = time.monotonic()
        # Making the plan does not count against the case's timeout (a local model may take minutes the first time).
        self.planning_s = 0.0
        self.resumed = False
        self.resumed_plan = False
        self.blocks: list[tuple[int, ...]] = []

    def _human_wait_s(self) -> float:
        control = self.deps.control
        return control.human_wait_s if control is not None else 0.0

    def graph(self) -> Any:
        graph = StateGraph(RunState)
        graph.add_node("prepare", self.prepare)
        graph.add_node("step", self.step)
        graph.add_node("finalize", self.finalize)
        graph.add_edge(START, "prepare")
        graph.add_conditional_edges("prepare", lambda state: "finalize" if state.get("setup_error") or state.get("cancelled") else "step", ["step", "finalize"])
        graph.add_conditional_edges("step", self._after_step, ["step", "finalize"])
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=self.deps.checkpointer) if self.deps.checkpointer is not None else graph.compile()

    def _after_step(self, state: RunState) -> str:
        done = state.get("stopped") or state.get("cancelled") or state["next_index"] >= len(self.case.steps) or self._step_cap_reached(state)
        return "finalize" if done else "step"

    def _step_cap_reached(self, state: RunState) -> bool:
        cap = self.deps.max_steps
        return cap is not None and state["next_index"] >= cap

    async def prepare(self, state: RunState) -> dict[str, Any]:
        ctx, case = self.ctx, self.case
        self.evidence.run_dir.mkdir(parents=True, exist_ok=True)
        ctx.telemetry.emit("run_started", step_count=len(case.steps))
        ctx.events.emit("run_started", run_id=ctx.run_id, case_id=case.id, steps=len(case.steps))
        self._build_runner()
        if case.environment.mobile is not None:
            problem = await self._read_case(state)
            if problem:
                return problem
            problem_text = await self._open_mobile()
            if problem_text:
                return {"setup_error": problem_text}
            return {**await self._check_mobile_preconditions(), **self._order()}
        if not self._needs_browser():
            problem = await self._read_case(state)
            if problem:
                return problem
            return {**self._unchecked_preconditions(), **self._order()}
        review = self.deps.plan.review
        if review == "ask" or self.deps.plan.strict or (review == "errors" and self.deps.control is not None):
            # The plan is reviewed (or checked strictly) before the browser starts; a plan with lint errors may need review.
            problem = await self._read_case(state)
            if problem:
                return problem
            problem_text = await self._open()
            if problem_text:
                return {"setup_error": problem_text}
            return {**await self._check_preconditions(), **self._order()}
        # The model reads the case while the browser starts: neither waits for the other.
        reading = asyncio.ensure_future(self._read_case(state))
        try:
            problem_text = await self._open()
            if problem_text:  # no browser: the run ends here, its plan is not worth waiting for
                await _stop_reading(reading)
                return {"setup_error": problem_text}
        except BaseException:
            await _stop_reading(reading)
            raise
        problem = await reading
        if problem:
            return problem
        return {**await self._check_preconditions(), **self._order()}

    async def _read_case(self, state: RunState | None = None) -> dict[str, Any] | None:
        """Make the run's plan before the run (the model reads every step: one call per part, or the cached reading; see
        engine.planning), review it when asked, and stop a strict run on lint errors. Steps the plan does not cover are read
        one by one. Returns a state update that ends the run (setup error or cancellation), or None."""
        planning = time.monotonic()
        try:
            work = self.planner.resume() if self.resumed_plan else await _cancellable(self.planner.prepare(), self.ctx.cancel)
        except PlanError as exc:
            return {"setup_error": self.data.redact(str(exc))}
        finally:
            self.planning_s += time.monotonic() - planning
        if work is None:
            return {"cancelled": True}
        self.reading = work
        self.ctx.events.emit(
            "plan_ready", steps=len(self.planner.plan.steps) if self.planner.plan else 0, origin=self.planner.origin, lint_errors=len(self.planner.plan.lint_errors) if self.planner.plan else 0
        )
        control = self.deps.control
        review = self.deps.plan.review
        # A strict run refuses a plan with lint errors, so the default review (``errors``) does not ask to approve one
        # first; an explicit ``ask`` still does, since a reviewer's edit can fix the errors.
        errors = self.planner.plan is not None and bool(self.planner.plan.lint_errors)
        ask = review == "ask" or (review == "errors" and errors and not self.deps.plan.strict)
        if ask and self.planner.needs_review and control is not None and not self.resumed_plan:
            try:
                go, reason = await self.planner.review(control)
            except RunCancelledError:
                return {"cancelled": True}
            if not go:
                return {"cancelled": True, "notes": tuple((state or {}).get("notes") or ()) + (reason or "the plan was not approved",)}
            self.reading = self.planner.reading()
        blocked = self.planner.blocked()
        if blocked:
            return {"setup_error": blocked}
        self.blocks = entry_blocks(self.case.steps, self.reading)
        if self.runner is not None:
            self.runner.deps.reading = self.reading
            self.runner.deps.end_criteria = self.planner.end_criteria()
            self.runner.deps.strict = self.deps.plan.strict
        return None

    def _order(self) -> dict[str, Any]:
        """The order the steps run in: the plan's (a reviewer's) order, else from the case reading's field dependencies
        (validated; see engine.ordering). A changed order is an amendment of the plan."""
        written = tuple(range(1, len(self.case.steps) + 1))
        given = self.planner.order()
        if given is not None:
            return {"order": given}
        try:
            planned = plan_order(self.case.steps, self.reading)
        except Exception as exc:  # ordering is an improvement; the written order always works
            logger.warning("the steps' order could not be planned (%s); they run as written", type(exc).__name__)
            return {}
        for number, record in planned.records.items():
            self.evidence.append_plan(number, dict(record))
        if not planned.reordered and not planned.notes:
            return {}
        update: dict[str, Any] = {"order": planned.order, "step_notes": {str(number): list(notes) for number, notes in planned.notes.items()}}
        if planned.reordered:
            moved = next((number for number, before in zip(planned.order, written) if number != before), planned.order[0])
            reason = "; ".join(note for notes in planned.notes.values() for note in notes) or "a field depends on a field a later step sets"
            amendment = Amendment(
                step=moved, kind="reorder", before=",".join(map(str, written)), after=",".join(map(str, planned.order)), reason=self.data.redact(reason)[:1900], source="model", at=now_iso()
            )
            update["amendments"] = (amendment,)
        return update

    def _unchecked_preconditions(self) -> dict[str, Any]:
        if not self.case.preconditions:
            return {}
        return {"notes": (f"preconditions not checked: this case opens no browser page ({len(self.case.preconditions)} listed)",)}

    async def _check_preconditions(self) -> dict[str, Any]:
        """Judge the case's preconditions on the page as the run starts: unmet ends the run with an error."""
        case, deps = self.case, self.deps
        if not case.preconditions:
            return {}
        assert self.runner is not None
        page, data = self.page, self.data
        if str(getattr(page, "url", "") or "about:blank") == "about:blank" and self.base_url:
            opened = await deps.actions.open_url(page, self.base_url, base_url=self.base_url, file_roots=(self.ctx.paths.project_dir,))
            if not opened.ok:
                return {"setup_error": f"preconditions could not be checked: the base URL did not open ({data.redact(opened.detail)})"}
        try:
            snapshot = await deps.perceiver.snapshot(page, next(self.runner.deps.seq))
            page_text = data.redact(await deps.actions.page_text(page))
            judgement = await judge(
                self.decider,
                [data.redact(text) for text in case.preconditions],
                purpose=PRECONDITION_PURPOSE,
                task="A test case is about to start. Judge, for each precondition of the case, whether the page as it is now shows that it holds.",
                url=snapshot.url,
                title=snapshot.title or "",
                page_text=page_text,
                screenshot=snapshot.plain_jpeg if deps.images else None,
            )
        except RunCancelledError:
            raise
        except Exception as exc:  # the judge is a model; when it cannot answer, the run says so and goes on
            return {"notes": (data.redact(f"preconditions not checked: {type(exc).__name__}: {_first_line(exc)}"),)}
        self.evidence.save_preconditions(
            snapshot,
            {
                "purpose": PRECONDITION_PURPOSE,
                "engine": judgement.decision.engine,
                "preconditions": list(case.preconditions),
                "probabilities": list(judgement.probabilities),
                "decision": judgement.decision.model_dump(mode="json"),
            },
        )
        unmet = [text for text, verdict in zip(case.preconditions, judgement.verdicts) if verdict == "failed"]
        unsure = [text for text, verdict in zip(case.preconditions, judgement.verdicts) if verdict == "unverified"]
        if unmet:
            return {"setup_error": data.redact(f"precondition not met: {unmet[0]!r} (judged by {judgement.decision.engine} on {snapshot.url})")}
        if unsure:
            return {"notes": tuple(data.redact(f"precondition not confirmed (the model was not sure): {text!r}") for text in unsure)}
        return {}

    def _needs_browser(self) -> bool:
        """True unless the case tests a native app or every step is an API or SQL step (by its payload, or by the case's kind tag)."""
        return self.case.environment.mobile is None and not all(self.service.handles(step) for step in self.case.steps)

    async def _open_mobile(self) -> str | None:
        """Start the device session of a native app case and give it to the step runner; returns a setup problem, or None."""
        from testzeus_hercules.mobile.target import TargetError, resolve_target

        app = self.case.environment.mobile
        assert app is not None
        settings = self.ctx.settings
        try:
            target = resolve_target(app, settings, self.deps.env, self.ctx.paths.project_dir)
        except TargetError as exc:
            return self.data.redact(str(exc))
        factory = {"client_factory": self.deps.mobile_client_factory} if self.deps.mobile_client_factory is not None else {}
        session = MobileSession(
            target,
            data=self.data,
            evidence=self.evidence,
            decider=self.decider,
            llm=self.deps.interpreter.model if self.deps.interpreter is not None else None,
            images=self.deps.images,
            events=self.ctx.events,
            max_actions=self.deps.limits.max_step_actions,
            assert_timeout_ms=self.deps.limits.assert_timeout_ms,
            command_timeout_s=settings.mobile.command_timeout_s,
            session_timeout_s=settings.mobile.session_timeout_s,
            run_name=f"{self.case.id} {self.ctx.run_id}",
            **factory,
        )
        # Ended when the run ends (also when it was cancelled or failed), after the result is known.
        self.stack.push_async_callback(self._close_mobile, session)
        try:
            await session.open()
        except MobileSetupError as exc:
            return str(exc)
        self.mobile = session
        assert self.runner is not None
        self.runner.deps.mobile = session
        return None

    async def _close_mobile(self, session: MobileSession) -> None:
        passed = self.result is not None and self.result.status == "passed"
        try:
            await session.close(passed)
        except Exception as exc:  # ending the session is cleanup; the result stands
            logger.warning("the device session did not end cleanly (%s)", type(exc).__name__)

    async def _check_mobile_preconditions(self) -> dict[str, Any]:
        """Judge the case's preconditions on the device's screen as the run starts: unmet ends the run with an error."""
        case, data, app = self.case, self.data, self.case.environment.mobile
        if not case.preconditions or self.mobile is None or app is None:
            return {}
        try:
            screen, jpeg = await self.mobile.screen_for_preconditions()
            judgement = await judge(
                self.decider,
                [data.redact(text) for text in case.preconditions],
                purpose=PRECONDITION_PURPOSE,
                task="A mobile app test case is about to start. Judge, for each precondition of the case, whether the device screen as it is now shows that it holds.",
                url=f"app://{app.app_id}",
                title="device screen",
                page_text=screen,
                screenshot=jpeg if self.deps.images else None,
            )
        except RunCancelledError:
            raise
        except Exception as exc:  # the judge is a model; when it cannot answer, the run says so and goes on
            return {"notes": (data.redact(f"preconditions not checked: {type(exc).__name__}: {_first_line(exc)}"),)}
        unmet = [text for text, verdict in zip(case.preconditions, judgement.verdicts) if verdict == "failed"]
        unsure = [text for text, verdict in zip(case.preconditions, judgement.verdicts) if verdict == "unverified"]
        if unmet:
            return {"setup_error": data.redact(f"precondition not met: {unmet[0]!r} (judged by {judgement.decision.engine} on the device screen)")}
        if unsure:
            return {"notes": tuple(data.redact(f"precondition not confirmed (the model was not sure): {text!r}") for text in unsure)}
        return {}

    async def _open(self) -> str | None:
        """Launch the browser and give its page to the step runner; returns a setup problem, or None."""
        case = self.case
        run_dir = self.evidence.run_dir
        self.stack.callback(forget_browser, run_dir)  # runs after the browser has closed
        try:
            environment = case.environment
            extra: dict[str, Any] = {"device": environment.device} if environment.device else {}
            if environment.browser not in (None, "chromium") or environment.browser_options is not None:
                extra.update(browser=environment.browser, options=environment.browser_options, project_dir=self.ctx.paths.project_dir, video_dir=run_dir / VIDEO_DIRNAME)
            viewport = parse_viewport(environment.viewport, environment.device)
            page = await self.stack.enter_async_context(self.deps.resolve_launcher().open(viewport=viewport, headless=self.deps.headless, **extra))
        except BrowserSetupError as exc:
            return f"the browser could not be set up: {exc}"
        except Exception as exc:  # report launch problems as a run error, not a crash
            return f"the browser could not start ({type(exc).__name__}): {_first_line(exc)}. {_install_hint(case.environment.browser)}"
        # So a supervisor can stop this browser if the worker dies (Playwright starts it in its own process group).
        await record_browser(page, run_dir)
        self.page = page
        if self.page_log is None:
            self.page_log = PageLog(self.evidence.run_dir, self.data.redact)
        self.page_log.attach(page)
        watch = getattr(self.deps.actions, "watch_page", None)
        if callable(watch):
            watch(page)  # navigations are tracked across steps; dialogs are answered and their messages kept
        assert self.runner is not None
        self.runner.deps.page = page
        if self.deps.on_page is not None:
            await self.deps.on_page(page)
        return None

    def _build_runner(self) -> None:
        """The step runner, without a page until a browser is opened."""
        self.runner = StepRunner(
            StepDeps(
                page=None,
                perceiver=self.deps.perceiver,
                decider=self.decider,
                interpreter=self.deps.interpreter,
                data=self.data,
                evidence=self.evidence,
                actions=self.deps.actions,
                base_url=self.base_url,
                file_roots=(self.ctx.paths.project_dir,),
                upload_roots=step_file_roots(self.ctx.paths.project_dir, self.case.environment.ref, self.case.id),
                threshold=threshold_for(_engine_name(self.deps.decider)),
                threshold_by_engine=threshold_for,
                images=self.deps.images,
                limits=self.deps.limits,
                events=self.ctx.events,
                control=self.deps.control,
                service=self.service,
                quality=self.quality,
                reading=self.reading,
                steps=tuple(step.expected if step.action.strip().lower() == VERIFY_ACTION and step.expected else step.action for step in self.case.steps),
            )
        )

    async def _reopen(self, state: RunState) -> str | None:
        """On resume: a new step runner and, when the case needs one, a browser back on the page where the last completed step left it."""
        self._build_runner()
        self.resumed_plan = True  # the plan the run started with (plan.json); never asked about again
        stopped = await self._read_case()
        if stopped and stopped.get("setup_error"):
            return str(stopped["setup_error"])
        if self.case.environment.mobile is not None:
            return await self._open_mobile()
        if not self._needs_browser():
            return None
        problem = await self._open()
        if problem:
            return problem
        trouble = await restore_page_state(self.page, state.get("page"))
        if trouble:
            self.ctx.events.emit("error", message=trouble)
        return None

    def _order_of(self, state: RunState) -> tuple[int, ...]:
        order = tuple(state.get("order") or ())
        return order if len(order) == len(self.case.steps) else tuple(range(1, len(self.case.steps) + 1))

    def _prepare_step(self, state: RunState, position: int) -> None:
        """Tell the step runner which steps ran so far and which field steps of this step's block are still to run."""
        assert self.runner is not None
        order = self._order_of(state)
        number = order[position]
        block = next((block for block in self.blocks if number in block), ())
        later = tuple((other, self._step_at(other - 1, state).action) for other in order[position + 1 :] if other in block)
        self.runner.deps.later = later
        self.runner.deps.executed = order[:position] if state.get("order") else None

    async def step(self, state: RunState) -> dict[str, Any]:
        ctx, position = self.ctx, state["next_index"]
        index = self._order_of(state)[position] - 1
        update: dict[str, Any] = {}
        if self.runner is None:  # resumed after the worker died: the browser is gone
            problem = await self._reopen(state)
            if problem:
                return {"setup_error": problem, "stopped": True}
            self.resumed = True
            ctx.events.emit("run_resumed", run_id=ctx.run_id, from_step=index + 1, steps=len(self.case.steps))
        if ctx.cancel.is_cancelled:
            return {"cancelled": True}
        control = self.deps.control
        if control is not None:
            try:
                if index == 0 and self.case.status == "draft" and not state.get("draft_approved"):
                    if not await control.approve_draft(len(self.case.steps)):
                        return {"cancelled": True, "notes": tuple(state.get("notes") or ()) + ("the drafted case was not approved",)}
                    update["draft_approved"] = True
                directive = await control.before_step(index + 1, self._step_at(index, state).action, can_retry=position > 0)
            except RunCancelledError:
                return {"cancelled": True}
            if directive.kind == "retry":
                return {**update, **_rewind(state, position, self._order_of(state))}
            if directive.kind == "skip":
                return {**update, **self._record(state, position, index, self._step_at(index, state), StepOutcome(status="skipped", actual="skipped by the tester"), stop=False)}
        step = self._step_at(index, state)
        number = index + 1
        if self.page is None and self.case.environment.mobile is None and not self.service.handles(step):
            problem = await self._open()  # a step a person edited into a browser step
            if problem:
                return {**update, "setup_error": problem, "stopped": True}
        sequence = {"sequence": position + 1} if state.get("order") else {}
        ctx.events.emit("step_started", step=number, action=step.action, **sequence)
        self._prepare_step(state, position)
        outcome, step = await self._run_step(number, step)
        cancelled = outcome is None
        if outcome is None:
            outcome = StepOutcome(status="skipped", actual="run cancelled")
        edited: tuple[Amendment, ...] = ()
        if step.action != self._step_at(index, state).action:
            before = self.data.redact(self._step_at(index, state).action)[:1900]
            edited = (
                Amendment(
                    step=number, kind="edit", before=before, after=self.data.redact(step.action)[:1900], reason="the tester edited the step's text and ran it again", source="user", at=now_iso()
                ),
            )
        if step.action != self.case.steps[index].action:
            update["edits"] = {**(state.get("edits") or {}), str(number): step.action}
        moved = self._run_first(state, position, number, outcome)
        if moved is not None:
            ctx.events.emit("step_deferred", step=number, after=outcome.run_first, duration_ms=outcome.duration_ms)
            return {**update, **moved, "amendments": tuple(moved["amendments"]) + edited}
        update.update(self._record(state, position, index, step, outcome, stop=outcome.status not in ("passed",) and not (outcome.status == "skipped" and not cancelled)))
        update["amendments"] = tuple(update["amendments"]) + edited
        update["cancelled"] = cancelled
        if not cancelled:
            update.update(await self._audit(state, number, step, outcome, update))
        if self.deps.checkpointer is not None:
            update["page"] = await capture_page_state(self.page)
        return update

    async def _audit(self, state: RunState, number: int, step: Step, outcome: StepOutcome, update: dict[str, Any]) -> dict[str, Any]:
        """The run's audits of the page a browser step left, when that page state was not audited yet."""
        if not self.deps.quality.enabled or self.page is None or self.service.handles(step) or outcome.status not in ("passed", "unverified"):
            return {}
        key = await self.quality.state_key(self.page)
        seen = tuple(state.get("quality_seen") or ())  # "<step>:<state key>"
        if key is None or any(item.split(":", 1)[1] == key for item in seen):
            return {}
        url = self.data.redact(str(self.page.url).split("#")[0])
        urls = {**(state.get("quality_urls") or {})}
        urls[url] = urls.get(url, 0) + 1
        pages = await _cancellable(self.quality.audit(number, self.page, url_seen=urls[url]), self.ctx.cancel)
        if pages is None:
            return {"cancelled": True}
        return {"quality": tuple(update.get("quality") or ()) + tuple(pages), "quality_seen": seen + (f"{number}:{key}",), "quality_urls": urls}

    def _step_at(self, index: int, state: RunState) -> Step:
        step = self.case.steps[index]
        edited = (state.get("edits") or {}).get(str(index + 1))
        return step.model_copy(update={"action": edited}) if edited else step

    def _run_first(self, state: RunState, position: int, number: int, outcome: StepOutcome) -> dict[str, Any] | None:
        """When the step's field depends on a later field step (``outcome.run_first``) that has not run: run that step now
        and this one again after it (once per step). None when the outcome stands."""
        first = outcome.run_first
        order = list(self._order_of(state))
        if first is None or number in (state.get("deferred") or ()) or first not in order[position + 1 :]:
            return None
        order.remove(first)
        order.insert(position, first)
        notes = {key: list(value) for key, value in (state.get("step_notes") or {}).items()}
        notes.setdefault(str(first), []).append(f"run before step {number}, whose field depends on it (found while step {number} ran)")
        notes.setdefault(str(number), []).extend(outcome.notes or (f"run again after step {first}, whose field it depends on",))
        self.evidence.append_plan(number, {"stage": "order", "run_first": first, "sequence": position + 2, "reason": self.data.redact(outcome.actual or "")})
        self.evidence.append_plan(first, {"stage": "order", "before": number, "sequence": position + 1})
        before = self._order_of(state)
        amendment = Amendment(
            step=number,
            kind="run_first",
            before=",".join(map(str, before)),
            after=",".join(map(str, order)),
            reason=self.data.redact(f"step {number}'s field depends on step {first}, so step {first} runs first: {outcome.actual or ''}")[:1900],
            source="model",
            at=now_iso(),
        )
        amendments = tuple(state.get("amendments") or ()) + outcome.amendments + (amendment,)
        return {"order": tuple(order), "deferred": tuple(state.get("deferred") or ()) + (number,), "step_notes": notes, "amendments": amendments}

    def _record(self, state: RunState, position: int, index: int, step: Step, outcome: StepOutcome, *, stop: bool) -> dict[str, Any]:
        ctx, number = self.ctx, index + 1
        notes = list((state.get("step_notes") or {}).get(str(number), ())) + [note for note in outcome.notes if note not in (state.get("step_notes") or {}).get(str(number), ())]
        result = StepResult(
            index=number,
            action=step.action,
            status=outcome.status,
            expected=step.expected,
            actual=outcome.actual,
            duration_ms=outcome.duration_ms,
            decisions=outcome.decisions,
            evidence=list(outcome.evidence),
            sequence=position + 1 if position + 1 != number else None,
            notes=notes,
        )
        ctx.events.emit("step_finished", step=number, status=outcome.status, duration_ms=outcome.duration_ms)
        if outcome.status not in ("passed", "skipped"):
            ctx.telemetry.emit("step_failed", error_class=outcome.error_class or "StepFailed", duration_ms=outcome.duration_ms)
        hints = tuple(f"step {number}: {note}" for note in outcome.notes if outcome.status == "passed")
        return {
            "next_index": position + 1,
            "notes": tuple(state.get("notes") or ()) + hints,
            "test_data": tuple(item for item in state.get("test_data") or () if item != number) + ((number,) if outcome.error_class == _TOO_LONG else ()),
            "results": tuple(state.get("results") or ()) + (result,),
            "ledger": tuple(state.get("ledger") or ()) + outcome.ledger,
            "stopped": stop,
            "quality": tuple(state.get("quality") or ()) + outcome.quality,
            "security": tuple(scan for scan in state.get("security") or () if scan.step != number) + ((outcome.security,) if outcome.security is not None else ()),
            "automation": tuple(state.get("automation") or ())
            + ((self._planned_automation(outcome.automation.model_copy(update={"index": number, "action": step.action}), outcome),) if outcome.automation is not None else ()),
            "unreadable": tuple(item for item in state.get("unreadable") or () if item != number) + ((number,) if outcome.error_class == _UNREADABLE else ()),
            "plan_results": tuple(state.get("plan_results") or ()) + outcome.criteria + self.planner.outcome_results(number, outcome.ledger, ran=outcome.status != "skipped"),
            "amendments": tuple(state.get("amendments") or ()) + outcome.amendments,
        }

    def _planned_automation(self, automation: AutomationStep, outcome: StepOutcome) -> AutomationStep:
        """The step's automation with the plan's criteria: author criteria that held become assertions of the export (the
        page facts a test can check), inferred ones become review notes (they are the model's expectations, not the test's)."""
        checks = list(automation.checks)
        notes = list(automation.notes)
        plan = self.planner.plan
        for result in outcome.criteria:
            criterion = plan.criterion(result.criterion) if plan is not None else None
            if criterion is None:
                continue
            if criterion.origin == "inferred":
                notes.append(f"plan (inferred, not asserted): {result.text} - {result.status}")
                continue
            check = _automation_check(criterion, result.status)
            if check is not None:
                checks.append(check)
            else:
                notes.append(f"plan (author) criterion checked by the run, not exported as code: {result.text} - {result.status}")
        if checks == list(automation.checks) and notes == list(automation.notes):
            return automation
        return automation.model_copy(update={"checks": checks, "notes": notes})

    async def _run_step(self, number: int, step: Step) -> tuple[StepOutcome | None, Step]:
        """Run step ``number`` within the case's remaining time; None if the run was cancelled meanwhile.

        A person's decision during the step (skip, retry, edit, reject) is applied here; after a failure
        the control may ask whether to retry. Returns the outcome and the step as it finally ran.
        """
        control = self.deps.control
        for _ in range(_MAX_HUMAN_RETRIES):
            remaining = self.case.timeout_s - (time.monotonic() - self.started - self._human_wait_s() - self.planning_s)
            if remaining <= 0:
                return StepOutcome(status="error", actual=f"case timeout of {self.case.timeout_s} s exceeded", error_class="CaseTimeout"), step
            assert self.runner is not None
            # A security step's own time limit (plus a margin to stop the scanner) replaces the default step limit.
            default = step.security.timeout_s + _SCAN_MARGIN_S if step.security is not None else self.deps.limits.step_timeout_s
            timeout = min(step.timeout_s or default, remaining)
            try:
                outcome = await _cancellable(self.runner.run(number, step, timeout_s=timeout), self.ctx.cancel)
            except SkipStep:
                return StepOutcome(status="skipped", actual="skipped by the tester"), step
            except StepBlocked as blocked:
                return StepOutcome(status="blocked", actual=blocked.reason, error_class="HumanRejected"), step
            except RetryStep as retry:
                step = step.model_copy(update={"action": self.data.protect(retry.text)}) if retry.text else step
                self.ctx.events.emit("step_started", step=number, action=step.action, retry=True)
                continue
            except RunCancelledError:
                return None, step
            if outcome is None or outcome.status in ("passed", "skipped") or control is None or outcome.run_first is not None:
                return outcome, step
            screenshot = next((uri for uri in reversed(outcome.evidence) if uri.endswith(("plain.jpg", "som.jpg"))), None)
            try:
                answer = await control.after_failure(number, step.action, outcome.error_class, outcome.actual, screenshot=screenshot)
            except RunCancelledError:
                return None, step
            if answer is None or answer.decision == "reject":
                return outcome, step
            if answer.decision == "edit" and answer.step_text:
                step = step.model_copy(update={"action": self.data.protect(answer.step_text)})
            self.ctx.events.emit("step_started", step=number, action=step.action, retry=True)
        return StepOutcome(status="error", actual=f"stopped after {_MAX_HUMAN_RETRIES} retries", error_class="TooManyRetries"), step

    async def finalize(self, state: RunState) -> dict[str, Any]:
        ctx, case = self.ctx, self.case
        ran = list(state.get("results") or ())  # in the order the steps ran
        done = {step.index for step in ran}
        ran += [StepResult(index=number, action=step.action, status="skipped", expected=step.expected) for number, step in enumerate(case.steps, start=1) if number not in done]
        results = sorted(ran, key=lambda step: step.index)
        ledger = tuple(state.get("ledger") or ())
        setup_error = state.get("setup_error")
        status = _run_status(results, cancelled=bool(state.get("cancelled")), setup_error=setup_error)
        quality = self.quality.section(tuple(state.get("quality") or ()))
        gate = quality.gate_failures() if quality is not None else []
        if gate and status == "passed":
            status = "failed"
        hints = [setup_error] if setup_error else []
        if gate:
            assert quality is not None
            hints.append(f"quality: {len(gate)} finding(s) on the audited pages at or above {quality.fail_on} (fail_on); see the quality section for fixes")
        hints += list(state.get("notes") or ())
        if self.resumed:
            hints.append("resumed from a checkpoint after the worker stopped")
        if not state.get("stopped") and not state.get("cancelled") and len(state.get("results") or ()) < len(case.steps) and self._step_cap_reached(state):
            hints.append(f"stopped after {self.deps.max_steps} of {len(case.steps)} steps (--max-steps)")
        plan = self.planner.finish(tuple(state.get("plan_results") or ()), tuple(state.get("amendments") or ()))
        hints += self._plan_hints()
        failure = await self._classify(status, ran, ledger, hints, unreadable=tuple(state.get("unreadable") or ()), test_data=tuple(state.get("test_data") or ()))
        if self.page_log is not None:
            self.page_log.close()
        evidence = self._evidence_paths()
        duration = time.monotonic() - self.started
        cost, counters = self.deps.usage() if self.deps.usage is not None else (CostInfo(), {})
        result = RunResult(
            run_id=ctx.run_id,
            status=status,
            case_id=case.id,
            verdict_source="ledger" if ledger else "none",
            steps=results,
            assertions=AssertionCounts(
                total=len(ledger),
                passed=sum(entry.verdict == "passed" for entry in ledger),
                failed=sum(entry.verdict == "failed" for entry in ledger),
                unverified=sum(entry.verdict == "unverified" for entry in ledger),
            ),
            hints=hints,
            failure=failure,
            evidence=evidence,
            engines=EngineInfo(
                llm=self.deps.llm,
                decider=_engine_name(self.deps.decider),
                egress=ctx.settings.egress.policy,
                mode=self.deps.mode,
                decider_model=self.deps.decider_model,
                **counters,
            ),
            cost=cost,
            duration_s=round(duration, 3),
            quality=quality,
            security=SecuritySection(scans=list(state.get("security") or ())) if state.get("security") else None,
            automation=self._automation(state),
            plan=plan,
        )
        write_junit(RunResult.model_validate_json(masked_json(result, self.data.redact)), self.evidence.run_dir)
        self.evidence.write_run(result)
        self.result = result
        telemetry_status = status if status in ("passed", "failed", "error", "cancelled") else "error"
        ctx.telemetry.emit("run_finished", run_status=telemetry_status, step_count=len(case.steps), duration_ms=int(duration * 1000))
        ctx.events.emit("run_finished", run_id=ctx.run_id, status=status)
        return {}

    def _plan_hints(self) -> list[str]:
        """The plan's lint findings and unmet inferred criteria, as hints (warnings shown with the result)."""
        plan = self.planner.plan
        if plan is None:
            return []
        hints: list[str] = []
        for finding in plan.lint[:3]:
            where = f"step {finding.step}: " if finding.step else ""
            hints.append(self.data.redact(f"plan lint ({finding.severity}) {where}{finding.message}" + (f" - {finding.suggestion}" if finding.suggestion else "")))
        if len(plan.lint) > 3:
            hints.append(f"plan lint: {len(plan.lint) - 3} more finding(s) in plan.json")
        unmet = [result for result in plan.results if result.origin == "inferred" and result.status in ("failed", "unverified")]
        if unmet:
            first = unmet[0]
            hints.append(
                self.data.redact(f"plan: {len(unmet)} inferred criterion(s) not met (warnings{'; strict mode fails them' if self.deps.plan.strict else ''}), first at step {first.step}: {first.text}")
            )
        return hints

    def _automation(self, state: RunState) -> AutomationSpec | None:
        """The run as framework-neutral automation (None, with a log line, if it cannot be built: exporting is never worth a run)."""
        try:
            appium = self.mobile.server_record() if self.mobile is not None else None
            return build_spec(self.case, tuple(state.get("automation") or ()), browser=self._needs_browser(), appium=appium)
        except Exception as exc:
            logger.warning("the run's automation spec was not built: %s", exc)
            return None

    async def _classify(
        self, status: RunStatus, results: list[StepResult], ledger: tuple[LedgerEntry, ...], hints: list[str], *, unreadable: tuple[int, ...] = (), test_data: tuple[int, ...] = ()
    ) -> FailureInfo | None:
        """The model's classification of a run that did not pass (not of a cancelled one).

        A run whose first problem step could not read the page is not a judgement of the test or the product: it is
        reported as ``environment`` (the page reader failed on that page) without asking a model to guess. A first problem
        step whose value is longer than its field accepts is ``test_data``, also without a model, and a security step that
        failed on its findings is ``product_bug`` (the scan found issues at or above its ``fail_on``).
        """
        if status not in ("failed", "error"):
            return None
        problem = next((step for step in results if step.status in ("failed", "error", "blocked", "unverified")), None)
        if problem is not None and problem.index in unreadable:
            signals = [f"step {problem.index}: the page could not be read (perception failed), so neither the test nor the product was judged"]
            return FailureInfo(category="environment", confidence=1.0, signals=signals, summary=self.data.redact(failure_summary(results, hints)))
        if problem is not None and problem.index in test_data:
            signals = [f"step {problem.index}: the step's value is longer than the field accepts, so the test's data does not fit the field"]
            return FailureInfo(category="test_data", confidence=1.0, signals=signals, summary=self.data.redact(failure_summary(results, hints)))
        if problem is not None and problem.status == "failed" and self.case.steps[problem.index - 1].security is not None:
            signals = [f"step {problem.index}: the security scan found issues at or above its fail_on severity"]
            return FailureInfo(category="product_bug", confidence=1.0, signals=signals, summary=self.data.redact(failure_summary(results, hints)))
        assertions = {"total": len(ledger), "failed": sum(entry.verdict == "failed" for entry in ledger), "unverified": sum(entry.verdict == "unverified" for entry in ledger)}
        api_steps = [number for number, step in enumerate(self.case.steps, start=1) if self.service.handles(step)]
        work = classify_failure(self.decider, status=status, steps=results, hints=[self.data.redact(hint) for hint in hints], assertions=assertions, api_steps=api_steps, redact=self.data.redact)
        failure = await _cancellable(work, self.ctx.cancel)
        return failure if failure is not None else FailureInfo(category="unclassified", confidence=0.0, signals=["cancelled before it was classified"])

    def _evidence_paths(self) -> EvidencePaths:
        """The run's evidence files, relative to the run directory (JUnit is written with the result)."""
        log = self.page_log
        console = log.console_path if log is not None else None
        network = log.network_path if log is not None else None
        videos = sorted((self.evidence.run_dir / VIDEO_DIRNAME).glob("*.webm")) if (self.evidence.run_dir / VIDEO_DIRNAME).is_dir() else []
        video = f"{VIDEO_DIRNAME}/{videos[0].name}" if videos else None
        return EvidencePaths(junit=JUNIT_FILE, console=console.name if console is not None else None, network=network.name if network is not None else None, video=video)


def _automation_check(criterion: Any, status: str) -> AutomationCheck | None:
    """An author criterion as an assertion of the exported test (None: a check form code cannot write as a plain assertion)."""
    verdict: Literal["passed", "failed", "unverified"] = status if status in ("passed", "failed", "unverified") else "unverified"  # type: ignore[assignment]
    note = f"from the plan (author criterion {criterion.id})"
    if criterion.check == "text_visible":
        return AutomationCheck(kind="text", expected=criterion.expected, verdict=verdict, note=note)
    if criterion.check == "text_absent":
        return AutomationCheck(kind="not_text", expected=criterion.expected, verdict=verdict, note=note)
    if criterion.check == "url_matches":
        return AutomationCheck(kind="url", expected=criterion.expected, match=criterion.match, verdict=verdict, note=note)
    if criterion.check == "element_state" and criterion.state == "visible" and criterion.target:
        return AutomationCheck(kind="visible_text", expected=criterion.target, verdict=verdict, note=note)
    if criterion.check == "judged":
        return AutomationCheck(kind="visual" if criterion.visual else "semantic", expected=criterion.expected or criterion.text, verdict=verdict, source="llm", faithful=False, note=note)
    return None


def _run_status(results: list[StepResult], *, cancelled: bool, setup_error: str | None) -> RunStatus:
    if setup_error:
        return "error"
    if cancelled:
        return "cancelled"
    if any(step.status == "error" for step in results):
        return "error"
    return "passed" if results and all(step.status == "passed" for step in results) else "failed"


def _engine_name(decider: DecisionEngine) -> str:
    return str(getattr(decider, "engine", type(decider).__name__))


def _first_line(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return lines[0][:300] if lines else ""


async def _stop_reading(reading: asyncio.Future[Any]) -> None:
    """Cancel the case reading started beside the browser and wait for it to end (its own failure is moot)."""
    reading.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await reading


async def _cancellable(work: Awaitable[T], token: CancelToken) -> T | None:
    """Await ``work``, polling the cancel token; a cancelled run aborts the step (or audit) and returns None."""
    task = asyncio.ensure_future(work)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=_CANCEL_POLL_S)
            if done:
                return task.result()
            if token.is_cancelled:
                return None
    finally:
        # Cancelled by the token or this awaiter was cancelled: the work must not go on in the background.
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                if not task.cancelled():  # this caller was cancelled while waiting
                    raise
            except Exception as exc:
                logger.debug("a cancelled run task ended with %s", type(exc).__name__)


def _rewind(state: RunState, position: int, order: tuple[int, ...]) -> dict[str, Any]:
    """Go back one step (a retry of the step that ran before): drop its result and its ledger entries."""
    results = tuple(state.get("results") or ())
    previous = order[position - 1]  # the number of the step that ran before this one
    return {
        "next_index": position - 1,
        "results": results[:-1],
        "ledger": tuple(entry for entry in state.get("ledger") or () if entry.step != previous),
        "stopped": False,
        "quality": tuple(page for page in state.get("quality") or () if page.step != previous),
        "quality_seen": tuple(item for item in state.get("quality_seen") or () if int(item.split(":", 1)[0]) != previous),
        "security": tuple(scan for scan in state.get("security") or () if scan.step != previous),
        "automation": tuple(step for step in state.get("automation") or () if step.index != previous),
        "unreadable": tuple(number for number in state.get("unreadable") or () if number != previous),
        "plan_results": tuple(result for result in state.get("plan_results") or () if result.step != previous),
    }


async def run_case(case: TestCase, ctx: RunContext, deps: RunDeps, *, resume: bool = False) -> RunResult:
    """Run one test case end to end and return its :class:`RunResult` (also saved as ``run.json``).

    ``resume`` continues the run ``ctx.run_id`` from its last checkpoint (``deps.checkpointer``
    is required); :class:`ResumeError` when there is nothing to resume.
    """
    if deps.control is not None:
        deps.control.bind(asyncio.get_running_loop(), cancel=ctx.cancel)
    config: dict[str, Any] = {"recursion_limit": len(case.steps) * _RECURSION_PER_STEP + _RECURSION_EXTRA}
    if deps.checkpointer is not None:
        config["configurable"] = {"thread_id": ctx.run_id}
    async with contextlib.AsyncExitStack() as stack:
        run = _Run(case, ctx, deps, stack)
        graph = run.graph()
        if resume:
            if deps.checkpointer is None:
                raise ResumeError("resuming needs a checkpointer")
            snapshot = await graph.aget_state(config)
            if not snapshot.values:
                raise ResumeError(f"no checkpoint for run {ctx.run_id}")
            if not snapshot.next:
                raise ResumeError(f"run {ctx.run_id} already finished")
            run.evidence.run_dir.mkdir(parents=True, exist_ok=True)
            await graph.ainvoke(None, config=config)
        else:
            initial: RunState = {
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
            await graph.ainvoke(initial, config=config)
    if run.result is None:  # finalize always runs; this guards against a graph wiring mistake
        raise RuntimeError("run finished without a result")
    if deps.checkpointer is not None:
        await deps.checkpointer.adelete_thread(ctx.run_id)  # finished: nothing to resume, and no cookies left behind
    return run.result
