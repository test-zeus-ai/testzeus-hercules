"""``hercules plan CASE``: make a case's plan and lint it without running it.

The plan is made exactly as a run makes it (see :mod:`testzeus_hercules.engine.planning`):
the local model reads the case, the decider audits its coverage, lint adds the model's
findings and the facts. It is printed as text, or as JSON with ``--json`` (the
:class:`~testzeus_hercules.cases.plan.Plan`, ready to edit and pass to
``hercules run --plan FILE``); ``--out FILE`` also writes the JSON. ``--approve`` stores it as
the approved plan of this case version and model, so daemon runs with ``plan_review: ask``
reuse it without asking.

Exit codes: 0 when the plan was made (1 with ``--strict`` when it has lint errors), 2 when it
could not be made (bad case file, no model server).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TextIO

from testzeus_hercules.cases.plan import Plan
from testzeus_hercules.cli.deciders import key_origin, prepare
from testzeus_hercules.cli.run import CaseLoadError, RunCommandDeps, RunOptions, load_case, with_environment
from testzeus_hercules.engine.data import CaseData, protect_case
from testzeus_hercules.engine.factory import EngineFactoryError, FactoryOptions, build_engines
from testzeus_hercules.engine.masking import MaskingDecider
from testzeus_hercules.engine.plan import PlanStore, render_plan
from testzeus_hercules.engine.planning import PlanOptions, approve_plan, make_plan
from testzeus_hercules.engine.service_intent import case_hint
from testzeus_hercules.cases.reference_files import load_reference_context, using_reference
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.secrets_store import with_saved_secrets
from testzeus_hercules.kernel.settings import Settings, SettingsError

EXIT_OK = 0
EXIT_LINT = 1
EXIT_ERROR = 2

__all__ = ["PlanCommandOptions", "options_from_args", "plan_command", "render_plan"]


@dataclass(frozen=True)
class PlanCommandOptions:
    case_path: Path
    project_dir: Path | None = None
    mode: str = "auto"
    egress: str | None = None
    jev_key_env: str | None = None
    decider: str | None = None
    base_url: str | None = None
    environment: str | None = None
    json_output: bool = False
    out_path: Path | None = None
    approve: bool = False
    strict: bool | None = None
    coverage: bool | None = None
    fresh: bool = False
    confirm_destructive: bool = False


def options_from_args(args: argparse.Namespace) -> PlanCommandOptions:
    return PlanCommandOptions(
        case_path=args.case,
        project_dir=args.project,
        mode=args.mode,
        egress=args.egress,
        jev_key_env=args.jev_key_env,
        decider=args.decider,
        base_url=args.base_url,
        environment=args.env,
        json_output=args.json,
        out_path=args.out,
        approve=args.approve,
        strict=args.strict,
        coverage=False if args.no_coverage else None,
        fresh=args.fresh,
    )


def plan_command(options: PlanCommandOptions, *, env: Mapping[str, str] | None = None, deps: RunCommandDeps | None = None, out: TextIO, err: TextIO) -> int:
    """Make the plan of ``options.case_path`` and print it; returns the exit code."""
    deps = deps or RunCommandDeps()
    environ: Mapping[str, str] = dict(os.environ) if env is None else env
    try:
        overrides = {"egress": {"policy": options.egress}} if options.egress is not None else None
        settings = Settings.load(options.project_dir, env=environ, overrides=overrides)
        environ = with_saved_secrets(environ, settings.home_dir)
        case = load_case(options.case_path, envs_dir=HerculesPaths.from_settings(settings).envs_dir, environment=options.environment)
        case = with_environment(case, HerculesPaths.from_settings(settings), RunOptions(case_path=options.case_path, base_url=options.base_url, environment=options.environment))
    except (CaseLoadError, SettingsError) as exc:
        out.write(f"error: {exc}\n")
        return EXIT_ERROR
    try:
        plan, info = asyncio.run(_make(case, settings, environ, options, deps))
    except EngineFactoryError as exc:
        out.write(f"error: {exc}\n")
        return EXIT_ERROR
    if plan is None:
        out.write(f"error: {info}\n")
        return EXIT_ERROR
    text = plan.model_dump_json(indent=2)
    if options.out_path is not None:
        try:
            options.out_path.write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            out.write(f"error: cannot write {options.out_path} ({type(exc).__name__})\n")
            return EXIT_ERROR
    if options.json_output:
        out.write(text + "\n")
        err.write(info + "\n")
    else:
        out.write(render_plan(plan) + "\n" + info + "\n")
    strict = settings.plan.strict if options.strict is None else options.strict
    return EXIT_LINT if strict and plan.lint_errors else EXIT_OK


async def _make(case: Any, settings: Settings, env: Mapping[str, str], options: PlanCommandOptions, deps: RunCommandDeps) -> tuple[Plan | None, str]:
    setup = prepare(settings, env, key_env=options.jev_key_env)
    factory = FactoryOptions(mode=options.mode, decider=options.decider, jev_key_env=options.jev_key_env, key_origin=key_origin(setup.key))  # type: ignore[arg-type]
    engines = await build_engines(setup.settings, env=setup.env, options=factory, deps=deps.factory)
    try:
        if engines.interpreter is None:
            return None, "no model is configured to read the case; run `hercules setup` / `hercules doctor`"
        protected, hidden = protect_case(case)
        data = CaseData.from_case(protected, env=env, hidden=hidden)
        paths = HerculesPaths.from_settings(settings)
        model_id = str(getattr(engines.interpreter, "model_id", "") or "")
        store = PlanStore(paths.project_hercules_dir / "plans") if model_id else None
        plan_options = PlanOptions(
            coverage=settings.plan.coverage if options.coverage is None else options.coverage,
            strict=settings.plan.strict if options.strict is None else bool(options.strict),
            confirm_destructive=options.confirm_destructive,
            reuse=not options.fresh,
        )
        with using_reference(load_reference_context(settings.project_dir, protected.environment.ref, data.redact, case_id=protected.id)):
            outcome = await make_plan(
                protected,
                interpreter=engines.interpreter,
                decider=MaskingDecider(engines.decider, data.redact),
                model_id=model_id,
                store=store,
                env=env,
                names=sorted(set(protected.test_data) | set(hidden)),
                hidden=sorted(hidden),
                service=case_hint(protected.tags) is not None,
                options=plan_options,
                redact=data.redact,
            )
        plan = outcome.plan
        if options.approve:
            plan = approve_plan(plan, store, outcome.key, by="user")
        usage = engines.usage()
        calls = usage.local_calls
        decisions = usage.decisions
        decided = f", {decisions.jev_calls} decider call(s), ${decisions.cost_usd:.4f}" if decisions is not None else ""
        info = f"{engines.describe()}\nPlanning: {plan.timing.total_ms / 1000:.1f} s ({outcome.origin}); {calls} local model call(s){decided}"
        if options.approve:
            info += "\nApproved: runs of this case version with this model reuse this plan"
        return plan, info
    finally:
        await engines.aclose()
