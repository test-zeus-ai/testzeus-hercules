"""``reverify``: rerun the cases a person kept, and move their findings by the result.

A kept (``known_bug``) case that passes moves its open finding to ``verify_fixed`` (a person then closes it); a
regression case that fails reopens its finding as ``new``. A run that ends any other way (an error, a run waiting for
approval, cancelled, out of time) is **inconclusive**: it changes nothing. The exit code is 1 when a regression case
failed, else 2 when a case was inconclusive, else 0.

The daemon runs this as a background job (``POST /api/explore/reverify``); ``hercules explore reverify`` and the
``hercules_explore_reverify`` MCP tool follow that job. The steps are given as callables so the logic does not depend on
how runs are started or findings triaged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal

__all__ = ["DEFAULT_CASE_TIMEOUT_S", "NO_CASES", "REVERIFY_BY", "Outcome", "chosen_for_reverify", "reverify"]

REVERIFY_BY = "hercules explore reverify"
NO_CASES = "No kept or regression cases. Keep a finding's case with: hercules explore triage FINDING --by NAME --case kept"
DEFAULT_CASE_TIMEOUT_S = 900
_OPEN_FOR_KEPT = ("new", "confirmed", "in_tracker", "verify_fixed", "known_issue")
_CONCLUSIVE = ("passed", "failed")

Outcome = Literal["regression", "verify_fixed", "still_failing", "still_passes", "inconclusive", "skipped"]
StartRun = Callable[[Path], Awaitable[str | None]]
WaitRun = Callable[[str, float], Awaitable[str]]  # (run_id, timeout_s) -> the run's final status, or "timeout"
Triage = Callable[[str, dict[str, Any]], Awaitable[None]]
Say = Callable[[str], None]
Report = Callable[[dict[str, Any]], None]


def chosen_for_reverify(item: Mapping[str, Any]) -> bool:
    """Kept cases of open findings, and every regression case."""
    if not item.get("case_path"):
        return False
    case = item.get("case", "proposed")
    return case == "regression" or (case == "kept" and item.get("state") in _OPEN_FOR_KEPT)


def _result(item: Mapping[str, Any], outcome: Outcome, line: str, *, run_id: str | None = None, run_status: str | None = None) -> dict[str, Any]:
    return {"finding": str(item["id"]), "title": item.get("title"), "case": item.get("case"), "outcome": outcome, "run_id": run_id, "run_status": run_status, "line": line}


async def reverify(
    findings: list[Mapping[str, Any]],
    *,
    project_dir: Path,
    build: str,
    start_run: StartRun,
    wait_run: WaitRun,
    triage: Triage,
    say: Say,
    report: Report = lambda result: None,
    by: str = REVERIFY_BY,
    case_timeout_s: float = DEFAULT_CASE_TIMEOUT_S,
    only: list[str] | None = None,
) -> int:
    """Run every chosen case (all started first, so the daemon's queue runs them in parallel) and report each as it
    finishes. ``only`` limits it to these finding ids. Returns the exit code (1 regression, 2 inconclusive, 0)."""
    candidates = [item for item in findings if only is None or str(item.get("id")) in only]
    chosen = [item for item in candidates if chosen_for_reverify(item)]
    for item in candidates:
        if only is not None and item not in chosen:
            line = f"{item['id']}: its case is {item.get('case', 'proposed')} ({item.get('state')}); only kept cases of open findings and regression cases are rerun; skipped"
            say(line)
            report(_result(item, "skipped", line))
    if not chosen:
        if only is None:
            say(NO_CASES)
        return 0
    started: list[tuple[Mapping[str, Any], str]] = []
    inconclusive = 0
    for item in chosen:
        case_path = project_dir / str(item["case_path"])
        run_id = await start_run(case_path) if case_path.is_file() else None
        if run_id is None:
            gone = not case_path.is_file()
            inconclusive += 0 if gone else 1
            line = f"{item['id']}: its case {item['case_path']} is gone; skipped" if gone else f"{item['id']}: inconclusive, its case could not be started ({item.get('title')})"
            say(line)
            report(_result(item, "skipped" if gone else "inconclusive", line))
            continue
        started.append((item, run_id))
    if started:
        say(f"running {len(started)} case(s)…")
    regressions = 0

    async def settle(item: Mapping[str, Any], run_id: str) -> tuple[Mapping[str, Any], str, str]:
        return item, run_id, await wait_run(run_id, case_timeout_s)

    for done in asyncio.as_completed([settle(item, run_id) for item, run_id in started]):
        item, run_id, status = await done
        regression = item.get("case") == "regression"
        title = item.get("title")
        if status not in _CONCLUSIVE:
            inconclusive += 1
            ended = "ran out of time" if status == "timeout" else f"ended {status}"
            line = f"{item['id']}: inconclusive, its run {run_id} {ended}; nothing changed ({title})"
            say(line)
            report(_result(item, "inconclusive", line, run_id=run_id, run_status=status))
            continue
        passed = status == "passed"
        if regression and not passed:
            regressions += 1
            if item.get("state") != "new":
                await triage(str(item["id"]), {"by": by, "state": "new", "reason": f"its regression case failed on build {build} (run {run_id})"})
            line = f"{item['id']}: REGRESSION, the bug is back ({title})"
            say(line)
            report(_result(item, "regression", line, run_id=run_id, run_status=status))
            continue
        if not regression and passed and item.get("state") != "verify_fixed":
            await triage(str(item["id"]), {"by": by, "state": "verify_fixed", "reason": f"its case passed on build {build} (run {run_id})"})
        verdict = "still passes" if regression else ("passes now: verify_fixed" if passed else "still fails")
        outcome: Outcome = "still_passes" if regression else ("verify_fixed" if passed else "still_failing")
        line = f"{item['id']}: {verdict} ({title})"
        say(line)
        report(_result(item, outcome, line, run_id=run_id, run_status=status))
    return 1 if regressions else 2 if inconclusive else 0
