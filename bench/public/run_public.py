"""Run the public demo-app scenarios with ``hercules run`` and score the outcomes.

Each scenario runs as its own ``hercules run --json --base-url <app>`` subprocess in a fresh project
directory under ``bench/public/results/<timestamp>/projects/``. The four apps are shared public demos,
so runs are strictly serial with a pause between them; apps that do not answer a probe are skipped.

Writes ``report.md`` and ``results.json`` to ``bench/public/results/<timestamp>/`` with the local
bench's metrics plus per-app accuracy and cost / latency / decision columns.

Usage::

    uv run --frozen --python 3.12 python bench/public/run_public.py --list
    uv run --frozen --python 3.12 python bench/public/run_public.py --mode pure
    uv run --frozen --python 3.12 python bench/public/run_public.py --tags negative --repeat 3
    uv run --frozen --python 3.12 python bench/public/run_public.py --only sd0,xb --cli-arg=--egress=hybrid-text
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

PUBLIC_DIR = Path(__file__).resolve().parent
BENCH_DIR = PUBLIC_DIR.parent
SCENARIOS_DIR = PUBLIC_DIR / "scenarios"
RESULTS_DIR = PUBLIC_DIR / "results"
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(PUBLIC_DIR))

import run_bench as rb  # noqa: E402  - shared helpers of the local bench
from validate import Entry, load_manifest, probe, select  # noqa: E402

# Published demo credentials (printed on each site's login page); real values can be overridden in the environment.
PUBLIC_SECRETS = {"SAUCE_PASSWORD": "secret_sauce", "ORANGEHRM_PASSWORD": "admin123", "PARABANK_PASSWORD": "demo"}
_PROBLEMS = ("failed", "error", "blocked", "unverified")


# --------------------------------------------------------------------------- running


@dataclass
class Outcome:
    scenario: str
    file: str
    app: str
    repeat: int
    tags: list[str]
    expected: str
    actual: str  # passed | failed | error | skipped (+ any other RunResult status)
    correct: bool
    false_pass: bool
    false_fail: bool
    wrong_reason: bool
    steps_passed: int = 0
    steps_total: int = 0
    duration_s: float = 0.0
    engine_duration_s: float | None = None
    failing_step: int | None = None
    failing_action: str | None = None
    failing_status: str | None = None
    reason: str | None = None
    run_id: str | None = None
    exit_code: int | None = None
    decisions: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    engines: dict[str, Any] = field(default_factory=dict)
    project: str = ""
    notes: str = ""
    hints: list[str] = field(default_factory=list)


def prepare_case(entry: Entry, project: Path, *, cli_base_url: bool) -> Path:
    """Copy the scenario into ``project``; JSON cases get the app in ``environment.base_url``."""
    target = project / entry.path.name
    if entry.path.suffix == ".json":
        case = json.loads(entry.path.read_text(encoding="utf-8"))
        case["environment"] = {**case.get("environment", {}), "base_url": entry.base_url}
        target.write_text(json.dumps(case, indent=2), encoding="utf-8")
    elif cli_base_url:
        shutil.copyfile(entry.path, target)
    else:
        target.write_text(rb.template_feature(entry.path.read_text(encoding="utf-8"), entry.base_url), encoding="utf-8")
    return target


def build_command(hercules: str, case_path: Path, project: Path, entry: Entry, args: argparse.Namespace, *, cli_base_url: bool) -> list[str]:
    command = [hercules, "run", str(case_path), "--json", "--project", str(project)]
    if args.mode:
        command += ["--mode", args.mode]
    if cli_base_url:
        command += ["--base-url", entry.base_url]
    if args.headed:
        command.append("--headed")
    return command + list(args.cli_arg)


def run_one(entry: Entry, *, repeat: int, workdir: Path, hercules: str, env: Mapping[str, str], args: argparse.Namespace, cli_base_url: bool) -> Outcome:
    project = workdir / (entry.name if args.repeat == 1 else f"{entry.name}-r{repeat}")
    project.mkdir(parents=True, exist_ok=True)
    case_path = prepare_case(entry, project, cli_base_url=cli_base_url)
    command = build_command(hercules, case_path, project, entry, args, cli_base_url=cli_base_url)
    started = time.monotonic()
    try:
        proc = subprocess.run(command, capture_output=True, text=True, env=dict(env), timeout=args.timeout_s, cwd=project)
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code = rb._text(exc.stdout), f"timed out after {args.timeout_s} s", None
    wall = round(time.monotonic() - started, 2)
    return score(entry, repeat, rb._parse_result(stdout), wall=wall, exit_code=code, stdout=stdout, stderr=stderr, project=project)


def _blank(entry: Entry, repeat: int, *, actual: str, reason: str | None = None) -> Outcome:
    return Outcome(entry.name, entry.file, entry.app, repeat, list(entry.tags), entry.expected_status, actual, False, False, False, False, reason=reason, notes=entry.notes)


def skipped(entry: Entry, repeat: int, reason: str) -> Outcome:
    return _blank(entry, repeat, actual="skipped", reason=reason)


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def score(entry: Entry, repeat: int, result: dict[str, Any] | None, *, wall: float, exit_code: int | None, stdout: str, stderr: str, project: Path) -> Outcome:
    expected = entry.expected_status
    base = replace(_blank(entry, repeat, actual="error"), duration_s=wall, exit_code=exit_code, project=str(project))
    if result is None:
        return replace(base, false_fail=expected == "passed", reason=_last_line(stderr) or _last_line(stdout) or "no RunResult on stdout")
    actual = str(result["status"])
    steps = result.get("steps") or []
    problem = next((step for step in steps if step.get("status") in _PROBLEMS), None)
    failing_step = int(problem["index"]) if problem else None
    cost = result.get("cost") or {}
    usd = cost.get("usd")
    return replace(
        base,
        actual=actual,
        correct=actual == expected,
        false_pass=expected == "failed" and actual == "passed",
        false_fail=expected == "passed" and actual != "passed",
        wrong_reason=expected == "failed" and actual == "failed" and bool(entry.fail_steps) and failing_step not in entry.fail_steps,
        steps_passed=sum(step.get("status") == "passed" for step in steps),
        steps_total=len(steps),
        engine_duration_s=result.get("duration_s"),
        failing_step=failing_step,
        failing_action=problem.get("action") if problem else None,
        failing_status=problem.get("status") if problem else None,
        reason=(problem.get("actual") if problem else None) or ((result.get("hints") or [None])[0] if actual != "passed" else None),
        run_id=result.get("run_id"),
        decisions=sum(int(step.get("decisions") or 0) for step in steps),
        tokens_in=int(cost.get("tokens_in") or 0),
        tokens_out=int(cost.get("tokens_out") or 0),
        cost_usd=float(usd) if isinstance(usd, (int, float)) else None,
        engines=dict(result.get("engines") or {}),
        hints=list(result.get("hints") or []),
    )


# --------------------------------------------------------------------------- summary


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 3) if whole else 0.0


def _group(outcomes: Sequence[Outcome]) -> dict[str, Any]:
    durations = [o.duration_s for o in outcomes]
    costs = [o.cost_usd for o in outcomes if o.cost_usd is not None]
    return {
        "runs": len(outcomes),
        "correct": sum(o.correct for o in outcomes),
        "accuracy": _rate(sum(o.correct for o in outcomes), len(outcomes)),
        "false_passes": sum(o.false_pass for o in outcomes),
        "false_fails": sum(o.false_fail for o in outcomes),
        "errors": sum(o.actual == "error" for o in outcomes),
        "failed_for_wrong_reason": sum(o.wrong_reason for o in outcomes),
        "median_duration_s": round(statistics.median(durations), 2) if durations else 0.0,
        "p95_duration_s": round(rb.percentile(durations, 95), 2),
        "decisions": sum(o.decisions for o in outcomes),
        "tokens_in": sum(o.tokens_in for o in outcomes),
        "tokens_out": sum(o.tokens_out for o in outcomes),
        "cost_usd": round(sum(costs), 4) if costs else None,
        "mean_cost_usd": round(sum(costs) / len(costs), 4) if costs else None,
    }


def summarize(outcomes: Sequence[Outcome]) -> dict[str, Any]:
    ran = [o for o in outcomes if o.actual != "skipped"]
    summary = {**_group(ran), "scenarios": len({o.scenario for o in ran}), "skipped": len(outcomes) - len(ran)}
    summary["by_app"] = {app: _group([o for o in ran if o.app == app]) for app in sorted({o.app for o in ran})}
    summary["by_tag"] = {tag: _group([o for o in ran if tag in o.tags]) for tag in sorted({t for o in ran for t in o.tags})}
    summary["flaky"] = sorted({o.scenario for o in ran if len({x.actual for x in ran if x.scenario == o.scenario}) > 1})
    return summary


def _money(value: float | None) -> str:
    return "–" if value is None else f"${value:.4f}"


def render_report(outcomes: Sequence[Outcome], summary: Mapping[str, Any], meta: Mapping[str, Any]) -> str:
    cell = rb._cell
    lines = [
        f"# Hercules public demo-app report ({meta['started']})",
        "",
        f"Engine: {meta['engine']} · runs: {summary['runs']} ({summary['scenarios']} scenarios × {meta['repeat']}) · skipped: {summary['skipped']} · base URL via: {meta['base_url_mode']}",
        "",
        "## Summary",
        "",
        "| Accuracy | Correct | False passes | False fails | Errors | Failed for the wrong reason | Median s | p95 s | Decisions | Tokens in/out | Cost |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| {summary['accuracy']:.1%} | {summary['correct']}/{summary['runs']} | {summary['false_passes']} | {summary['false_fails']} | {summary['errors']} | {summary['failed_for_wrong_reason']} "
        f"| {summary['median_duration_s']} | {summary['p95_duration_s']} | {summary['decisions']} | {summary['tokens_in']}/{summary['tokens_out']} | {_money(summary['cost_usd'])} |",
        "",
        "A **false pass** passed although the correct outcome is failed; a **false fail** did not pass although a human can",
        "complete the scenario; **failed for the wrong reason** failed at a step other than the manifest's `fail_step`.",
        "Skipped runs (site unavailable) are not counted.",
        "",
    ]
    if summary["flaky"]:
        lines += [f"Inconsistent across repeats: {', '.join(summary['flaky'])}", ""]
    header = "| {} | Runs | Accuracy | False passes | False fails | Errors | Wrong reason | Median s | p95 s | Decisions | Mean cost |"
    rule = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    for title, key in (("App", "by_app"), ("Tag", "by_tag")):
        lines += [f"## Accuracy by {title.lower()}", "", header.format(title), rule]
        for name, row in summary[key].items():
            lines.append(
                f"| {name} | {row['runs']} | {row['accuracy']:.0%} | {row['false_passes']} | {row['false_fails']} | {row['errors']} | {row['failed_for_wrong_reason']} "
                f"| {row['median_duration_s']} | {row['p95_duration_s']} | {row['decisions']} | {_money(row['mean_cost_usd'])} |"
            )
        lines.append("")
    lines += ["## Scenarios", "", "| Scenario | App | Expected | Actual | Verdict | Steps | s | Decisions | Cost | Failing step | Reason |", "|---|---|---|---|---|---:|---:|---:|---:|---|---|"]
    for o in outcomes:
        if o.actual == "skipped":
            verdict = "skipped"
        else:
            verdict = "yes" if o.correct and not o.wrong_reason else ("wrong step" if o.correct else ("FALSE PASS" if o.false_pass else "no"))
        failing = f"{o.failing_step}: {cell(o.failing_action, 60)}" if o.failing_step else ""
        name = o.scenario if meta["repeat"] == 1 else f"{o.scenario} #{o.repeat}"
        lines.append(
            f"| {name} | {o.app} | {o.expected} | {o.actual} | {verdict} | {o.steps_passed}/{o.steps_total} | {o.duration_s} | {o.decisions} | {_money(o.cost_usd)} | {failing} | {cell(o.reason, 140)} |"
        )
    lines += ["", "Evidence for each run is under `projects/<scenario>/.hercules/runs/<run_id>/` next to this report.", ""]
    return "\n".join(lines)


def write_results(outdir: Path, outcomes: Sequence[Outcome], meta: Mapping[str, Any]) -> dict[str, Any]:
    summary = summarize(outcomes)
    payload = {"meta": dict(meta), "summary": summary, "results": [asdict(o) for o in outcomes]}
    (outdir / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (outdir / "report.md").write_text(render_report(outcomes, summary, meta), encoding="utf-8")
    return summary


# --------------------------------------------------------------------------- CLI


def run_env(home: Path) -> dict[str, str]:
    env = rb.run_env(home)
    for name, value in PUBLIC_SECRETS.items():
        env.setdefault(name, value)
    return env


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the public demo-app scenarios with `hercules run` and score them.")
    parser.add_argument("--apps", type=_csv, default=[], help="comma-separated apps: saucedemo, orangehrm, parabank, xyzbank")
    parser.add_argument("--tags", type=_csv, default=[], help="comma-separated tags; run scenarios having any of them")
    parser.add_argument("--only", type=_csv, default=[], help="comma-separated scenario names or name prefixes (e.g. sd0,ohn)")
    parser.add_argument("--mode", default=None, help="passed to `hercules run --mode` when given")
    parser.add_argument("--cli-arg", action="append", default=[], metavar="ARG", help="extra `hercules run` argument (repeatable), e.g. --cli-arg=--egress=hybrid-text")
    parser.add_argument("--repeat", type=int, default=1, help="run each scenario N times")
    parser.add_argument("--timeout-s", type=float, default=300.0, help="per-run timeout for the hercules subprocess")
    parser.add_argument("--pause-s", type=float, default=3.0, help="pause between runs, to be polite to the shared demos")
    parser.add_argument("--include-unavailable", action="store_true", help="also run scenarios whose manifest site_status is unavailable")
    parser.add_argument("--no-probe", action="store_true", help="do not probe each app before running it")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--hercules", default=None, help="path to the hercules executable (default: next to this Python)")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR, help="where to write <timestamp>/report.md")
    parser.add_argument("--list", action="store_true", help="list the selected scenarios and exit")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    return args


def _progress(o: Outcome, done: int, total: int) -> None:
    if o.actual == "skipped":
        mark = "skip"
    else:
        mark = "ok " if o.correct and not o.wrong_reason else ("FALSE PASS" if o.false_pass else "MISS")
    where = f" step {o.failing_step}:" if o.failing_step else ""
    detail = f"{where} {rb._cell(o.reason, 100)}" if o.reason and o.actual != "passed" else ""
    print(f"[{done}/{total}] {mark:4} {o.scenario:40} expected {o.expected:6} got {o.actual:7} {o.duration_s:6.1f}s{detail}", flush=True)


def run_public(args: argparse.Namespace) -> int:
    entries = select(load_manifest(), apps=args.apps, tags=args.tags, only=args.only)
    if not entries:
        print("no scenarios match the filters", file=sys.stderr)
        return 2
    if args.list:
        for e in entries:
            fail = f"@{','.join(map(str, e.fail_steps))}" if e.fail_steps else ""
            print(f"{e.name:42} {e.app:10} {e.expected_status + fail:10} {e.site_status:11} {','.join(t for t in e.tags if not t.startswith('app:'))}")
        print(f"{len(entries)} scenario(s)")
        return 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = args.results_dir / stamp
    workdir = outdir / "projects"
    workdir.mkdir(parents=True, exist_ok=True)
    hercules = rb.hercules_executable(args.hercules)
    env = run_env(outdir / "hercules-home")
    cli_base_url = rb.cli_supports_base_url(hercules, env)
    reachable = {app: ("available" if args.no_probe else probe(url)) for app, url in sorted({(e.app, e.base_url) for e in entries})}
    engine = " ".join(part for part in (f"mode={args.mode}" if args.mode else "", " ".join(args.cli_arg)) if part) or "defaults"
    meta = {"started": stamp, "engine": engine, "repeat": args.repeat, "base_url_mode": "--base-url" if cli_base_url else "case templating", "hercules": hercules, "sites": reachable}
    total = len(entries) * args.repeat
    print(f"{total} run(s); engine {engine}; sites {reachable}; results in {outdir}", flush=True)
    outcomes: list[Outcome] = []
    for repeat in range(1, args.repeat + 1):
        for entry in entries:
            if entry.site_status != "available" and not args.include_unavailable:
                outcome = skipped(entry, repeat, "manifest marks the site unavailable")
            elif reachable[entry.app] != "available":
                outcome = skipped(entry, repeat, "site did not answer the probe")
            else:
                outcome = run_one(entry, repeat=repeat, workdir=workdir, hercules=hercules, env=env, args=args, cli_base_url=cli_base_url)
                time.sleep(args.pause_s)
            outcomes.append(outcome)
            _progress(outcome, len(outcomes), total)
    summary = write_results(outdir, outcomes, meta)
    print(
        f"\naccuracy {summary['accuracy']:.1%} ({summary['correct']}/{summary['runs']}), false passes {summary['false_passes']}, false fails {summary['false_fails']}, "
        f"errors {summary['errors']}, skipped {summary['skipped']}, median {summary['median_duration_s']} s, p95 {summary['p95_duration_s']} s, cost {_money(summary['cost_usd'])}"
    )
    print(f"report: {outdir / 'report.md'}")
    return 0 if summary["correct"] == summary["runs"] else 1


def main(argv: Iterable[str] | None = None) -> int:
    return run_public(parse_args(list(argv) if argv is not None else None))


if __name__ == "__main__":
    sys.exit(main())
