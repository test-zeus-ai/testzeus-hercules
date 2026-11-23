"""Run the bench scenarios against the Demo Shop app with ``hercules run`` and score the outcomes.

Each scenario runs as a separate ``hercules run --json`` subprocess in its own
project directory under ``bench/results/<timestamp>/projects/``, so runs never
share state or evidence. The app's address reaches the case through
``environment.base_url`` (TestCase JSON), ``--base-url`` when the CLI offers it,
or else by rewriting the relative URLs in a ``.feature`` file's Given/When steps.

Writes ``report.md`` and ``results.json`` to ``bench/results/<timestamp>/``.

Every run needs the local model server (it reads the steps) and, in hybrid mode,
the JEV key: there is no model-free mode. ``--cache-dir`` shares the case-reading
cache between runs (each run's ``.hercules/cache`` points at it), so a second bench
run measures the cached path: no reading call before the first step.

Usage::

    uv run python bench/run_bench.py --cli-arg=--mode=pure               # the local model does everything
    uv run python bench/run_bench.py --cli-arg=--mode=hybrid --cli-arg=--egress=hybrid-text
    uv run python bench/run_bench.py --cli-arg=--mode=hybrid --cache-dir bench/results/cache   # run twice to see the cache
    uv run python bench/run_bench.py --tags form,iframe --repeat 3
    uv run python bench/run_bench.py --only 01_login_success,n04
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

BENCH_DIR = Path(__file__).resolve().parent
SCENARIOS_DIR = BENCH_DIR / "scenarios"
RESULTS_DIR = BENCH_DIR / "results"
sys.path.insert(0, str(BENCH_DIR))

from serve import serving  # noqa: E402

DEFAULT_CHROMIUM = Path.home() / "Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
DEMO_SECRETS = {"DEMO_SHOP_PASSWORD": "demo1234"}  # the demo app's published test password
_STEP_LINE = re.compile(r"^(\s*)(Given|When|Then|And|But|\*)(\s+.*)$")
_SECTION_LINE = re.compile(r"^\s*(Feature|Background|Scenario|Example)\b")
_RELATIVE_URL = re.compile(r'"(/[^"]*)"')
_PROBLEMS = ("failed", "error", "blocked", "unverified")
_LOCAL_CALLS = re.compile(r"^Local model calls: (\d+)", re.MULTILINE)
_DECIDER_CALLS = re.compile(r"^(?:JEV|Clef) calls: (\d+)", re.MULTILINE)


# --------------------------------------------------------------------------- manifest


@dataclass(frozen=True)
class Scenario:
    file: str
    expected_status: str
    tags: tuple[str, ...]
    notes: str = ""
    fail_step: int | None = None

    @property
    def name(self) -> str:
        return Path(self.file).stem


def load_manifest(path: Path = SCENARIOS_DIR / "manifest.yaml") -> list[Scenario]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    scenarios = []
    for entry in raw.get("scenarios", []):
        if entry["expected_status"] not in ("passed", "failed"):
            raise ValueError(f"{entry['file']}: expected_status must be passed or failed")
        scenarios.append(Scenario(entry["file"], entry["expected_status"], tuple(entry.get("tags") or ()), entry.get("notes", ""), entry.get("fail_step")))
    return scenarios


def select(scenarios: Sequence[Scenario], *, tags: Sequence[str], only: Sequence[str]) -> list[Scenario]:
    """Scenarios having any of ``tags`` whose name starts with any ``only`` term."""
    chosen = [s for s in scenarios if not tags or set(tags) & set(s.tags)]
    return [s for s in chosen if not only or any(s.name.startswith(term) for term in only)]


# --------------------------------------------------------------------------- case preparation


def template_feature(text: str, base_url: str) -> str:
    """Make relative URLs in Given/When steps absolute; assertions (Then blocks) are left as written."""
    base = base_url.rstrip("/")
    lines, in_then = [], False
    for line in text.splitlines():
        step = _STEP_LINE.match(line)
        if step is not None:
            keyword = step.group(2)
            in_then = keyword == "Then" or (in_then and keyword not in ("Given", "When"))
            if not in_then:
                line = _RELATIVE_URL.sub(lambda m: f'"{base}{m.group(1)}"', line)
        elif _SECTION_LINE.match(line):
            in_then = False
        lines.append(line)
    return "\n".join(lines) + "\n"


def prepare_case(scenario: Scenario, base_url: str, project: Path, *, cli_base_url: bool) -> Path:
    """Copy the scenario into ``project`` with the app's base URL applied."""
    source = SCENARIOS_DIR / scenario.file
    target = project / source.name
    if source.suffix == ".json":
        case = json.loads(source.read_text(encoding="utf-8"))
        case["environment"] = {**case.get("environment", {}), "base_url": base_url}
        target.write_text(json.dumps(case, indent=2), encoding="utf-8")
    elif cli_base_url:
        shutil.copyfile(source, target)
    else:
        target.write_text(template_feature(source.read_text(encoding="utf-8"), base_url), encoding="utf-8")
    return target


# --------------------------------------------------------------------------- running


@dataclass
class Outcome:
    scenario: str
    file: str
    repeat: int
    tags: list[str]
    expected: str
    actual: str
    correct: bool
    false_pass: bool
    false_fail: bool
    wrong_reason: bool
    steps_passed: int
    steps_total: int
    duration_s: float
    engine_duration_s: float | None
    failing_step: int | None = None
    failing_action: str | None = None
    failing_status: str | None = None
    reason: str | None = None
    run_id: str | None = None
    exit_code: int | None = None
    project: str = ""
    notes: str = ""
    hints: list[str] = field(default_factory=list)
    mode: str | None = None
    jev_calls: int = 0
    fallbacks: int = 0
    cost_usd: float | None = None
    local_calls: int = 0
    # Where the steps' reading came from: "model" (one call before the run), "cache", or "none".
    reading: str = "none"


def hercules_executable(explicit: str | None) -> str:
    if explicit:
        return explicit
    sibling = Path(sys.executable).with_name("hercules")
    if sibling.exists():
        return str(sibling)
    found = shutil.which("hercules")
    if found is None:
        raise SystemExit("cannot find the `hercules` command; run `uv sync` or pass --hercules PATH")
    return found


def cli_supports_base_url(hercules: str, env: Mapping[str, str]) -> bool:
    try:
        help_text = subprocess.run([hercules, "run", "--help"], capture_output=True, text=True, env=dict(env), timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "--base-url" in help_text


def run_env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("HERCULES_TELEMETRY", "0")  # bench runs are not real usage
    env["HERCULES_HOME"] = str(home)
    if not env.get("HERCULES_CHROMIUM") and DEFAULT_CHROMIUM.is_file():
        env["HERCULES_CHROMIUM"] = str(DEFAULT_CHROMIUM)
    for name, value in DEMO_SECRETS.items():
        env.setdefault(name, value)
    return env


def run_one(scenario: Scenario, *, repeat: int, base_url: str, workdir: Path, hercules: str, env: Mapping[str, str], args: argparse.Namespace, cli_base_url: bool) -> Outcome:
    project = workdir / (scenario.name if args.repeat == 1 else f"{scenario.name}-r{repeat}")
    project.mkdir(parents=True, exist_ok=True)
    if args.cache_dir is not None:
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        (project / ".hercules").mkdir(exist_ok=True)
        link = project / ".hercules" / "cache"
        if not link.exists():
            link.symlink_to(args.cache_dir.resolve(), target_is_directory=True)
    case_path = prepare_case(scenario, base_url, project, cli_base_url=cli_base_url)
    command = [hercules, "run", str(case_path), "--json", "--project", str(project)]
    if cli_base_url:
        command += ["--base-url", base_url]
    if args.headed:
        command.append("--headed")
    command += args.cli_arg
    started = time.monotonic()
    try:
        proc = subprocess.run(command, capture_output=True, text=True, env=dict(env), timeout=args.timeout_s, cwd=project)
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code = _text(exc.stdout), f"timed out after {args.timeout_s} s", None
    wall = round(time.monotonic() - started, 2)
    result = _parse_result(stdout)
    return score(scenario, repeat, result, wall=wall, exit_code=code, stdout=stdout, stderr=stderr, project=project)


def _text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def _parse_result(stdout: str) -> dict[str, Any] | None:
    start = stdout.find("{")
    if start < 0:
        return None
    try:
        parsed = json.loads(stdout[start:])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) and "status" in parsed else None


def score(scenario: Scenario, repeat: int, result: dict[str, Any] | None, *, wall: float, exit_code: int | None, stdout: str, stderr: str, project: Path) -> Outcome:
    expected = scenario.expected_status
    if result is None:
        reason = _first_line(stderr) or _first_line(stdout) or "no RunResult on stdout"
        return Outcome(
            scenario=scenario.name,
            file=scenario.file,
            repeat=repeat,
            tags=list(scenario.tags),
            expected=expected,
            actual="error",
            correct=False,
            false_pass=False,
            false_fail=expected == "passed",
            wrong_reason=False,
            steps_passed=0,
            steps_total=0,
            duration_s=wall,
            engine_duration_s=None,
            reason=reason,
            exit_code=exit_code,
            project=str(project),
            notes=scenario.notes,
        )
    actual = str(result["status"])
    steps = result.get("steps") or []
    problem = next((step for step in steps if step.get("status") in _PROBLEMS), None)
    failing_step = int(problem["index"]) if problem else None
    wrong_reason = expected == "failed" and actual == "failed" and scenario.fail_step is not None and failing_step != scenario.fail_step
    engines = result.get("engines") or {}
    cost = (result.get("cost") or {}).get("usd")
    local = _LOCAL_CALLS.search(stderr or "")
    decider = _DECIDER_CALLS.search(stderr or "")
    return Outcome(
        scenario=scenario.name,
        file=scenario.file,
        repeat=repeat,
        tags=list(scenario.tags),
        expected=expected,
        actual=actual,
        correct=actual == expected,
        false_pass=expected == "failed" and actual == "passed",
        false_fail=expected == "passed" and actual != "passed",
        wrong_reason=wrong_reason,
        steps_passed=sum(step.get("status") == "passed" for step in steps),
        steps_total=len(steps),
        duration_s=wall,
        engine_duration_s=result.get("duration_s"),
        failing_step=failing_step,
        failing_action=problem.get("action") if problem else None,
        failing_status=problem.get("status") if problem else None,
        reason=(problem.get("actual") if problem else None) or ((result.get("hints") or [None])[0] if actual != "passed" else None),
        run_id=result.get("run_id"),
        exit_code=exit_code,
        project=str(project),
        notes=scenario.notes,
        hints=list(result.get("hints") or []),
        mode=engines.get("mode"),
        jev_calls=int(decider.group(1)) if decider else int(engines.get("jev_calls") or 0) + int(engines.get("clef_calls") or 0),
        fallbacks=int(engines.get("fallbacks") or 0),
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        local_calls=int(local.group(1)) if local else 0,
        reading=_reading(project, result.get("run_id")),
    )


def _reading(project: Path, run_id: Any) -> str:
    """Where the run's step readings came from, from the first plan record of each step."""
    sources = set()
    for path in (project / ".hercules" / "runs" / str(run_id or "") / "steps").glob("*/plan.jsonl"):
        try:
            first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        except (OSError, ValueError, IndexError):
            continue
        sources.add(str(first.get("reading") or "none"))
    return "+".join(sorted(sources)) or "none"


def _first_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


# --------------------------------------------------------------------------- summary and report


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(outcomes: Sequence[Outcome]) -> dict[str, Any]:
    durations = [o.duration_s for o in outcomes]
    tags = sorted({tag for o in outcomes for tag in o.tags})
    by_tag = {}
    for tag in tags:
        tagged = [o for o in outcomes if tag in o.tags]
        by_tag[tag] = {"runs": len(tagged), "correct": sum(o.correct for o in tagged), "accuracy": round(sum(o.correct for o in tagged) / len(tagged), 3)}
    flaky = sorted({o.scenario for o in outcomes if len({x.actual for x in outcomes if x.scenario == o.scenario}) > 1})
    total = len(outcomes)
    return {
        "runs": total,
        "scenarios": len({o.scenario for o in outcomes}),
        "correct": sum(o.correct for o in outcomes),
        "accuracy": round(sum(o.correct for o in outcomes) / total, 3) if total else 0.0,
        "false_passes": sum(o.false_pass for o in outcomes),
        "false_fails": sum(o.false_fail for o in outcomes),
        "errors": sum(o.actual == "error" for o in outcomes),
        "failed_for_wrong_reason": sum(o.wrong_reason for o in outcomes),
        "median_duration_s": round(statistics.median(durations), 2) if durations else 0.0,
        "p95_duration_s": round(percentile(durations, 95), 2),
        "by_tag": by_tag,
        "flaky": flaky,
        "modes": sorted({o.mode for o in outcomes if o.mode}),
        "jev_calls": sum(o.jev_calls for o in outcomes),
        "fallbacks": sum(o.fallbacks for o in outcomes),
        "jev_cost_usd": round(sum(o.cost_usd or 0.0 for o in outcomes), 6),
        "local_calls": sum(o.local_calls for o in outcomes),
        "model_calls_per_case": round(sum(o.local_calls + o.jev_calls for o in outcomes) / total, 2) if total else 0.0,
        "local_calls_per_case": round(sum(o.local_calls for o in outcomes) / total, 2) if total else 0.0,
        "decider_calls_per_case": round(sum(o.jev_calls for o in outcomes) / total, 2) if total else 0.0,
        "cached_readings": sum(o.reading == "cache" for o in outcomes),
    }


def _cell(text: Any, limit: int = 160) -> str:
    flat = " ".join(str(text).split()) if text is not None else ""
    flat = flat if len(flat) <= limit else flat[: limit - 1] + "…"
    return flat.replace("|", "\\|")


def render_report(outcomes: Sequence[Outcome], summary: Mapping[str, Any], meta: Mapping[str, Any]) -> str:
    lines = [
        f"# Hercules bench report ({meta['started']})",
        "",
        f"Engines: `{meta['decider']}` · mode: {', '.join(summary['modes']) or 'n/a'} · runs: {summary['runs']} ({summary['scenarios']} scenarios × {meta['repeat']}) · app: {meta['base_url']} · base URL via: {meta['base_url_mode']}",
        "",
        f"JEV/Clef calls: {summary['jev_calls']} · local fallbacks: {summary['fallbacks']} · JEV cost: ${summary['jev_cost_usd']:.4f}",
        f"Model calls per case: {summary['model_calls_per_case']} (local {summary['local_calls_per_case']}, decider {summary['decider_calls_per_case']}) · case readings from the cache: {summary['cached_readings']}/{summary['runs']}",
        "",
        "## Summary",
        "",
        "| Accuracy | Correct | False passes | False fails | Errors | Failed for the wrong reason | Median s | p95 s |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| {summary['accuracy']:.1%} | {summary['correct']}/{summary['runs']} | {summary['false_passes']} | {summary['false_fails']} | {summary['errors']} | {summary['failed_for_wrong_reason']} | {summary['median_duration_s']} | {summary['p95_duration_s']} |",
        "",
        "A **false pass** is a run that passed although the correct outcome is failed. A **false fail** is a run that did not pass",
        "although a human can complete the scenario. **Failed for the wrong reason** counts expected failures that failed at a",
        "different step than `fail_step` in the manifest.",
        "",
    ]
    if summary["flaky"]:
        lines += [f"Inconsistent across repeats: {', '.join(summary['flaky'])}", ""]
    lines += ["## Accuracy by tag", "", "| Tag | Runs | Correct | Accuracy |", "|---|---:|---:|---:|"]
    lines += [f"| {tag} | {row['runs']} | {row['correct']} | {row['accuracy']:.0%} |" for tag, row in summary["by_tag"].items()]
    lines += ["", "## Scenarios", "", "| Scenario | Expected | Actual | Correct | Steps | Duration s | Failing step | Reason |", "|---|---|---|---|---:|---:|---|---|"]
    for o in outcomes:
        verdict = "yes" if o.correct and not o.wrong_reason else ("wrong step" if o.correct else ("FALSE PASS" if o.false_pass else "no"))
        failing = f"{o.failing_step}: {_cell(o.failing_action, 70)}" if o.failing_step else ""
        name = o.scenario if meta["repeat"] == 1 else f"{o.scenario} #{o.repeat}"
        lines.append(f"| {name} | {o.expected} | {o.actual} | {verdict} | {o.steps_passed}/{o.steps_total} | {o.duration_s} | {failing} | {_cell(o.reason)} |")
    lines += ["", "Evidence for each run is under `projects/<scenario>/.hercules/runs/<run_id>/` next to this report.", ""]
    return "\n".join(lines)


def write_results(outdir: Path, outcomes: Sequence[Outcome], meta: Mapping[str, Any]) -> dict[str, Any]:
    summary = summarize(outcomes)
    payload = {"meta": dict(meta), "summary": summary, "results": [asdict(o) for o in outcomes]}
    (outdir / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (outdir / "report.md").write_text(render_report(outcomes, summary, meta), encoding="utf-8")
    return summary


# --------------------------------------------------------------------------- CLI


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Hercules bench scenarios against the Demo Shop app.")
    parser.add_argument("--tags", type=_csv, default=[], help="comma-separated tags; run scenarios having any of them")
    parser.add_argument("--only", type=_csv, default=[], help="comma-separated scenario names or name prefixes (e.g. 01_login,n0)")
    parser.add_argument("--cache-dir", type=Path, default=None, help="share the case-reading cache across runs (and bench invocations) through this directory")
    parser.add_argument("--repeat", type=int, default=1, help="run each scenario N times")
    parser.add_argument("--timeout-s", type=float, default=240.0, help="per-run timeout for the hercules subprocess")
    parser.add_argument("--port", type=int, default=0, help="serve the app on this port (default: a free port)")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--cli-arg", action="append", default=[], metavar="ARG", help="extra argument for `hercules run` (repeatable), e.g. --cli-arg=--mode=pure")
    parser.add_argument("--hercules", default=None, help="path to the hercules executable (default: next to this Python)")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR, help="where to write <timestamp>/report.md")
    parser.add_argument("--list", action="store_true", help="list the selected scenarios and exit")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    return args


def _progress(outcome: Outcome, done: int, total: int) -> None:
    mark = "ok " if outcome.correct and not outcome.wrong_reason else ("FALSE PASS" if outcome.false_pass else "MISS")
    where = f" step {outcome.failing_step}:" if outcome.failing_step else ""
    detail = f"{where} {_cell(outcome.reason, 100)}" if outcome.reason and outcome.actual != "passed" else ""
    print(f"[{done}/{total}] {mark:4} {outcome.scenario:36} expected {outcome.expected:6} got {outcome.actual:6} {outcome.duration_s:6.1f}s{detail}", flush=True)


def run_bench(args: argparse.Namespace) -> int:
    scenarios = select(load_manifest(), tags=args.tags, only=args.only)
    if not scenarios:
        print("no scenarios match the filters", file=sys.stderr)
        return 2
    if args.list:
        for s in scenarios:
            print(f"{s.name:36} {s.expected_status:6} {','.join(s.tags)}")
        return 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = args.results_dir / stamp
    workdir = outdir / "projects"
    workdir.mkdir(parents=True, exist_ok=True)
    hercules = hercules_executable(args.hercules)
    env = run_env(outdir / "hercules-home")
    cli_base_url = cli_supports_base_url(hercules, env)
    outcomes: list[Outcome] = []
    total = len(scenarios) * args.repeat
    with serving(args.port) as base_url:
        label = " ".join(args.cli_arg) or "--mode auto"
        meta = {"started": stamp, "decider": label, "repeat": args.repeat, "base_url": base_url, "base_url_mode": "--base-url" if cli_base_url else "case templating", "hercules": hercules}
        print(f"Demo Shop at {base_url}; {total} run(s) with {label}; results in {outdir}", flush=True)
        for repeat in range(1, args.repeat + 1):
            for scenario in scenarios:
                outcome = run_one(scenario, repeat=repeat, base_url=base_url, workdir=workdir, hercules=hercules, env=env, args=args, cli_base_url=cli_base_url)
                outcomes.append(outcome)
                _progress(outcome, len(outcomes), total)
    summary = write_results(outdir, outcomes, meta)
    print(
        f"\naccuracy {summary['accuracy']:.1%} ({summary['correct']}/{summary['runs']}), false passes {summary['false_passes']}, "
        f"false fails {summary['false_fails']}, errors {summary['errors']}, median {summary['median_duration_s']} s, p95 {summary['p95_duration_s']} s, "
        f"JEV/Clef calls {summary['jev_calls']}, fallbacks {summary['fallbacks']}, JEV cost ${summary['jev_cost_usd']:.4f}, "
        f"model calls per case {summary['model_calls_per_case']} (local {summary['local_calls_per_case']}), cached readings {summary['cached_readings']}/{summary['runs']}"
    )
    print(f"report: {outdir / 'report.md'}")
    return 0 if summary["correct"] == summary["runs"] else 1


def main(argv: Iterable[str] | None = None) -> int:
    return run_bench(parse_args(list(argv) if argv is not None else None))


if __name__ == "__main__":
    sys.exit(main())
