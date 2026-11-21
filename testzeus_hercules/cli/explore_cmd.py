"""``hercules explore``: start an exploration session in the daemon, follow it, steer it, and triage what it found.

Exit codes of ``start`` (when it waits for the end): 0 no new findings at the gate severity, 1 new findings at or above
it, 2 the session could not start, 3 the budget ran out before recon finished. No findings is not a pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

__all__ = ["add_explore_command", "parse_duration", "run_explore_command"]

_DURATION = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?$")
_DAEMON_START_TIMEOUT_S = 60.0
_ACTIONS_HELP = "start, sessions, status, report, stop, size, charter, findings, show, triage, issue, reverify"
_STATES = ("new", "confirmed", "in_tracker", "verify_fixed", "closed", "duplicate", "by_design", "wont_fix", "known_issue", "cannot_reproduce")
_CASES = ("proposed", "kept", "regression", "rejected")
_LATEST = "last"
_SESSION_HELP = "the session id, a unique start of it, or `last` (default: the latest session)"
_HEARTBEAT_S = 60.0


def parse_duration(text: str) -> int:
    """``1h``, ``30m``, ``90s``, ``1h30m``; a bare number is minutes. At least a minute."""
    cleaned = text.strip().lower()
    if cleaned.isdigit():
        cleaned += "m"
    found = _DURATION.match(cleaned)
    if not found or not any(found.groups()):
        raise argparse.ArgumentTypeError(f"{text!r} is not a duration: use 30m, 1h, 1h30m or 90s (a bare number is minutes)")
    hours, minutes, seconds = (int(part or 0) for part in found.groups())
    total = hours * 3600 + minutes * 60 + seconds
    if total < 60:
        raise argparse.ArgumentTypeError(f"{text!r} is under a minute: explore for at least 1m")
    return total


def _list(text: str) -> list[str]:
    return [item.strip() for item in text.split(",") if item.strip()]


def add_explore_command(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser(
        "explore",
        help="explore an app for a set time with a mission and a swarm of explorers you size; report bugs with failing cases",
        description="Exploratory testing: from a starting state, explore for a set time with a mission (find P1+ bugs, ...), with the number of explorers you choose. Every bug comes with a failing case.",
    )
    actions = parser.add_subparsers(dest="explore_action", metavar="<action>", required=True, help=_ACTIONS_HELP)
    start = actions.add_parser("start", help="start a session (and follow it to the end unless --no-wait)", description="Start an exploration session in the daemon.")
    start.add_argument("--from", dest="start_case", default=None, metavar="CASE", help="a case that reaches the starting state (for example signed in)")
    start.add_argument("--env", dest="environment", default=None, metavar="NAME", help="a saved environment (.hercules/envs/NAME.json)")
    start.add_argument("--base-url", default=None, metavar="URL", help="the app's base URL (default: the environment's or the case's)")
    start.add_argument("--start-url", default=None, metavar="URL", help="where explorers begin (default: where the start case ends)")
    start.add_argument("-m", "--mission", default="p1", help="p1, forms, changed, smoke-plus, a11y, conditions, persona:<name>, or a project mission (default p1)")
    start.add_argument("--prompt", default=None, metavar="TEXT", help="your own target for the mission, replacing the preset's")
    start.add_argument(
        "-t",
        "--time",
        dest="time_s",
        type=parse_duration,
        default=None,
        metavar="DURATION",
        help="how long to explore: 30m, 1h, 1h30m; a bare number is minutes (default: explore.default_time_s, 30m)",
    )
    start.add_argument("-w", "--workers", type=int, default=None, metavar="N", help="how many explorers run at once (default: explore.workers); Hercules never changes it")
    start.add_argument("--reproducers", type=int, default=None, metavar="N", help="how many reproducers run at once (default: explore.reproducers)")
    start.add_argument("--allow-more-workers", action="store_true", help="start even when the size exceeds this machine's guard rail")
    start.add_argument("--levers", type=_list, default=None, metavar="LIST", help="inputs,order,conditions,time,people (default: all)")
    start.add_argument("--persona", dest="personas", action="append", default=[], metavar="NAME", help="add charters for a persona: impatient, careful, confused, screen-reader (repeatable)")
    start.add_argument("--forbid", type=_list, default=[], metavar="LIST", help="more actions never to do, by words in their name (pay, delete account, send ... are always forbidden)")
    start.add_argument("--allow", type=_list, default=[], metavar="LIST", help="default forbidden words this session may use anyway")
    start.add_argument("--allow-hosts", type=_list, default=[], metavar="LIST", help="hosts explorers may reach (default: the base URL's)")
    start.add_argument("--deny-paths", type=_list, default=[], metavar="LIST", help="paths never opened or posted to, e.g. /logout,/admin/billing")
    start.add_argument("--ask", action="store_true", help="ask a person before a forbidden action instead of skipping it")
    start.add_argument("--severity-floor", choices=("P0", "P1", "P2", "P3"), default=None, help="what counts as a finding for the exit code and --target (default: the mission's)")
    start.add_argument(
        "--gate-on",
        choices=("confirmed", "proposed-hard"),
        default="confirmed",
        help="exit 1 only on severities a person confirmed (default), or also on proposed ones backed by code or specified promises",
    )
    start.add_argument("--target", type=int, default=None, metavar="N", help="stop once N findings at or above the floor are found")
    start.add_argument("--max-cost", type=float, default=None, metavar="USD", help="stop when model spend reaches this")
    start.add_argument("--isolation", choices=("serial-writes", "partition", "accounts", "single"), default="serial-writes", help="how explorers avoid each other's data (default serial-writes)")
    start.add_argument("--accounts", type=_list, default=[], metavar="VARS", help="accounts isolation: one environment variable per explorer, given to the start case as {{explore_account}}")
    start.add_argument("--promises", type=Path, default=None, metavar="FILE", help="specified promises (YAML list), added to .hercules/explore/promises.yaml")
    start.add_argument("--changed", type=_list, default=[], metavar="LIST", help="the changed mission: changed routes or paths")
    start.add_argument("--since", default=None, metavar="REF", help="the changed mission: the files changed since this git ref (read here, never by the daemon)")
    start.add_argument("--build", default=None, help="the app build the findings carry (default: --build-header, else the response's ETag)")
    start.add_argument("--build-header", default=None, metavar="NAME", help="the response header holding the app's build")
    start.add_argument("--teardown", dest="teardown_case", default=None, metavar="CASE", help="a case run at the end to clean up what explorers created")
    start.add_argument("--reproduce-runs", type=int, default=3, metavar="N", help="recorded replays per clue (N-1 must hit; default 3)")
    start.add_argument("--i-own-this-environment", dest="production_ok", action="store_true", help="explore a host that does not look like a test environment")
    start.add_argument("--dry-run", action="store_true", help="recon only: print the charters a full session would start with")
    start.add_argument("--no-wait", dest="wait", action="store_false", help="print the session id and return")
    start.add_argument("--yes", "-y", action="store_true", help="start without showing the estimate first")
    start.add_argument("--json", action="store_true", help="print the final session as JSON")
    sessions = actions.add_parser("sessions", help="list exploration sessions, newest first", description="List this project's exploration sessions, newest first.")
    sessions.add_argument("--limit", type=int, default=10, metavar="N", help="how many (default 10)")
    sessions.add_argument("--json", action="store_true", help="print JSON")
    for name, text in (("status", "a session's progress"), ("report", "a session's report (Markdown, or --json)"), ("stop", "stop a session (explorers stop, reproductions get a grace period)")):
        action = actions.add_parser(name, help=text, description=text[0].upper() + text[1:] + ".")
        action.add_argument("session", metavar="SESSION", nargs="?", default=_LATEST, help=_SESSION_HELP)
        action.add_argument("--json", action="store_true", help="print JSON")
    size = actions.add_parser("size", help="change a running session's swarm size", description="Change how many explorers or reproducers a running session uses.")
    size.add_argument("session", metavar="SESSION", nargs="?", default=_LATEST, help=_SESSION_HELP)
    size.add_argument("-w", "--workers", type=int, default=None, metavar="N", help="explorers at once from now on")
    size.add_argument("--reproducers", type=int, default=None, metavar="N", help="reproducers at once from now on")
    size.add_argument("--allow-more-workers", action="store_true", help="change it even when the size exceeds this machine's guard rail")
    charter = actions.add_parser("charter", help="add, veto, pin or annotate a charter of a running session", description="Steer a running session's charters.")
    charter.add_argument("session", metavar="SESSION", nargs="?", default=_LATEST, help=_SESSION_HELP)
    charter.add_argument("--add", default=None, metavar="TITLE", help="add a charter (with --area and --levers)")
    charter.add_argument("--area", default="/", help="the route the added charter explores")
    charter.add_argument("--levers", type=_list, default=[], metavar="LIST", help="the added charter's levers")
    charter.add_argument("--pin", default=None, metavar="CHARTER", help="run this queued charter next")
    charter.add_argument("--veto", default=None, metavar="CHARTER", help="never run this queued charter")
    charter.add_argument("--note", nargs=2, default=None, metavar=("CHARTER", "TEXT"), help="add a note to a charter")
    findings = actions.add_parser("findings", help="the project's findings (the ledger), with their triage state", description="List the project's findings.")
    findings.add_argument(
        "--state", choices=(*_STATES, "open", "all"), default=None, help="only findings in this state; open = new, confirmed, in_tracker, verify_fixed; all (the default) = every state"
    )
    findings.add_argument("--case", choices=_CASES, default=None, help="only findings whose case has this decision")
    findings.add_argument("--json", action="store_true", help="print JSON")
    show = actions.add_parser("show", help="one finding: what happened, steps, evidence, case and history", description="Show one finding in full.")
    show.add_argument("finding", metavar="FINDING", help="the finding id, or a unique start of it")
    show.add_argument("--json", action="store_true", help="print JSON")
    triage = actions.add_parser(
        "triage",
        help="decide a finding: confirm it, dismiss it (by_design, wont_fix, cannot_reproduce, duplicate), set severity or its case (audited)",
        description="Triage a finding. Every change is recorded with who made it and why. Confirming keeps its case; dismissing rejects it; closing a verified fix makes it a regression test.",
    )
    triage.add_argument("finding", metavar="FINDING", help="the finding id, or a unique start of it")
    triage.add_argument("--by", default=None, help="who is triaging (default: git user.name)")
    triage.add_argument("--state", choices=_STATES, default=None, help="the new state; dismissing is by_design, wont_fix, cannot_reproduce or duplicate (with --reason)")
    triage.add_argument("--severity", choices=("P0", "P1", "P2", "P3"), default=None, help="the severity you confirm (how bad)")
    triage.add_argument("--priority", choices=("P0", "P1", "P2", "P3"), default=None, help="the priority you set (how soon)")
    triage.add_argument("--tracker", default=None, metavar="KEY", help="the issue's key in your tracker (moves it to in_tracker)")
    triage.add_argument("--duplicate-of", default=None, metavar="FINDING", help="with --state duplicate: the finding it duplicates")
    triage.add_argument(
        "--case",
        choices=_CASES,
        default=None,
        help="what to do with its case: kept (a known_bug case in .hercules/cases), regression (a case that must pass), rejected (never run)",
    )
    triage.add_argument("--reason", default="", help="why (required to dismiss or reject a case)")
    triage.add_argument("--json", action="store_true", help="print the finding as JSON")
    issue = actions.add_parser("issue", help="a finding as an issue for GitHub, Jira or Linear", description="Print a finding as an issue (or a prefilled GitHub URL with --repository).")
    issue.add_argument("finding", metavar="FINDING", help="the finding id, or a unique start of it")
    issue.add_argument("--tracker", choices=("github", "jira", "linear", "markdown"), default=None, help="the issue format (default markdown; github with --repository)")
    issue.add_argument("--repository", default=None, metavar="OWNER/NAME", help="github: print a prefilled new-issue URL")
    issue.add_argument("--json", action="store_true", help="print the issue as JSON")
    reverify = actions.add_parser(
        "reverify",
        help="rerun the kept and regression cases on the current build",
        description="Rerun the explore cases a person kept: a kept (known_bug) case that passes moves its finding to verify_fixed; "
        "a regression case that fails reopens its finding and exits 1. A run that ends any other way (error, approval, cancelled, "
        "out of time) is inconclusive, changes nothing and exits 2.",
    )
    reverify.add_argument("--build", default="current", help="the build being verified (recorded with the result)")
    reverify.add_argument("--finding", action="append", default=None, metavar="FINDING", help="only this finding (id or a unique prefix); repeat for more")
    reverify.add_argument("--timeout", type=float, default=900, metavar="SECONDS", help="each case's time limit; a case over it is inconclusive (default 900)")
    reverify.add_argument("--json", action="store_true", help="print the result as JSON (progress goes to standard error)")
    for action in actions.choices.values():
        action.add_argument("--project", type=Path, default=None, metavar="DIR", help="project directory (default: the current directory)")


def _changed_since(ref: str, project: Path) -> list[str]:
    try:
        output = subprocess.run(["git", "diff", "--name-only", f"{ref}...HEAD"], cwd=project, capture_output=True, text=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"cannot list the files changed since {ref}: {exc}") from None
    return [line.strip() for line in output.splitlines() if line.strip()][:200]


def _spec(args: argparse.Namespace, project: Path, settings: Any) -> dict[str, Any]:
    import yaml  # type: ignore[import-untyped]

    from testzeus_hercules.explore.missions import load_mission

    mission = load_mission(args.mission, args.prompt, project)
    promises: list[Any] = []
    if args.promises is not None:
        data = yaml.safe_load(args.promises.read_text(encoding="utf-8")) or []
        promises = data.get("promises", []) if isinstance(data, dict) else data
    changed = list(args.changed) + (_changed_since(args.since, project) if args.since else [])
    spec: dict[str, Any] = {
        "mission": args.mission,
        "prompt": args.prompt,
        "start_case": args.start_case,
        "environment": args.environment,
        "base_url": args.base_url,
        "start_url": args.start_url,
        "budget": {"time_s": args.time_s or settings.explore.default_time_s, "max_cost_usd": args.max_cost, "target_findings": args.target},
        "scope": {"allow_hosts": args.allow_hosts, "deny_paths": args.deny_paths, "forbid": args.forbid, "allow": args.allow},
        "workers": args.workers if args.workers is not None else (mission.suggested_workers or settings.explore.workers),
        "reproducers": args.reproducers if args.reproducers is not None else settings.explore.reproducers,
        "allow_more_workers": args.allow_more_workers,
        "levers": args.levers or list(mission.levers),
        "personas": args.personas,
        "severity_floor": args.severity_floor or mission.severity_floor,
        "gate_on": args.gate_on,
        "reproduce_runs": args.reproduce_runs,
        "isolation": args.isolation,
        "accounts": args.accounts,
        "promises": promises,
        "changed_paths": changed,
        "build": args.build,
        "build_header": args.build_header,
        "teardown_case": args.teardown_case,
        "ask_before_forbidden": args.ask,
        "production_ok": args.production_ok,
        "dry_run": args.dry_run,
    }
    return {key: value for key, value in spec.items() if value is not None}


def run_explore_command(args: argparse.Namespace, *, env: Mapping[str, str] | None = None, out: TextIO, err: TextIO, ask: Any = input) -> int:
    import os

    from testzeus_hercules.kernel.settings import Settings, SettingsError

    environ = dict(os.environ if env is None else env)
    project = (args.project or Path.cwd()).resolve()
    try:
        settings = Settings.load(project, env=environ)
    except SettingsError as exc:
        err.write(f"hercules explore: {exc}\n")
        return 2
    try:
        return asyncio.run(_run(args, project, settings, environ, out, err, ask))
    except KeyboardInterrupt:
        session = getattr(args, "followed_session", None) or "last"
        err.write(f"hercules explore: stopped following; the session keeps running. Follow it again with `hercules explore status {session}`, stop it with `hercules explore stop {session}`\n")
        return 130


async def _run(args: argparse.Namespace, project: Path, settings: Any, env: Mapping[str, str], out: TextIO, err: TextIO, ask: Any) -> int:
    from testzeus_hercules.mcp.daemon_client import DaemonClient, DaemonUnavailable

    # A person at a terminal can wait for a cold daemon start; the 8 s default is for MCP clients that give up sooner.
    client = DaemonClient(env=env, project_dir=project, start_timeout_s=_DAEMON_START_TIMEOUT_S)
    try:
        return await _action(args, client, project, settings, out, err, ask)
    except DaemonUnavailable as exc:
        if args.explore_action in _OFFLINE:
            err.write("hercules explore: the daemon is busy with another project; reading this project's files instead\n")
            return _offline(args, project, out, err)
        err.write(f"hercules explore: the daemon is not available: {exc}\n")
        return 2
    finally:
        await client.aclose()


_OFFLINE = ("findings", "show", "sessions", "report", "status", "issue")


def _offline(args: argparse.Namespace, project: Path, out: TextIO, err: TextIO) -> int:
    """Read-only actions from the project's files, for when the daemon serves another project (or is not running)."""
    from testzeus_hercules.explore.issues import issue_text, issue_title
    from testzeus_hercules.explore.ledger import Ledger

    explore_dir = project / ".hercules" / "explore"
    ledger = Ledger(explore_dir)
    action = args.explore_action
    if action == "findings":
        findings = [json.loads(item.model_dump_json()) for item in ledger.all()]
        return _print_findings(args, {"findings": findings, "precision": ledger.precision()}, out)
    if action in ("show", "issue"):
        found, problem = _find_offline(ledger, args.finding)
        if found is None:
            err.write(f"hercules explore: {problem}\n")
            return 2
        if action == "show":
            return _print_finding(args, json.loads(found.model_dump_json()), out)
        tracker = args.tracker or "markdown"
        out.write(f"{issue_title(found)}\n\n{issue_text(found, tracker)}")
        return 0
    views = _disk_sessions(explore_dir)
    if action == "sessions":
        return _print_sessions(args, views, out)
    view, problem = _pick_session(views, args.session)
    if view is None:
        err.write(f"hercules explore: {problem}\n")
        return 2
    if action == "status":
        out.write(json.dumps(view, indent=1) + "\n" if args.json else _status_line(view) + "\n")
        return 0
    report = explore_dir / view["id"] / ("report.json" if args.json else "report.md")
    if not report.is_file():
        err.write(f"hercules explore: {view['id']} has no report yet (it is written when the session ends)\n")
        return 2
    out.write(report.read_text(encoding="utf-8"))
    return 0


def _find_offline(ledger: Any, text: str) -> tuple[Any, str]:
    matches = [item for item in ledger.all() if item.id.startswith(text.strip())]
    if len(matches) == 1:
        return matches[0], ""
    if not matches:
        return None, f"no finding {text!r} (list them with `hercules explore findings`)"
    return None, f"{text!r} matches {len(matches)} findings ({', '.join(item.id for item in matches[:5])}): give more of the id"


def _disk_sessions(explore_dir: Path) -> list[dict[str, Any]]:
    views = []
    for path in sorted(explore_dir.glob("x_*/session.json"), reverse=True)[:200]:
        try:
            views.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return views


def _pick_session(views: list[dict[str, Any]], text: str | None) -> tuple[dict[str, Any] | None, str]:
    """``last`` (or nothing) is the newest session; otherwise a unique start of its id."""
    if not views:
        return None, "no exploration sessions in this project yet: start one with `hercules explore start --base-url URL`"
    if not text or text == _LATEST:
        return max(views, key=lambda view: str(view.get("started_at") or view.get("id"))), ""
    exact = [view for view in views if view["id"] == text]
    matches = exact or [view for view in views if str(view["id"]).startswith(text)]
    if len(matches) == 1:
        return matches[0], ""
    if not matches:
        return None, f"no session {text!r} (list them with `hercules explore sessions`)"
    return None, f"{text!r} matches {len(matches)} sessions ({', '.join(view['id'] for view in matches[:5])}): give more of the id"


async def _session_id(client: Any, text: str | None, err: TextIO) -> str | None:
    """Resolve ``last`` or a short id against the daemon's sessions."""
    if text and text.startswith("x_") and len(text) >= 24:
        return text
    data = await _call(client, "GET", "/api/explore", err)
    if data is None:
        return None
    view, problem = _pick_session(list(data.get("sessions") or []), text)
    if view is None:
        err.write(f"hercules explore: {problem}\n")
        return None
    return str(view["id"])


async def _call(client: Any, method: str, path: str, err: TextIO, **kwargs: Any) -> Any:
    response = await client.request(method, path, **kwargs)
    try:
        data = response.json()
    except ValueError:
        data = response.text
    if response.status_code >= 400:
        message = data.get("error") if isinstance(data, dict) else str(data)
        err.write(f"hercules explore: {_flag_words(str(message))}\n")
        return None
    return data


# The API's field names as the CLI's flags, so an error names what the user typed.
_FLAG_WORDS = (
    (re.compile(r"<root>: (?:Value error, )?"), ""),
    (re.compile(r"budget\.time_s: Input should be greater than or equal to (\d+)"), "--time must be at least 1m"),
    (re.compile(r"budget\.max_cost_usd"), "--max-cost"),
    (re.compile(r"\(duplicate_of\)"), "(--duplicate-of FINDING)"),
    (re.compile(r"\(reason\)"), "(--reason)"),
    (re.compile(r"\(case kept\)"), "(--case kept)"),
    (re.compile(r"\(state closed\)"), "(--state closed)"),
    (re.compile(r"give workers and/or reproducers"), "give --workers and/or --reproducers"),
    (re.compile(r"\bproduction_ok\b"), "--i-own-this-environment"),
)


def _flag_words(message: str) -> str:
    for pattern, words in _FLAG_WORDS:
        message = pattern.sub(words, message)
    return message


async def _action(args: argparse.Namespace, client: Any, project: Path, settings: Any, out: TextIO, err: TextIO, ask: Any) -> int:
    action = args.explore_action
    if action == "start":
        try:
            spec = _spec(args, project, settings)
        except (ValueError, OSError) as exc:
            err.write(f"hercules explore: {exc}\n")
            return 2
        check = await _call(client, "POST", "/api/explore/preflight", err, json=spec)
        if check is None:
            return 2
        estimate = check["estimate"]
        scope = "recon only (a dry run); a full session would be" if spec.get("dry_run") else "Swarm:"
        out.write(
            f"{scope} {spec['workers']} explorer(s), {spec['reproducers']} reproducer(s) for {estimate['time_s'] // 60} min, plus a few minutes to reproduce the last clues · "
            f"about {estimate['decisions']} model decisions, ~${estimate['cost_usd']:.2f}\n"
        )
        if estimate.get("note"):
            out.write(f"  note: {estimate['note']}\n")
        for problem in check["problems"]:
            out.write(f"  over the guard rail: {problem}\n")
        if check["problems"] and not spec.get("allow_more_workers"):
            err.write("hercules explore: lower --workers/--reproducers, or pass --allow-more-workers\n")
            return 2
        if check.get("host_problem") and not spec.get("production_ok"):
            err.write(f"hercules explore: {check['host_problem']}\n  If this host is yours to test, pass --i-own-this-environment\n")
            return 2
        if not args.yes and sys.stdin.isatty():
            if str(ask("Start? [Y/n] ")).strip().lower() not in ("", "y", "yes"):
                return 0
        view = await _call(client, "POST", "/api/explore", err, json={**spec, "by": "hercules explore (CLI)"})
        if view is None:
            return 2
        args.followed_session = view["id"]
        out.write(f"Session {view['id']} started. Watch it live in the tester UI (`hercules ui`, Explore), or: hercules explore status {view['id']}\n")
        if not args.wait:
            return 0
        return await _follow(client, view["id"], out, err, as_json=args.json, dry_run=bool(spec.get("dry_run")))
    if action == "sessions":
        data = await _call(client, "GET", "/api/explore", err)
        if data is None:
            return 2
        return _print_sessions(args, list(data.get("sessions") or []), out)
    if action in ("status", "report", "stop", "size", "charter"):
        session = await _session_id(client, args.session, err)
        if session is None:
            return 2
        args.session = session
    if action == "status":
        view = await _call(client, "GET", f"/api/explore/{args.session}", err)
        if view is None:
            return 2
        out.write(json.dumps(view, indent=1) + "\n" if args.json else _status_line(view) + "\n")
        if not args.json and view["status"] in ("finished", "failed", "stopped"):
            _next_steps(await _current_states(client, view), out)
        return 0
    if action == "report":
        response = await client.request("GET", f"/api/explore/{args.session}/report", params={"format": "json" if args.json else "md"})
        if response.status_code >= 400:
            err.write(f"hercules explore: {response.json().get('error')}\n")
            return 2
        out.write(response.text if not args.json else json.dumps(response.json(), indent=1) + "\n")
        return 0
    if action == "stop":
        before = await _call(client, "GET", f"/api/explore/{args.session}", err)
        if before is None:
            return 2
        if before["status"] in ("finished", "failed", "stopped"):
            out.write(f"Session {args.session} already {before['status']}.\n")
            return 0
        if await _call(client, "POST", f"/api/explore/{args.session}/stop", err, json={"by": "hercules explore (CLI)"}) is None:
            return 2
        out.write(f"Stopping {args.session}: explorers stop now; clues already found get a short grace period to reproduce. Follow it: hercules explore status {args.session}\n")
        return 0
    if action == "size":
        view = await _call(
            client,
            "PATCH",
            f"/api/explore/{args.session}",
            err,
            json={"workers": args.workers, "reproducers": args.reproducers, "allow_more_workers": args.allow_more_workers, "by": "hercules explore (CLI)"},
        )
        if view is None:
            return 2
        if getattr(args, "json", False):
            out.write(json.dumps(view, indent=1) + "\n")
        else:
            out.write(f"Session {args.session}: {view['workers']} explorer(s), {view['reproducers']} reproducer(s) from the next charter on\n")
        return 0
    if action == "charter":
        return await _charter(args, client, out, err)
    if action == "findings":
        params = {key: value for key, value in (("state", args.state), ("case", args.case)) if value and value not in ("open", "all")}
        data = await _call(client, "GET", "/api/explore/findings", err, params=params or None)
        if data is None:
            return 2
        return _print_findings(args, data, out)
    if action == "show":
        finding = await _call(client, "GET", f"/api/explore/findings/{args.finding}", err)
        if finding is None:
            return 2
        return _print_finding(args, finding, out)
    if action == "triage":
        by = args.by or await asyncio.to_thread(_default_triager, project)
        if not by:
            err.write("hercules explore: say who is triaging with --by NAME (no git user.name is set)\n")
            return 2
        body = {
            "by": by,
            "state": args.state,
            "severity": args.severity,
            "priority": args.priority,
            "tracker": args.tracker,
            "duplicate_of": args.duplicate_of,
            "case": args.case,
            "reason": args.reason,
        }
        finding = await _call(
            client, "POST", f"/api/explore/findings/{args.finding}/triage", err, json={key: value for key, value in body.items() if value not in (None, "") or key in ("by", "reason")}
        )
        if finding is None:
            return 2
        if args.json:
            out.write(json.dumps(finding, indent=1) + "\n")
            return 0
        out.write(
            f"{finding['id']}: {finding['state']}, severity {finding['severity'] or finding['proposed_severity'] + ' (proposed)'}, "
            f"case {finding.get('case', 'proposed')}{' at ' + finding['case_path'] if finding.get('case_path') else ''} (by {by})\n"
        )
        _triage_hint(finding, out)
        return 0
    if action == "issue":
        tracker = args.tracker or ("github" if args.repository else "markdown")
        if args.repository and tracker != "github":
            err.write("hercules explore: --repository makes a GitHub new-issue link; use it with --tracker github\n")
            return 2
        params = {"tracker": tracker, **({"repository": args.repository} if args.repository else {})}
        data = await _call(client, "GET", f"/api/explore/findings/{args.finding}/issue", err, params=params)
        if data is None:
            return 2
        if args.json:
            out.write(json.dumps(data, indent=1) + "\n")
            return 0
        out.write(f"{data['title']}\n\n{data['body']}")
        if data.get("url"):
            out.write(f"\nOpen it: {data['url']}\n")
        return 0
    if action == "reverify":
        return await _reverify(args, client, project, out, err)
    return 2


async def _charter(args: argparse.Namespace, client: Any, out: TextIO, err: TextIO) -> int:
    session = args.session
    if args.add:
        result = await _call(client, "POST", f"/api/explore/{session}/charters", err, json={"title": args.add, "area": args.area, "levers": args.levers, "by": "hercules explore (CLI)"})
    elif args.pin or args.veto:
        result = await _call(client, "POST", f"/api/explore/{session}/charters/{args.pin or args.veto}", err, json={"action": "pin" if args.pin else "veto", "by": "hercules explore (CLI)"})
    elif args.note:
        result = await _call(client, "POST", f"/api/explore/{session}/charters/{args.note[0]}", err, json={"action": "note", "note": args.note[1], "by": "hercules explore (CLI)"})
    else:
        err.write("hercules explore charter: give --add, --pin, --veto or --note\n")
        return 2
    if result is None:
        return 2
    out.write(f"{result['id']}: {result['title']} ({result['status']})\n")
    return 0


async def _follow(client: Any, session: str, out: TextIO, err: TextIO, *, as_json: bool, dry_run: bool = False) -> int:
    since = 0
    loop = asyncio.get_running_loop()
    quiet_since = loop.time()
    while True:
        view = await _call(client, "GET", f"/api/explore/{session}", err, params={"wait_s": 30, "since": since}, timeout_s=45)
        if view is None:
            return 2
        printed = False
        for event in view.get("events", []):
            line = _event_line(event)
            if line:
                out.write(line + "\n")
                printed = True
        since = view.get("last_seq", since)
        if printed:
            quiet_since = loop.time()
        elif loop.time() - quiet_since >= _HEARTBEAT_S and view["status"] not in ("finished", "failed", "stopped"):
            out.write(
                f"  … {view['status']}: {_clock(view.get('elapsed_s', 0))} of {_clock(view['spec']['budget']['time_s'])}, {view.get('clues', 0)} clue(s), {len(view.get('findings') or [])} finding(s)\n"
            )
            quiet_since = loop.time()
        out.flush()
        if view["status"] in ("finished", "failed", "stopped"):
            if as_json:
                out.write(json.dumps(view, indent=1) + "\n")
            else:
                out.write(_status_line(view) + "\n")
                if dry_run:
                    _print_charters(view, out)
                else:
                    out.write(f"Report: hercules explore report {session}  (.hercules/explore/{session}/report.md)\n")
                    _next_steps(await _current_states(client, view), out)
            return int(view.get("exit_code") if view.get("exit_code") is not None else 2)


async def _current_states(client: Any, view: Mapping[str, Any]) -> Mapping[str, Any]:
    """The session's findings with their triage state now (the session's copy is as they were when it ended)."""
    response = await client.request("GET", "/api/explore/findings", params={"session": view["id"]})
    if response.status_code >= 400:
        return view
    states = {item["id"]: item["state"] for item in response.json().get("findings", [])}
    return {**view, "findings": [{**item, "state": states.get(item["id"], item.get("state", "new"))} for item in view.get("findings") or []]}


def _clock(seconds: float) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def _print_charters(view: Mapping[str, Any], out: TextIO) -> None:
    charters = [charter for charter in view.get("charters") or [] if charter.get("status") != "done" or charter.get("kind") != "recon"]
    out.write(f"A full session would start with {len(charters)} charter(s):\n")
    for charter in charters:
        out.write(f"  {charter.get('id')}  {charter.get('title')}  (on {charter.get('area') or '/'}; levers: {', '.join(charter.get('levers') or []) or '-'})\n")
    out.write("Start it for real without --dry-run; steer it with `hercules explore charter --pin/--veto CHARTER`.\n")


def _next_steps(view: Mapping[str, Any], out: TextIO) -> None:
    findings = view.get("findings") or []
    fresh = [item for item in findings if item.get("state", "new") == "new"]
    if not findings:
        out.write("No findings. That is not a pass: the report lists what was not reached (coverage gaps).\n")
        return
    out.write(f"{len(fresh)} of {len(findings)} finding(s) wait for your decision" if fresh else f"All {len(findings)} finding(s) are decided")
    exit_code = view.get("exit_code")
    if exit_code == 0 and fresh:
        out.write(" (the exit code is 0 because only confirmed severities gate it; --gate-on proposed-hard gates on fresh ones)")
    out.write(".\n")
    if fresh:
        first = fresh[0]["id"]
        out.write(f"Next: hercules explore show {first}\n")
        out.write(f"      hercules explore triage {first} --state confirmed      (a real bug: its case is kept)\n")
        out.write(f"      hercules explore triage {first} --state by_design --reason WHY   (not a bug: its case is rejected)\n")


def _triage_hint(finding: Mapping[str, Any], out: TextIO) -> None:
    state, case = finding.get("state"), finding.get("case", "proposed")
    if case == "kept" and finding.get("case_path"):
        out.write(f"  Its case is in your cases as a known bug: hercules run {finding['case_path']}. After the fix: hercules explore reverify\n")
    elif case == "regression":
        out.write("  Its case is now a regression test: it fails a run if the bug comes back.\n")
    elif case == "rejected":
        out.write(f"  Its case is set aside and never proposed again. Undo: hercules explore triage {finding['id']} --state new\n")
    if state == "verify_fixed":
        out.write(f"  Close it once you agree it is fixed: hercules explore triage {finding['id']} --state closed\n")


def _default_triager(project: Path) -> str:
    from testzeus_hercules.explore.ledger import default_triager

    return default_triager(project)


def _print_findings(args: argparse.Namespace, data: Mapping[str, Any], out: TextIO) -> int:
    open_states = {"new", "confirmed", "in_tracker", "verify_fixed"}
    items = [
        item
        for item in data["findings"]
        if (args.state in (None, "all") or (item["state"] in open_states if args.state == "open" else item["state"] == args.state)) and (not args.case or item.get("case", "proposed") == args.case)
    ]
    if args.json:
        out.write(json.dumps({**data, "findings": items}, indent=1) + "\n")
        return 0
    if not items:
        out.write("No findings" + (" match" if args.state or args.case else "") + ".\n")
    for item in items:
        severity = item["severity"] or f"{item['proposed_severity']}?"
        out.write(f"{item['id']}  {severity:<4} {item['state']:<16} {item.get('case', 'proposed'):<10} {item['title']}\n")
        summary = next((line.strip() for line in str(item.get("actual") or "").splitlines() if line.strip()), "")
        if summary:
            out.write(f"             {summary[:110]}{'…' if len(summary) > 110 else ''}\n")
    precision = data["precision"]
    decided = precision.get("confirmed", 0) + precision.get("dismissed", 0)
    rate = f"{round(precision['precision'] * 100)}% real bugs ({precision['confirmed']} of {decided} decided)" if precision.get("precision") is not None else "nothing decided yet"
    out.write(f"All findings: {rate}; {precision['untriaged']} wait for a decision. (P1? = proposed by the model; a person confirms it.)\n")
    if precision["untriaged"] and not args.state:
        out.write("Decide one: hercules explore show ID, then hercules explore triage ID --state confirmed | by_design --reason WHY\n")
    return 0


def _print_finding(args: argparse.Namespace, item: Mapping[str, Any], out: TextIO) -> int:
    if args.json:
        out.write(json.dumps(item, indent=1) + "\n")
        return 0
    severity = item.get("severity") or f"{item.get('proposed_severity')} (proposed by the model)"
    out.write(f"{item['id']}  {item['title']}\n")
    out.write(f"  state     {item['state']}    severity {severity}    case {item.get('case', 'proposed')}" + (f"    tracker {item['tracker']}" if item.get("tracker") else "") + "\n")
    out.write(f"  expected  {item.get('expected') or '-'}\n  actual    {item.get('actual') or '-'}\n")
    out.write(
        f"  reproduced {item.get('reproduced_recorded') or '-'} replays, {item.get('reproduced_interpreted') or '-'} from the steps"
        + (f" (flaky: {item['flaky']})" if item.get("flaky") not in (None, "none") else "")
        + "\n"
    )
    if item.get("severity_reason"):
        out.write(f"  why this severity: {item['severity_reason']}\n")
    out.write("  steps:\n" + "".join(f"    {index}. {step}\n" for index, step in enumerate(item.get("steps") or [], start=1)))
    if item.get("case_path"):
        out.write(f"  case      {item['case_path']}\n")
    if item.get("run_ids"):
        out.write(f"  evidence  {', '.join(item['run_ids'][-3:])} (open them in the tester UI, Runs)\n")
    out.write(f"  seen in   {len(item.get('sessions') or [])} session(s), last {item.get('last_seen') or '-'}\n")
    for event in item.get("audit") or []:
        out.write(
            f"  history   {event.get('at')} {event.get('by')}: {event.get('field')} {event.get('before') or '-'} → {event.get('after') or '-'}"
            + (f" ({event['reason']})" if event.get("reason") else "")
            + "\n"
        )
    if item["state"] == "new":
        out.write(f"Decide: hercules explore triage {item['id']} --state confirmed | by_design --reason WHY | duplicate --duplicate-of OTHER --reason WHY\n")
    return 0


def _print_sessions(args: argparse.Namespace, views: Sequence[Mapping[str, Any]], out: TextIO) -> int:
    views = sorted(views, key=lambda view: str(view.get("started_at") or view.get("id")), reverse=True)[: max(1, args.limit)]
    if args.json:
        out.write(json.dumps({"sessions": views}, indent=1) + "\n")
        return 0
    if not views:
        out.write("No exploration sessions yet: hercules explore start --base-url URL --time 30m\n")
        return 0
    for view in views:
        spec = view.get("spec") or {}
        where = spec.get("base_url") or spec.get("environment") or spec.get("start_case") or "-"
        findings = view.get("findings") or []
        out.write(
            f"{view['id']}  {view['status']:<11} {spec.get('mission', '-'):<10} {_clock(view.get('elapsed_s', 0)):>8}  {len(findings)} finding(s)  "
            f"{'~' if view.get('cost_estimated') else ''}${view.get('cost_usd', 0):.2f}  {where}\n"
        )
    out.write("Use the id (or a unique start of it, or `last`) with status, report and stop.\n")
    return 0


def _event_line(event: Mapping[str, Any]) -> str:
    data = event.get("data") or {}
    kind = event.get("kind")
    if kind == "charter_started":
        return f"  ▸ charter {data.get('charter') or ''}: {data.get('title')}".replace("charter : ", "charter: ")
    if kind == "charter_finished":
        return f"  ✓ charter done {data.get('charter')}: {data.get('turns')} moves, {data.get('clues')} clue(s)"
    if kind == "clue":
        return f"  ? clue ({data.get('tier')}): {data.get('title')}"
    if kind == "finding":
        return f"  ✗ finding {data.get('finding') or ''} {data.get('severity')} proposed ({data.get('outcome')}): {data.get('title')}"
    if kind == "size":
        parts = [f"{label} {' → '.join(str(value) for value in data[key])}" for key, label in (("workers", "explorers"), ("reproducers", "reproducers")) if data.get(key)]
        return f"  swarm size changed by {data.get('by')}: {', '.join(parts)}"
    return ""


def _budget(view: Mapping[str, Any]) -> str:
    budget = ((view.get("spec") or {}).get("budget") or {}).get("time_s")
    if not budget:
        return ""
    return f" of {_clock(budget)}" if view.get("status") not in ("finished", "failed", "stopped") else f" (time box {_clock(budget)})"


def _status_line(view: Mapping[str, Any]) -> str:
    findings = view.get("findings") or []
    return (
        f"Session {view['id']}: {view['status']} · {_clock(view.get('elapsed_s', 0))}{_budget(view)} · {view['workers']} explorer(s), {view['reproducers']} reproducer(s) · "
        f"{view.get('clues', 0)} clue(s), {len(findings)} finding(s) · {'~' if view.get('cost_estimated') else ''}${view.get('cost_usd', 0):.2f}"
        + (f" · {view['message']}" if view.get("message") else "")
    )


async def _reverify(args: argparse.Namespace, client: Any, project: Path, out: TextIO, err: TextIO) -> int:
    """Rerun the kept and regression cases: the daemon runs the job (``POST /api/explore/reverify``) and this follows it,
    printing each line as it comes. A kept case that passes moves its open finding to verify_fixed; a regression case
    that fails reopens its finding, and the command exits 1."""
    body: dict[str, Any] = {"build": args.build, "case_timeout_s": getattr(args, "timeout", 900)}
    if getattr(args, "finding", None):
        body["findings"] = list(args.finding)
    as_json = bool(getattr(args, "json", False))
    progress = err if as_json else out
    job = await _call(client, "POST", "/api/explore/reverify", err, json=body)
    if job is None:
        return 2
    printed = 0
    while True:
        for line in job.get("lines", [])[printed:]:
            progress.write(line + "\n")
            progress.flush()
        printed = max(printed, len(job.get("lines", [])))
        if job.get("finished"):
            break
        job = await _call(client, "GET", f"/api/explore/reverify/{job['id']}", err, params={"wait_s": 30, "since": printed}, timeout_s=45)
        if job is None:
            return 2
    for problem in job.get("errors", []):
        err.write(f"hercules explore: {problem}\n")
    if as_json:
        out.write(json.dumps(job, indent=1) + "\n")
    return int(job["exit_code"]) if job.get("exit_code") is not None else 2
