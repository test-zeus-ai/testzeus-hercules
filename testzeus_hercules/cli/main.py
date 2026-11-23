"""Entry point of the ``hercules`` command.

This module must stay cheap to import: it only builds the argument parser.
Each subcommand imports what it needs when it runs.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence, TextIO

if TYPE_CHECKING:
    from testzeus_hercules.cli.doctor import DoctorDeps
    from testzeus_hercules.cli.run import RunCommandDeps
    from testzeus_hercules.cli.setup import SetupDeps


def build_parser() -> argparse.ArgumentParser:
    from testzeus_hercules.cli.parser import HerculesArgumentParser

    parser: argparse.ArgumentParser = HerculesArgumentParser(
        prog="hercules", description="Hercules, an open-source QA engine by TestZeus: plain-language end-to-end testing in a real browser, driven by language models."
    )
    parser.add_argument("--no-telemetry", action="store_true", help=_NO_TELEMETRY_HELP)
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    doctor = commands.add_parser("doctor", help="check the configuration, hardware tier and model server", description="Check the configuration, hardware tier and model server.")
    doctor.add_argument("--json", action="store_true", help="print the report as JSON")
    doctor.add_argument("--project", type=Path, default=None, metavar="DIR", help="project directory (default: the current directory)")
    doctor.add_argument("--no-cache", action="store_true", help="re-probe the server instead of reusing cached capabilities")

    run = commands.add_parser("run", help="run a test case (.json) or a single-scenario .feature file", description="Run a test case and print its result.")
    run.add_argument("case", type=Path, metavar="CASE", help="path to a TestCase .json file or a .feature file")
    run.add_argument("--project", type=Path, default=None, metavar="DIR", help="project directory (default: the current directory)")
    run.add_argument("--headed", action="store_true", help="show the browser window")
    run.add_argument(
        "--mode",
        choices=("auto", "pure", "hybrid"),
        default="auto",
        help="pure: the local model does everything (or a local Clef server decides, when configured); hybrid: JEV makes the decisions "
        "and the local model does the rest. Default auto: hybrid when an OpenRouter key is found (environment or ~/.hercules/openrouter.env) "
        "and egress allows it, else pure",
    )
    run.add_argument(
        "--egress",
        choices=("local", "hybrid-text", "hybrid"),
        default=None,
        help="what may leave the machine for this run: local (nothing), hybrid-text (page text to JEV) or hybrid "
        "(text, and screenshots when JEV is unsure). Default: egress.policy from settings (hybrid)",
    )
    run.add_argument("--jev-key-env", type=_env_var_name, default=None, metavar="NAME", help="environment variable holding the JEV (OpenRouter) API key (default OPENROUTER_API_KEY)")
    run.add_argument("--decider", choices=("stub",), default=None, help=argparse.SUPPRESS)  # a test double: fixed answers, no model
    run.add_argument("--base-url", default=None, metavar="URL", help="base URL of the app under test (overrides the case's environment)")
    run.add_argument("--env", default=None, metavar="NAME", help="saved environment to run against (default: the case's environment.ref)")
    run.add_argument("--allow-draft", action="store_true", help="run a case whose status is draft without reviewing it first")
    run.add_argument("--max-steps", type=_positive_int, default=None, metavar="N", help="run at most N steps; the rest are reported as skipped")
    run.add_argument("--step-timeout-s", type=_positive_float, default=None, metavar="S", help="time limit per step in seconds (default 120, 300 in pure mode; a step's own timeout wins)")
    run.add_argument("--json", action="store_true", help="print the RunResult as JSON")
    run.add_argument("--a11y", action="store_true", help="audit every page the run reaches for accessibility")
    run.add_argument("--visual", action="store_true", help="compare every page with its visual baseline and check the layout")
    run.add_argument("--quality-fail-on", choices=("critical", "serious", "moderate", "minor"), default=None, help="fail the run on audit findings at or above this impact")
    run.add_argument(
        "--strict",
        action="store_true",
        default=None,
        help="strict plan mode: lint errors stop the run before the browser starts, and an inferred success criterion still unmet after recovery fails its step (default: plan.strict)",
    )
    run.add_argument("--plan", type=Path, default=None, metavar="FILE", help="run this plan (hercules plan --json output, possibly edited); it must be a plan of this case")

    _add_plan_command(commands)
    commands.add_parser("version", help="print the Hercules version", description="Print the Hercules version.")
    completion = commands.add_parser(
        "completion",
        help="print a shell completion script (bash or zsh)",
        description='Print a shell completion script. bash: hercules completion bash > ~/.local/share/bash-completion/completions/hercules; zsh: add eval "$(hercules completion zsh)" to ~/.zshrc.',
    )
    completion.add_argument("shell", choices=("bash", "zsh"), help="the shell to complete in")
    _add_setup_command(commands)
    _add_daemon_commands(commands)
    from testzeus_hercules.cli.cloud_cmd import add_cloud_command
    from testzeus_hercules.cli.explore_cmd import add_explore_command
    from testzeus_hercules.cli.export_cmd import add_export_command
    from testzeus_hercules.cli.import_cmd import add_import_command
    from testzeus_hercules.cli.scan_cmd import add_scan_command

    add_explore_command(commands)
    add_import_command(commands)
    add_export_command(commands)
    add_scan_command(commands)
    add_cloud_command(commands)
    for subcommand in commands.choices.values():
        # Also accepted after the command (``hercules run --no-telemetry CASE``); SUPPRESS keeps the top-level value.
        subcommand.add_argument("--no-telemetry", action="store_true", default=argparse.SUPPRESS, help=_NO_TELEMETRY_HELP)
    return parser


_NO_TELEMETRY_HELP = "send no usage telemetry from this command or anything it starts (same as HERCULES_TELEMETRY=0)"


def _without_telemetry(env: Mapping[str, str] | None) -> Mapping[str, str] | None:
    """Turn telemetry off for this command.

    With the process environment, it is changed in place so that the daemon,
    workers and other processes this command starts inherit the setting.
    """
    if env is None:
        os.environ["HERCULES_TELEMETRY"] = "0"
        return None
    return {**env, "HERCULES_TELEMETRY": "0"}


def _add_plan_command(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    plan = commands.add_parser(
        "plan", help="make a test case's plan (steps, success criteria, risks) and lint it, without running it", description="Make and lint a test case's plan without running it."
    )
    plan.add_argument("case", type=Path, metavar="CASE", help="path to a TestCase .json file or a .feature file")
    plan.add_argument("--project", type=Path, default=None, metavar="DIR", help="project directory (default: the current directory)")
    plan.add_argument("--mode", choices=("auto", "pure", "hybrid"), default="auto", help="who audits the plan's coverage: hybrid (JEV) or pure (the local model); as for hercules run")
    plan.add_argument("--egress", choices=("local", "hybrid-text", "hybrid"), default=None, help="what may leave the machine (as for hercules run)")
    plan.add_argument("--jev-key-env", type=_env_var_name, default=None, metavar="NAME", help="environment variable holding the JEV (OpenRouter) API key")
    plan.add_argument("--decider", choices=("stub",), default=None, help=argparse.SUPPRESS)
    plan.add_argument("--base-url", default=None, metavar="URL", help="base URL of the app under test (overrides the case's environment)")
    plan.add_argument("--env", default=None, metavar="NAME", help="saved environment (default: the case's environment.ref)")
    plan.add_argument("--json", action="store_true", help="print the plan as JSON (edit it and pass it to hercules run --plan FILE)")
    plan.add_argument("--out", type=Path, default=None, metavar="FILE", help="also write the plan as JSON to FILE")
    plan.add_argument("--approve", action="store_true", help="store the plan as approved for this case version and model")
    plan.add_argument("--strict", action="store_true", default=None, help="exit 1 when the plan has lint errors")
    plan.add_argument("--no-coverage", action="store_true", help="skip the coverage audit")
    plan.add_argument("--fresh", action="store_true", help="make a new plan even when one is stored for this case version and model")


def _add_daemon_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    serve = commands.add_parser("serve", help="run the local daemon: run queue, workers and the HTTP API", description="Run the local Hercules daemon for a project.")
    serve.add_argument("--project", type=Path, default=None, metavar="DIR", help="project directory (default: the current directory)")
    serve.add_argument("--host", default=None, help="address to listen on (default 127.0.0.1; anything else needs --allow-remote)")
    serve.add_argument("--port", type=_port, default=None, help="port to listen on (default 7373 from settings; 0 picks a free port)")
    serve.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow a non-loopback --host; requires HERCULES_DAEMON_TOKEN, a TLS proxy in front, and the hostnames clients use in HERCULES_DAEMON_ALLOWED_HOSTS (comma-separated)",
    )
    serve.add_argument("--parallel", type=_positive_int, default=None, metavar="N", help="runs at a time (default: from the hardware tier, usually 1)")
    serve.add_argument("--fallback-port", action="store_true", help="use a free port when the requested one is busy")
    serve.add_argument("--worker-command", default=None, help=argparse.SUPPRESS)

    worker = commands.add_parser("worker", help="run one queued case (started by the daemon)", description="Run one case for the daemon and stream JSON-lines events on stdout.")
    worker.add_argument("--run-id", default=None, help="run id assigned by the daemon")
    worker.add_argument("--case", type=Path, default=None, metavar="CASE", help="TestCase .json or .feature file (default with --resume: the run's saved case)")
    worker.add_argument("--resume", default=None, metavar="RUN_ID", help="continue this run from its last completed step (after its worker died)")
    worker.add_argument("--project", type=Path, default=None, metavar="DIR", help="project directory (default: the current directory)")
    worker.add_argument("--headed", action="store_true", help="show the browser window")
    worker.add_argument("--mode", choices=("auto", "pure", "hybrid"), default="auto", help="as for hercules run")
    worker.add_argument("--egress", choices=("local", "hybrid-text", "hybrid"), default=None, help="as for hercules run")
    worker.add_argument("--jev-key-env", type=_env_var_name, default=None, metavar="NAME", help="as for hercules run")
    worker.add_argument("--decider", choices=("stub",), default=None, help=argparse.SUPPRESS)
    worker.add_argument("--max-steps", type=_positive_int, default=None, metavar="N", help="as for hercules run")
    worker.add_argument("--step-timeout-s", type=_positive_float, default=None, metavar="S", help="as for hercules run")
    worker.add_argument("--control", action="store_true", help="read pause/resume/approval messages as JSON lines on stdin")
    worker.add_argument("--frames-fd", type=int, default=None, help=argparse.SUPPRESS)
    worker.add_argument("--confirm-destructive", action="store_true", help="ask before clicking elements whose names suggest deleting, paying or transferring")
    worker.add_argument("--destructive-terms", default=None, metavar="TERMS", help="comma-separated words that mark a destructive click (default: a generic list)")
    worker.add_argument("--ask-on-failure", action="store_true", help="when a step's recovery is exhausted, ask whether to retry it")
    worker.add_argument("--a11y", action="store_true", help="audit every page the run reaches for accessibility")
    worker.add_argument("--visual", action="store_true", help="compare every page with its visual baseline and check the layout")
    worker.add_argument("--quality-fail-on", choices=("critical", "serious", "moderate", "minor"), default=None, help="fail the run on audit findings at or above this impact")
    worker.add_argument("--approval-timeout-s", type=_positive_float, default=None, metavar="S", help="how long to wait for an approval (default 600)")
    worker.add_argument("--strict", action="store_true", default=None, help="strict plan mode (as for hercules run)")
    worker.add_argument("--plan-review", choices=("auto", "ask"), default=None, help="ask: wait for the plan's approval before the browser starts")
    worker.add_argument("--plan", type=Path, default=None, metavar="FILE", help="run this plan (checked against the case)")
    worker.add_argument("--plan-only", action="store_true", help="make the case's plan and write it to --plan-out, without running it")
    worker.add_argument("--explore", type=Path, default=None, metavar="JOB", help="run an exploration job (an explorer's or a reproducer's, written by the daemon) instead of the case's steps")
    worker.add_argument("--plan-out", type=Path, default=None, metavar="FILE", help=argparse.SUPPRESS)

    ui = commands.add_parser(
        "ui", help="open the tester UI of the running daemon (prints a one-time sign-in link)", description="Print and open a one-time sign-in link for the running daemon's tester UI."
    )
    ui.add_argument("--print", dest="print_only", action="store_true", help="only print the link; do not open a browser")

    mcp = commands.add_parser("mcp", help="MCP server on stdio for coding agents (talks to the daemon, starting it if needed)", description="Serve the Hercules MCP tools on stdio.")
    mcp.add_argument(
        "--project",
        type=Path,
        default=None,
        metavar="DIR",
        help="project directory (default: the current directory; give it for clients that start servers elsewhere, such as Claude Desktop)",
    )

    init = commands.add_parser(
        "init",
        help="set up a coding agent (Claude Code, Codex, Cursor, Copilot, Gemini CLI) to use Hercules",
        description="Write the MCP configuration, skill and rules that let a coding agent test its changes with Hercules.",
    )
    init.add_argument(
        "--agent",
        required=True,
        choices=("claude", "codex", "cursor", "copilot", "gemini", "generic"),
        help="claude (Claude Code), codex, cursor, copilot (GitHub Copilot in VS Code), gemini (Gemini CLI), or generic (any MCP client: skill and AGENTS.md, config printed)",
    )
    init.add_argument("--project", type=Path, default=None, metavar="DIR", help="project directory (default: the current directory)")
    init.add_argument("--print", dest="print_only", action="store_true", help="print what would be written and change nothing")
    init.add_argument("--force", action="store_true", help="replace existing Hercules files and entries")
    init.add_argument("--write-global", action="store_true", help="codex: also update ~/.codex/config.toml (otherwise the snippet is printed)")
    init.add_argument("--command", dest="server_command", default="hercules", metavar="CMD", help="command that starts Hercules (default: hercules)")


def _add_setup_command(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    setup = commands.add_parser(
        "setup",
        help="guided first-time setup: local model server, OpenRouter key, egress policy, telemetry, coding agents",
        description="Guided first-time setup. Starts and downloads nothing; one tiny JEV request checks the key.",
    )
    setup.add_argument("--yes", "-y", action="store_true", help="answer every question with its default (non-interactive, for CI)")
    setup.add_argument("--project", type=Path, default=None, metavar="DIR", help="project directory (default: the current directory)")
    setup.add_argument("--scope", choices=("global", "project"), default="global", help="write ~/.hercules/config.yaml (global, default) or the project's .hercules/config.yaml")
    setup.add_argument(
        "--provider",
        default=None,
        help="model provider: a local server (ollama, mlx-vlm, llamacpp, lmstudio, vllm; default: detected), openrouter, or openai-compatible with --base-url",
    )
    setup.add_argument("--base-url", default=None, metavar="URL", help="model endpoint base URL (default: the provider's; required for openai-compatible)")
    setup.add_argument("--model", default=None, help="model id or alias (default: auto, from the hardware tier; a hosted endpoint's own model id)")
    setup.add_argument(
        "--api-key-env",
        default=None,
        metavar="NAME",
        help="the variable holding a hosted endpoint's key (default: the provider's, or HERCULES_LLM_API_KEY); only the name is saved, so set it wherever Hercules runs, or save the key with --llm-key-stdin",
    )
    setup.add_argument("--llm-key-stdin", action="store_true", help="read the hosted model endpoint's key from the first line of standard input and save it (never echoed)")
    setup.add_argument("--egress", choices=("hybrid", "hybrid-text", "local"), default=None, help="egress policy (default: hybrid when a key is configured)")
    setup.add_argument("--telemetry", choices=("on", "off"), default=None, help="anonymous usage telemetry (default: on)")
    setup.add_argument("--key-stdin", action="store_true", help="read the OpenRouter key from the first line of standard input and save it (never echoed)")
    setup.add_argument("--save-key", action="store_true", help="save the key found in the environment to ~/.hercules/openrouter.env (mode 600)")
    setup.add_argument("--no-key-check", action="store_true", help="do not send the tiny JEV request that checks the key")
    setup.add_argument(
        "--agents",
        default=None,
        metavar="LIST",
        help="coding agents to connect without asking: comma-separated claude, codex, cursor, copilot, gemini, generic, or none " "(default: ask about each agent found; with --yes, none)",
    )
    setup.add_argument("--clef-url", default=None, metavar="URL", help="a local Clef server (for example http://127.0.0.1:30000) that decides in pure mode")


def _port(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a port number, got {text!r}") from None
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError("must be between 0 and 65535")
    return value


def _env_var_name(text: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        raise argparse.ArgumentTypeError(f"expected an environment variable name such as OPENROUTER_API_KEY, got {text!r}")
    return text


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number of seconds, got {text!r}") from None
    if not value > 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def _quality(args: argparse.Namespace) -> Any:
    """Run-level audit options from ``--a11y``/``--visual``/``--quality-fail-on`` (imported lazily to keep start-up fast)."""
    from testzeus_hercules.engine.quality import RunQualityOptions

    if args.quality_fail_on and not (args.a11y or args.visual):
        raise SystemExit("hercules: error: --quality-fail-on needs --a11y or --visual")
    return RunQualityOptions.from_flags(args.a11y, args.visual, args.quality_fail_on)


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    doctor_deps: DoctorDeps | None = None,
    run_deps: RunCommandDeps | None = None,
    setup_deps: SetupDeps | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the ``hercules`` command and return its exit code."""
    out = stdout if stdout is not None else sys.stdout
    from testzeus_hercules.cli.parser import ERROR_ENV

    parser = build_parser()
    token = ERROR_ENV.set(env)
    try:
        args = parser.parse_args(argv)
    finally:
        ERROR_ENV.reset(token)
    if args.no_telemetry:
        env = _without_telemetry(env)
    if args.command not in (None, "setup"):
        from testzeus_hercules.cli.first_run import show_telemetry_notice_once

        show_telemetry_notice_once(args.command, os.environ if env is None else env, stderr if stderr is not None else sys.stderr)

    if args.command == "setup":
        from testzeus_hercules.cli.setup import (
            SetupDeps,
            run_setup,
            setup_options_from_args,
        )

        return run_setup(setup_options_from_args(args), env=env, deps=setup_deps or SetupDeps(), out=out)

    if args.command == "doctor":
        from testzeus_hercules.cli.doctor import DoctorDeps, DoctorOptions, run_doctor

        options = DoctorOptions(project_dir=args.project, json_output=args.json, use_cache=not args.no_cache)
        return run_doctor(options, env=env, deps=doctor_deps or DoctorDeps(), out=out)
    if args.command == "run":
        from testzeus_hercules.cli.run import RunOptions, run_command

        run_options = RunOptions(
            case_path=args.case,
            project_dir=args.project,
            headed=args.headed,
            mode=args.mode,
            egress=args.egress,
            jev_key_env=args.jev_key_env,
            decider=args.decider,
            json_output=args.json,
            base_url=args.base_url,
            environment=args.env,
            allow_draft=args.allow_draft,
            quality=_quality(args),
            max_steps=args.max_steps,
            step_timeout_s=args.step_timeout_s,
            strict=args.strict,
            plan_path=args.plan,
        )
        return run_command(run_options, env=env, deps=run_deps, out=out, err=stderr)
    if args.command == "plan":
        from testzeus_hercules.cli.plan_cmd import options_from_args, plan_command

        return plan_command(options_from_args(args), env=env, deps=run_deps, out=out, err=stderr if stderr is not None else sys.stderr)
    if args.command == "ui":
        from testzeus_hercules.cli.ui import UiOptions, run_ui

        return run_ui(UiOptions(print_only=args.print_only), env=env, out=out, err=stderr)
    if args.command == "import":
        from testzeus_hercules.cli.import_cmd import run_import

        return run_import(args, env=env, out=out, err=stderr if stderr is not None else sys.stderr)
    if args.command == "export":
        from testzeus_hercules.cli.export_cmd import run_export

        return run_export(args, env=env, out=out, err=stderr if stderr is not None else sys.stderr)
    if args.command == "scan":
        from testzeus_hercules.cli.scan_cmd import run_scan_command

        return run_scan_command(args, env=env, out=out, err=stderr if stderr is not None else sys.stderr)
    if args.command == "explore":
        from testzeus_hercules.cli.explore_cmd import run_explore_command

        return run_explore_command(args, env=env, out=out, err=stderr if stderr is not None else sys.stderr)
    if args.command == "cloud":
        from testzeus_hercules.cli.cloud_cmd import run_cloud_command

        return run_cloud_command(args, env=env, out=out, err=stderr if stderr is not None else sys.stderr)
    if args.command in ("serve", "worker", "mcp", "init"):
        return _run_daemon_command(args, env=env, out=out, err=stderr)
    if args.command == "completion":
        from testzeus_hercules.cli.completion import completion_script

        out.write(completion_script(parser, args.shell))
        return 0
    if args.command == "version":
        from testzeus_hercules.cli.version import package_version

        out.write(f"Hercules {package_version()} by TestZeus\n")
        return 0
    parser.print_help(out)
    return 2


def _run_daemon_command(args: argparse.Namespace, *, env: Mapping[str, str] | None, out: TextIO, err: TextIO | None) -> int:
    if args.command == "mcp":
        from testzeus_hercules.mcp.shim import main as mcp_main

        return mcp_main(env=env, project_dir=args.project)
    if args.command == "serve":
        from testzeus_hercules.cli.serve import ServeOptions, run_serve

        options = ServeOptions(
            project_dir=args.project,
            host=args.host,
            port=args.port,
            allow_remote=args.allow_remote,
            parallel=args.parallel,
            fallback_port=args.fallback_port,
            worker_command=args.worker_command,
        )
        return run_serve(options, env=env, err=err)
    if args.command == "worker":
        from testzeus_hercules.cli.worker import WorkerOptions, run_worker

        run_id = args.resume or args.run_id
        if run_id is None or (args.case is None and not args.resume) or (args.resume and args.run_id and args.run_id != args.resume):
            (err if err is not None else sys.stderr).write("hercules worker: give --run-id and --case, or --resume RUN_ID\n")
            return 2
        terms = tuple(term.strip() for term in args.destructive_terms.split(",") if term.strip()) if args.destructive_terms else None
        worker = WorkerOptions(
            run_id=run_id,
            case_path=args.case,
            resume=bool(args.resume),
            control=args.control,
            frames_fd=args.frames_fd,
            confirm_destructive=args.confirm_destructive,
            destructive_terms=terms,
            ask_on_failure=args.ask_on_failure,
            approval_timeout_s=args.approval_timeout_s,
            quality=_quality(args),
            project_dir=args.project,
            headed=args.headed,
            mode=args.mode,
            egress=args.egress,
            jev_key_env=args.jev_key_env,
            decider=args.decider,
            max_steps=args.max_steps,
            step_timeout_s=args.step_timeout_s,
            strict=args.strict,
            plan_review=args.plan_review,
            plan_path=args.plan,
            plan_only=args.plan_only,
            plan_out=args.plan_out,
            explore_path=args.explore,
        )
        return run_worker(worker, env=env, err=err)
    from testzeus_hercules.cli.init import InitOptions, run_init

    init = InitOptions(agent=args.agent, project_dir=args.project, print_only=args.print_only, force=args.force, write_global=args.write_global, command=args.server_command)
    return run_init(init, env=env, out=out)


if __name__ == "__main__":
    sys.exit(main())
