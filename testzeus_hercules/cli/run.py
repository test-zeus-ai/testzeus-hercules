"""``hercules run <case.json|scenario.feature>``: run one test case and print its result.

``--mode`` picks who does what: ``pure`` (the local model does everything, or a
local Clef server decides when one is configured) or ``hybrid`` (JEV makes the
decisions, the local model does the rest); ``auto`` (the default) is hybrid when the
JEV key is found (environment or ``<hercules home>/openrouter.env``) and the egress
policy lets page content leave the machine (it does by default), else pure with one
notice line. ``--egress`` overrides the egress policy for the run. Every step is
read and every decision made by a model: when no model server can be used the run
exits 2 and says to run ``hercules setup`` / ``hercules doctor`` (there is no coded
fallback). The chosen mode is printed before the run (to stderr with ``--json``, so
stdout stays one JSON document), and the per-engine calls and cost after it.

Exit codes: 0 when the run passed, 1 when it failed (or was cancelled),
2 when it could not run (bad case file, browser, perception or model unavailable).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, TextIO

from pydantic import ValidationError

from testzeus_hercules.act.redact import mask_url_credentials
from testzeus_hercules.cases.gherkin import GherkinError, load_feature
from testzeus_hercules.cases.models import CostInfo, EnvironmentRef, RunResult, TestCase, placeholder_names, set_environment_names
from testzeus_hercules.cli.progress import StepProgress
from testzeus_hercules.cli.style import Style
from testzeus_hercules.cli.deciders import DeciderSetup, fallback_notice, key_origin, prepare
from testzeus_hercules.engine.factory import TEST_DECIDERS, EngineFactoryError, FactoryDeps, FactoryOptions, RunEngines, RunMode, TestDecider, build_engines
from testzeus_hercules.engine.ports import DefaultPerceiver, Perceiver
from testzeus_hercules.cases.plan import Plan
from testzeus_hercules.engine.run_graph import PageLauncher, ResumeError, RunDeps, RunPlanOptions, run_case
from testzeus_hercules.engine.step_graph import StepLimits
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.secrets_store import with_saved_secrets
from testzeus_hercules.kernel.run_context import CancelToken, EventSink, RunContext
from testzeus_hercules.kernel.settings import EgressPolicy, Settings, SettingsError
from testzeus_hercules.kernel.telemetry import TelemetryClient, run_origin_from_env

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_ERROR = 2

DECIDERS: tuple[TestDecider, ...] = TEST_DECIDERS


class CaseLoadError(ValueError):
    """The case file cannot be read or is not a valid test case."""


def _default_telemetry(settings: Settings, paths: HerculesPaths) -> TelemetryClient:
    # `hercules run` is "cli"; the daemon's workers get their run's origin (tester UI or a coding agent) in the environment.
    origin, agent = run_origin_from_env(os.environ)
    return TelemetryClient(settings, paths, run_origin=origin, agent_client=agent)


@dataclass(frozen=True)
class RunCommandDeps:
    """Seams for tests: perception, browser, actions, telemetry and the model server connection.

    ``events`` and ``cancel`` let a caller (the daemon's worker) watch the run and stop it;
    ``control`` pauses it and answers approvals, ``on_page`` sees the browser page once it is
    open (the live view), and ``checkpoints`` keeps checkpoints (in
    ``.hercules/checkpoints/<run_id>.db``) so the run can be resumed; they are removed when it finishes.
    """

    make_perceiver: Callable[[], Perceiver] | None = None
    launcher: PageLauncher | None = None
    actions: Any = None
    make_telemetry: Callable[[Settings, HerculesPaths], TelemetryClient] = _default_telemetry
    factory: FactoryDeps = field(default_factory=FactoryDeps)
    events: EventSink | None = None
    cancel: CancelToken | None = None
    control: Any = None
    on_page: Callable[[Any], Awaitable[None]] | None = None
    checkpoints: bool = False


@dataclass(frozen=True)
class RunOptions:
    case_path: Path
    project_dir: Path | None = None
    headed: bool = False
    mode: RunMode = "auto"
    egress: EgressPolicy | None = None
    jev_key_env: str | None = None
    decider: TestDecider | None = None
    json_output: bool = False
    base_url: str | None = None
    environment: str | None = None  # saved environment name (overrides the case's ``environment.ref``)
    quality: Any = None  # engine.quality.RunQualityOptions: run-level a11y/visual audits (off when None)
    allow_draft: bool = True  # False: refuse a ``draft`` case (plain ``hercules run``; the daemon asks for approval instead)
    max_steps: int | None = None
    step_timeout_s: float | None = None
    run_id: str | None = None  # pre-assigned by the daemon; a new id when None
    resume: bool = False  # continue run_id from its checkpoint
    strict: bool | None = None  # strict plan mode (None: plan.strict from settings)
    plan_path: Path | None = None  # run this plan (``hercules plan --json`` output, edited), checked against the case
    plan_review: str | None = None  # auto | ask (None: plan.review from settings; ask needs a daemon to answer)
    explore_path: Path | None = None  # an exploration job (explorer or reproducer) the daemon wrote; the case gives the environment


def load_case(path: Path, *, envs_dir: Path | None = None, environment: str | None = None) -> TestCase:
    """Load a ``.json`` TestCase or compile a single-scenario ``.feature`` file; with ``envs_dir``, ``{{name}}``s that
    the saved environment (``environment``, else the case's own) defines are known names."""
    if envs_dir is None:
        return _load_case(path)
    from testzeus_hercules.daemon.cases import environment_data, environment_names

    set_environment_names(environment_names(envs_dir))
    with placeholder_names(set(environment_data(envs_dir, environment))):
        return _load_case(path)


def _load_case(path: Path) -> TestCase:
    if not path.is_file():
        raise CaseLoadError(f"case file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".feature":
        try:
            return load_feature(path)
        except (GherkinError, ValidationError) as exc:
            raise CaseLoadError(str(exc)) from None
    if suffix == ".json":
        try:
            return TestCase.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as exc:
            raise CaseLoadError(f"invalid test case in {path.name}: {_brief(exc)}") from None
    raise CaseLoadError(f"unsupported case file {path.name!r}: use a .json test case or a .feature file")


def _brief(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}" for error in exc.errors()[:5])
    return exc.__class__.__name__


def with_base_url(case: TestCase, base_url: str) -> TestCase:
    """``case`` with its environment's base URL replaced (validated like a case file's)."""
    try:
        environment = EnvironmentRef.model_validate({**case.environment.model_dump(exclude_none=True), "base_url": base_url})
    except ValidationError:
        raise CaseLoadError(f"invalid --base-url {base_url!r}: use an http(s) URL with a host, for example http://127.0.0.1:8000") from None
    return case.model_copy(update={"environment": environment})


def with_environment(case: TestCase, paths: HerculesPaths, options: RunOptions) -> TestCase:
    """Apply the saved environment (``--env``, else the case's ``ref``) and ``--base-url``, as the daemon does."""
    if options.environment is None and case.environment.ref is None:
        return with_base_url(case, options.base_url) if options.base_url is not None else case
    from testzeus_hercules.daemon.cases import CaseError, apply_environment

    try:
        return apply_environment(case, paths.envs_dir, environment=options.environment, base_url=options.base_url)
    except CaseError as exc:
        message = str(exc).replace("save it with POST /api/environments first", f"save it in {paths.envs_dir} (or from the UI), or pass --base-url")
        raise CaseLoadError(message) from None


def _perception_available() -> bool:
    try:
        import testzeus_hercules.perceive  # noqa: F401
    except ImportError:
        return False
    return True


def exit_code(result: RunResult) -> int:
    if result.status == "passed":
        return EXIT_PASSED
    return EXIT_ERROR if result.status == "error" else EXIT_FAILED


def run_command(options: RunOptions, *, env: Mapping[str, str] | None = None, deps: RunCommandDeps | None = None, out: TextIO, err: TextIO | None = None) -> int:
    """Run the case named by ``options`` and print the result; returns the exit code."""
    deps = deps or RunCommandDeps()
    errors = err if err is not None else sys.stderr
    environ: Mapping[str, str] = dict(os.environ) if env is None else env
    try:
        overrides = {"egress": {"policy": options.egress}} if options.egress is not None else None
        settings = Settings.load(options.project_dir, env=environ, overrides=overrides)
        environ = with_saved_secrets(environ, settings.home_dir)
        case = load_case(options.case_path, envs_dir=HerculesPaths.from_settings(settings).envs_dir, environment=options.environment)
        case = with_environment(case, HerculesPaths.from_settings(settings), options)
    except (CaseLoadError, SettingsError) as exc:
        out.write(f"error: {exc}\n")
        return EXIT_ERROR
    if case.status == "draft" and not options.allow_draft:
        out.write(f'error: {options.case_path.name} is a draft; review its steps and set "status": "ready", or pass --allow-draft to run it anyway\n')
        return EXIT_ERROR
    try:
        given = load_plan(options.plan_path) if options.plan_path is not None else None
    except CaseLoadError as exc:
        out.write(f"error: {exc}\n")
        return EXIT_ERROR
    if deps.make_perceiver is None and not _perception_available():
        out.write("error: the perception package is not installed in this build; cannot take page snapshots\n")
        return EXIT_ERROR

    decider_setup = prepare(settings, environ, key_env=options.jev_key_env)
    paths = HerculesPaths.from_settings(settings)
    telemetry = deps.make_telemetry(settings, paths)
    style = Style.for_stream(out, environ)
    # `hercules run` shows each step as it goes (on standard error with --json, so standard output stays one
    # JSON document); the daemon's worker watches the run through deps.events instead.
    progress_stream = errors if options.json_output else out
    progress = StepProgress(progress_stream, Style.for_stream(progress_stream, environ)) if deps.events is None else None
    ctx = RunContext.create(settings, telemetry=telemetry, run_id=options.run_id, events=deps.events or progress)
    if deps.cancel is not None:
        ctx = replace(ctx, cancel=deps.cancel)
    notices = errors if options.json_output else out
    checkpoint = checkpoint_file(paths, ctx.run_id) if deps.checkpoints else None
    try:
        result, engines = asyncio.run(_execute(case, ctx, settings, options, environ, deps, notices, checkpoint, decider_setup, given))
    except (EngineFactoryError, ResumeError) as exc:
        out.write(f"error: {exc}\n")
        return EXIT_ERROR
    finally:
        telemetry.close()
        if progress is not None:
            progress.close()
    if checkpoint is not None:
        checkpoint.unlink(missing_ok=True)

    # The mode line was printed before the run; the summary after it lists who decided and what it cost.
    summary = engine_summary(engines, paths.run_dir(result.run_id))[1:] + plan_lines(result)
    # The result as run.json has it: URL credentials masked (CI logs keep what is printed here).
    if options.json_output:
        out.write(mask_url_credentials(result.model_dump_json(indent=2)) + "\n")
        errors.write("".join(line + "\n" for line in summary))
    else:
        out.write(styled_summary(mask_url_credentials(result.summary_text()), result.status, style) + "\n")
        out.write("".join(style.dim(line) + "\n" for line in summary))
        out.write(f"{style.accent('Evidence:')} {_shown(paths.run_dir(result.run_id))}\n")
    if case.status == "known_bug" and options.explore_path is None:
        return known_bug_exit(result, out)
    return exit_code(result)


def known_bug_exit(result: RunResult, out: TextIO) -> int:
    """A ``known_bug`` case reproduces an open bug: still failing is expected (exit 0); passing means the bug may be
    fixed (exit 0, and the finding moves to verify_fixed with `hercules explore reverify`); an error stays an error."""
    if result.status == "error":
        return EXIT_ERROR
    if result.status == "passed":
        out.write("note: this known_bug case now passes: the bug may be fixed. Promote it to a regression test (status ready) or run `hercules explore reverify`.\n")
    else:
        out.write("note: this known_bug case still fails, as expected: its bug is open. It does not fail the suite.\n")
    return EXIT_PASSED


def styled_summary(text: str, status: str, style: Style) -> str:
    """The run summary with its status word and line labels coloured (unchanged without colour)."""
    if not style.enabled:
        return text
    first, *rest = text.split("\n")
    word = status.upper()
    lines = [first.replace(f" {word} ", f" {style.status(status, word)} ", 1)]
    for line in rest:
        label, sep, value = line.partition(": ")
        labelled = sep and (" " not in label or label.startswith("Step "))
        lines.append(f"{style.dim(label + ':')} {value}" if labelled else line)
    return "\n".join(lines)


def checkpoint_file(paths: HerculesPaths, run_id: str) -> Path:
    """Where a run's checkpoints live (outside the run directory, which the daemon serves)."""
    return paths.project_hercules_dir / "checkpoints" / f"{run_id}.db"


async def _execute(
    case: TestCase,
    ctx: RunContext,
    settings: Settings,
    options: RunOptions,
    env: Mapping[str, str],
    deps: RunCommandDeps,
    notices: TextIO,
    checkpoint: Path | None = None,
    decider_setup: DeciderSetup | None = None,
    given: Plan | None = None,
) -> tuple[RunResult, RunEngines]:
    """Build the engines and run the case. The JEV key from the key file reaches the engines only, never the case's environment."""
    setup = decider_setup or prepare(settings, env, key_env=options.jev_key_env)
    factory_options = FactoryOptions(mode=options.mode, decider=options.decider, jev_key_env=options.jev_key_env, key_origin=key_origin(setup.key))
    engines = await build_engines(setup.settings, env=setup.env, options=factory_options, deps=deps.factory)
    try:
        if engines.mode == "hybrid" and setup.key.source == "file" and str(setup.key.path) not in engines.mode_reason:
            engines.mode_reason += f" (key from {setup.key.path})"
        notice = fallback_notice(engines, setup.key, options.mode)
        if notice is not None:
            notices.write(notice + "\n")
        notices.write(engines.describe() + "\n")
        for note in engines.notes:
            notices.write(f"  note: {note}\n")
        if engines.warmup_ms is not None:
            notices.write(f"  model warm-up: {engines.warmup_ms} ms\n")
        notices.flush()
        # Every candidate goes to the decider; a pick larger than one request is split by the decider itself.
        limits = step_limits(engines.mode, options.step_timeout_s)
        run_deps = RunDeps(
            decider=engines.decider,
            perceiver=deps.make_perceiver() if deps.make_perceiver is not None else DefaultPerceiver(),
            interpreter=engines.interpreter,
            launcher=deps.launcher,
            env=env,
            headless=not options.headed,
            images=engines.images,
            limits=limits,
            llm=engines.llm.model_id if engines.llm is not None else None,
            max_steps=options.max_steps,
            mode=engines.mode,
            decider_model=engines.decider_model,
            **({"quality": options.quality} if options.quality is not None else {}),
            usage=lambda: run_usage(engines),
            control=deps.control,
            on_page=deps.on_page,
            plan=plan_options(settings, options, given, deps.control),
        )
        if deps.actions is not None:
            run_deps.actions = deps.actions
        if options.explore_path is not None:
            from testzeus_hercules.explore.explorer import ExploreJob, run_exploration
            from testzeus_hercules.explore.reproduce import load_job, run_reproduction

            job = load_job(options.explore_path)
            if isinstance(job, ExploreJob):
                return await run_exploration(case, ctx, run_deps, job), engines
            return await run_reproduction(case, ctx, run_deps, job), engines
        if checkpoint is None:
            return await run_case(case, ctx, run_deps, resume=options.resume), engines
        from testzeus_hercules.engine.checkpoint import SqliteCheckpointer

        run_deps.checkpointer = SqliteCheckpointer(checkpoint)
        try:
            return await run_case(case, ctx, run_deps, resume=options.resume), engines
        finally:
            run_deps.checkpointer.close()
    finally:
        await engines.aclose()


def plan_lines(result: RunResult) -> list[str]:
    """One line about the run's plan: its criteria, what the run changed, its lint and what planning took."""
    plan = result.plan
    if plan is None:
        return []
    unmet = plan.failed + plan.unverified
    criteria = f"{plan.criteria} criteria ({plan.author} author, {plan.inferred} inferred): {plan.passed} held, {unmet} not met, {plan.not_run} not run"
    recoveries = f", {plan.recoveries} drove a recovery" if plan.recoveries else ""
    lint = f"lint {plan.lint_errors} error(s), {plan.lint_warnings} warning(s)" if plan.lint_errors or plan.lint_warnings else "lint clean"
    strict = ", strict" if plan.strict else ""
    return [f"Plan ({plan.source}{strict}): {plan.steps} plan steps; {criteria}{recoveries}; {plan.amendments} amendment(s); {lint}; planning {plan.planning_ms / 1000:.1f} s (plan.json)"]


def load_plan(path: Path) -> Plan:
    """A plan file (``hercules plan --json`` output, possibly edited), validated; the run checks it against its case."""
    if not path.is_file():
        raise CaseLoadError(f"plan file not found: {path}")
    try:
        return Plan.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError) as exc:
        raise CaseLoadError(f"invalid plan in {path.name}: {_brief(exc) if isinstance(exc, ValidationError) else type(exc).__name__}") from None


# In pure mode a local model makes every decision, with the page's screenshot: on a large page one takes tens of
# seconds, and a step may need several. Its steps get this long unless the run sets a limit.
PURE_STEP_TIMEOUT_S = 300.0


def step_limits(mode: str, step_timeout_s: float | None) -> StepLimits:
    """The run's step limits: the time limit the run sets, else the default (longer in pure mode)."""
    if step_timeout_s is not None:
        return replace(StepLimits(), step_timeout_s=step_timeout_s)
    return replace(StepLimits(), step_timeout_s=PURE_STEP_TIMEOUT_S) if mode == "pure" else StepLimits()


def plan_options(settings: Settings, options: RunOptions, given: Plan | None, control: Any) -> RunPlanOptions:
    """The run's plan options: the run's flags over the ``plan`` settings; asking before risky actions follows the run's policy."""
    review = options.plan_review if options.plan_review in ("auto", "ask") else settings.plan.review
    policy = getattr(control, "policy", None)
    return RunPlanOptions(
        review=review,  # type: ignore[arg-type]
        strict=settings.plan.strict if options.strict is None else options.strict,
        coverage=settings.plan.coverage,
        given=given,
        given_by="file",
        confirm_destructive=bool(getattr(policy, "confirm_destructive", False)),
    )


def run_usage(engines: RunEngines) -> tuple[CostInfo, dict[str, int]]:
    """The run's cost (local-model tokens, JEV dollars) and decision counters for the RunResult."""
    usage = engines.usage()
    decisions = usage.decisions
    estimate = decisions.prompt_tokens_est if decisions is not None and decisions.jev_calls else None
    cost = CostInfo(tokens_in=usage.tokens_in, tokens_out=usage.tokens_out, usd=usage.cost_usd, decision_tokens_est=estimate)
    counters = {"clef_calls" if usage.clef else "jev_calls": decisions.jev_calls, "fallbacks": decisions.fallbacks} if decisions is not None else {}
    if usage.cache_hits:
        counters["llm_cache_hits"] = usage.cache_hits
    if engines.llm is not None and not engines.llm.endpoint.is_local and usage.local_calls > usage.cache_hits:
        counters["hosted_llm_calls"] = usage.local_calls - usage.cache_hits
    return cost, counters


def engine_summary(engines: RunEngines, run_dir: Path) -> list[str]:
    """The mode, which engine answered each step's decisions, and the calls and cost per engine."""
    per_step = _decision_engines(run_dir)
    totals: Counter[str] = Counter(engine for step in per_step.values() for engine in step)
    lines = [engines.describe()]
    if totals:
        steps = "; ".join(f"step {index}: {', '.join(step)}" for index, step in sorted(per_step.items()))
        lines.append(f"Decisions: {', '.join(f'{engine} {count}' for engine, count in sorted(totals.items()))} ({steps})")
    usage = engines.usage()
    decisions = usage.decisions
    if decisions is not None:
        images = f", {decisions.image_calls} with screenshots" if decisions.image_calls else ""
        failed = f", {decisions.jev_failures} failed" if decisions.jev_failures else ""
        fallback = f"; local fallback {decisions.fallbacks}" if decisions.fallbacks else ""
        tokens = f", ~{decisions.prompt_tokens_est:,} prompt tokens (estimated)" if decisions.jev_calls else ""
        lines.append(f"{engines.decider_label} calls: {decisions.jev_calls}{images}{failed}{tokens}, cost ${decisions.cost_usd:.4f}{fallback}")
    if engines.llm is not None:
        calls = engines.llm.calls
        if calls:
            total_ms = sum(call.latency_ms for call in calls)
            slowest = max(call.latency_ms for call in calls)
            cached = f"; {usage.cache_hits} answered from the proxy's cache" if usage.cache_hits else ""
            lines.append(
                f"Local model calls: {len(calls)}, {total_ms / 1000:.1f} s total, mean {total_ms // len(calls)} ms, slowest {slowest} ms, "
                f"{usage.tokens_in} tokens in / {usage.tokens_out} out, cost $0 (local){cached}"
            )
        else:
            lines.append("Local model calls: 0")
    return lines


def _decision_engines(run_dir: Path) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    for path in sorted((run_dir / "steps").glob("*/decisions.jsonl")):
        try:
            index = int(path.parent.name)
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError):
            continue
        found[index] = [str(record.get("engine") or record.get("decision", {}).get("engine") or "?") for record in records]
    return found


def _shown(path: Path) -> str:
    """``path`` relative to the current folder when it is inside it (the usual case), else as it is."""
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
    except (ValueError, OSError):
        return str(path)
