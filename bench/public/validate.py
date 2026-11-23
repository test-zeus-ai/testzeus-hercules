"""Check the public demo-app scenarios without Hercules.

Two passes:

* **lint** (offline): every scenario file compiles to a Hercules ``TestCase`` with the step count the
  validator expects, and every failing scenario names its ``fail_step``. (Hercules reads every step
  with a model; the ``needs_llm`` tag only marks the scenarios written in looser natural phrasing.)
* **replay** (live): each scenario's intent is carried out with hand-written Playwright locators
  (``pw_checks/``) in a fresh headless Chromium context. A passing scenario must complete every step;
  a failing one must fail at its manifest ``fail_step``. The tool reports whether it agrees with the
  manifest.

The sites are shared public demos: the replay is serial, uses one browser, pauses between scenarios
and never registers accounts or submits real data.

Usage::

    uv run --frozen --python 3.13 python bench/public/validate.py               # lint + replay all
    uv run --frozen --python 3.13 python bench/public/validate.py --lint        # lint only (offline)
    uv run --frozen --python 3.13 python bench/public/validate.py --apps saucedemo --only sd0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

PUBLIC_DIR = Path(__file__).resolve().parent
SCENARIOS_DIR = PUBLIC_DIR / "scenarios"
RESULTS_DIR = PUBLIC_DIR / "results"
DEFAULT_CHROMIUM = Path.home() / "Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
_STEP_LINE = re.compile(r"^\s*(Given|When|Then|And|But|\*)\s+\S")
USER_AGENT = "hercules-public-bench-validator (+https://github.com/test-zeus-ai/testzeus-hercules)"

sys.path.insert(0, str(PUBLIC_DIR))


# --------------------------------------------------------------------------- manifest


@dataclass(frozen=True)
class Entry:
    file: str
    app: str
    base_url: str
    expected_status: str
    tags: tuple[str, ...]
    fail_steps: tuple[int, ...] = ()
    site_status: str = "available"
    notes: str = ""

    @property
    def name(self) -> str:
        return Path(self.file).stem

    @property
    def path(self) -> Path:
        return SCENARIOS_DIR / self.file


def _fail_steps(value: Any) -> tuple[int, ...]:
    if value is None:
        return ()
    return tuple(int(v) for v in value) if isinstance(value, list) else (int(value),)


def load_manifest(path: Path = PUBLIC_DIR / "manifest.yaml") -> list[Entry]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = []
    for item in raw.get("scenarios", []):
        if item["expected_status"] not in ("passed", "failed"):
            raise ValueError(f"{item['file']}: expected_status must be passed or failed")
        entries.append(
            Entry(
                file=item["file"],
                app=item["app"],
                base_url=item["base_url"],
                expected_status=item["expected_status"],
                tags=tuple(item.get("tags") or ()),
                fail_steps=_fail_steps(item.get("fail_step")),
                site_status=item.get("site_status", "available"),
                notes=item.get("notes", ""),
            )
        )
    return entries


def select(entries: Sequence[Entry], *, apps: Sequence[str], tags: Sequence[str], only: Sequence[str]) -> list[Entry]:
    chosen = [e for e in entries if not apps or e.app in apps]
    chosen = [e for e in chosen if not tags or set(tags) & set(e.tags)]
    return [e for e in chosen if not only or any(e.name.startswith(term) for term in only)]


def step_count(path: Path) -> int:
    if path.suffix == ".json":
        return len(json.loads(path.read_text(encoding="utf-8"))["steps"])
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if _STEP_LINE.match(line))


# --------------------------------------------------------------------------- lint


def lint(entries: Sequence[Entry]) -> list[str]:
    """Problems with the scenario files; an empty list means they are consistent with their tags."""
    try:
        from testzeus_hercules.cases.gherkin import load_feature
        from testzeus_hercules.cases.models import TestCase
    except ImportError as exc:  # lint is best effort
        return [f"lint skipped: cannot import Hercules ({exc})"]
    problems = []
    for entry in entries:
        if not entry.path.is_file():
            problems.append(f"{entry.file}: file missing")
            continue
        try:
            case = TestCase.model_validate_json(entry.path.read_text(encoding="utf-8")) if entry.path.suffix == ".json" else load_feature(entry.path)
        except ValueError as exc:
            problems.append(f"{entry.file}: does not compile ({str(exc).splitlines()[0]})")
            continue
        if len(case.steps) != step_count(entry.path):
            problems.append(f"{entry.file}: step count {len(case.steps)} differs from the validator's count {step_count(entry.path)}")
        if entry.expected_status == "failed" and not entry.fail_steps:
            problems.append(f"{entry.file}: failed scenario without fail_step")
        if any(step > len(case.steps) for step in entry.fail_steps):
            problems.append(f"{entry.file}: fail_step beyond the last step")
        if f"app:{entry.app}" not in entry.tags:
            problems.append(f"{entry.file}: missing tag app:{entry.app}")
    return problems


# --------------------------------------------------------------------------- replay


@dataclass
class Verdict:
    scenario: str
    app: str
    expected: str
    fail_steps: list[int]
    actual: str  # passed | failed | error | skipped
    failed_step: int | None = None
    reason: str = ""
    steps_total: int = 0
    steps_covered: int = 0
    agreed: bool = False
    duration_s: float = 0.0
    dialogs: list[str] = field(default_factory=list)
    page_text: str = ""


def probe(url: str, timeout_s: float = 25.0) -> str:
    """'available' when the site answers with a non-5xx status."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed public demo URLs
            return "available" if response.status < 500 else "unavailable"
    except urllib.error.HTTPError as exc:
        return "available" if exc.code < 500 else "unavailable"
    except (urllib.error.URLError, TimeoutError, OSError):
        return "unavailable"


def judge(entry: Entry, verdict: Verdict) -> Verdict:
    if verdict.actual in ("error", "skipped"):
        verdict.agreed = False
    elif entry.expected_status == "passed":
        verdict.agreed = verdict.actual == "passed" and verdict.steps_covered == verdict.steps_total
        if verdict.actual == "passed" and not verdict.agreed:
            verdict.reason = f"validator covered {verdict.steps_covered} of {verdict.steps_total} steps"
    else:
        verdict.agreed = verdict.actual == "failed" and verdict.failed_step in entry.fail_steps
    return verdict


def replay(entry: Entry, browser: Any, *, timeout_s: float, headed: bool) -> Verdict:
    from pw_checks import CHECKS, Flow, StepFailed

    verdict = Verdict(entry.name, entry.app, entry.expected_status, list(entry.fail_steps), actual="error", steps_total=step_count(entry.path))
    check = CHECKS.get(entry.name)
    if check is None:
        verdict.reason = "no replay written for this scenario"
        return verdict
    context = browser.new_context(viewport={"width": 1366, "height": 900}, user_agent=None)
    flow = Flow(context.new_page(), entry.base_url, timeout_s=timeout_s)
    started = time.monotonic()
    try:
        check(flow)
        verdict.actual = "passed"
    except StepFailed as exc:
        verdict.actual, verdict.failed_step, verdict.reason = "failed", exc.step, exc.reason
        verdict.page_text = " ".join(flow.text().split())[:600]
    except Exception as exc:  # noqa: BLE001 - a bug in a replay is reported, not raised
        verdict.reason = f"replay error at step {flow.current}: {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
    finally:
        verdict.duration_s = round(time.monotonic() - started, 1)
        verdict.steps_covered = flow.max_step
        verdict.dialogs = list(flow.dialogs)
        context.close()
    return judge(entry, verdict)


def run_replays(entries: Sequence[Entry], args: argparse.Namespace) -> list[Verdict]:
    from playwright.sync_api import sync_playwright

    status = {}
    for app, url in sorted({(e.app, e.base_url) for e in entries}):
        status[app] = "available" if args.no_probe else probe(url)
        print(f"{app:10} {url:45} {status[app]}", flush=True)
    verdicts: list[Verdict] = []
    launch: dict[str, Any] = {"headless": not args.headed}
    if args.chromium and Path(args.chromium).is_file():
        launch["executable_path"] = str(args.chromium)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch)
        try:
            for done, entry in enumerate(entries, start=1):
                if status[entry.app] != "available":
                    verdict = Verdict(entry.name, entry.app, entry.expected_status, list(entry.fail_steps), actual="skipped", reason="site unavailable", steps_total=step_count(entry.path))
                else:
                    verdict = replay(entry, browser, timeout_s=args.timeout_s, headed=args.headed)
                    time.sleep(args.pause_s)
                verdicts.append(verdict)
                _progress(verdict, done, len(entries))
        finally:
            browser.close()
    return verdicts


def _progress(v: Verdict, done: int, total: int) -> None:
    mark = "agree" if v.agreed else ("skip " if v.actual == "skipped" else "DISAGREE")
    where = f" at step {v.failed_step}" if v.failed_step else ""
    wanted = v.expected + (f"@{','.join(map(str, v.fail_steps))}" if v.fail_steps else "")
    print(f"[{done}/{total}] {mark:8} {v.scenario:42} want {wanted:12} got {v.actual}{where} ({v.duration_s}s) {v.reason[:140]}", flush=True)


# --------------------------------------------------------------------------- CLI


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lint and replay the public demo-app scenarios with plain Playwright (no Hercules).")
    parser.add_argument("--apps", type=_csv, default=[], help="comma-separated apps (saucedemo, orangehrm, parabank, xyzbank)")
    parser.add_argument("--tags", type=_csv, default=[], help="comma-separated tags; scenarios having any of them")
    parser.add_argument("--only", type=_csv, default=[], help="comma-separated scenario names or name prefixes")
    parser.add_argument("--lint", action="store_true", help="lint only; do not open a browser")
    parser.add_argument("--no-lint", action="store_true", help="skip the lint pass")
    parser.add_argument("--no-probe", action="store_true", help="do not probe the sites before replaying")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--pause-s", type=float, default=2.0, help="pause between scenarios, to be polite to the shared demos")
    parser.add_argument("--timeout-s", type=float, default=15.0, help="default per-action and per-check timeout")
    parser.add_argument("--chromium", type=Path, default=DEFAULT_CHROMIUM, help="Chromium executable (default: the local Playwright cache when present)")
    parser.add_argument("--out", type=Path, default=None, help="write verdicts as JSON here (default: results/validate-<timestamp>.json)")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(list(argv) if argv is not None else None)
    entries = select(load_manifest(), apps=args.apps, tags=args.tags, only=args.only)
    if not entries:
        print("no scenarios match the filters", file=sys.stderr)
        return 2
    problems = [] if args.no_lint else lint(entries)
    for problem in problems:
        print(f"lint: {problem}")
    if not args.no_lint:
        print(f"lint: {len(entries)} scenario(s), {len(problems)} problem(s)")
    if args.lint:
        return 1 if problems else 0
    verdicts = run_replays(entries, args)
    agreed = sum(v.agreed for v in verdicts)
    skipped = sum(v.actual == "skipped" for v in verdicts)
    print(f"\nreplay: {agreed}/{len(verdicts)} agree with the manifest, {skipped} skipped (site unavailable), {len(verdicts) - agreed - skipped} disagree")
    out = args.out or RESULTS_DIR / f"validate-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"lint_problems": problems, "verdicts": [asdict(v) for v in verdicts]}, indent=2), encoding="utf-8")
    print(f"verdicts: {out}")
    return 0 if agreed + skipped == len(verdicts) and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
