"""The home page's numbers (``GET /api/dashboard``): runs, pass rate, median duration and model cost over the last
``days`` days next to the period before, and runs per day for the chart.

Counts, statuses and durations come from the run history; the cost of a finished run from its ``run.json``, read once
and remembered (a finished run's result does not change). Days are the viewer's calendar days, in the time zone the
page names (``tz``), else UTC.
"""

from __future__ import annotations

import statistics
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from testzeus_hercules.daemon.store import FINAL_STATUSES

CHART_DAYS = 14
MAX_COSTED_RUNS = 2000  # run.json files read for one answer, newest first; older runs count without a cost
_FAILED = frozenset({"failed", "error"})

_costs: dict[str, float] = {}
_costs_lock = threading.Lock()


def time_zone(name: str | None) -> tzinfo:
    """The named IANA time zone, or UTC when there is none or it is unknown."""
    if not name or len(name) > 64:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _when(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def run_cost(runs_dir: Path, run_id: str) -> float | None:
    """The model cost of a finished run in USD, from its ``run.json``; remembered once found."""
    with _costs_lock:
        if run_id in _costs:
            return _costs[run_id]
    from testzeus_hercules.daemon.manager import read_result

    result = read_result(runs_dir / run_id)
    usd = result.cost.usd if result is not None else None
    if usd is not None:
        with _costs_lock:
            _costs[run_id] = float(usd)
    return usd


def _period(rows: list[Mapping[str, Any]], cost_of: Callable[[str], float | None], costed: set[str]) -> dict[str, Any]:
    finished = [row for row in rows if row["status"] in FINAL_STATUSES]
    judged = [row for row in finished if row["status"] != "cancelled"]
    passed = sum(1 for row in judged if row["status"] == "passed")
    durations = []
    for row in finished:
        started, ended = _when(row.get("started_at")), _when(row.get("finished_at"))
        if started and ended and ended >= started:
            durations.append((ended - started).total_seconds())
    costs = [cost for cost in (cost_of(row["id"]) for row in finished if row["id"] in costed) if cost is not None]
    return {
        "runs": len(rows),
        "finished": len(finished),
        "passed": passed,
        "failed": sum(1 for row in judged if row["status"] in _FAILED),
        "pass_rate": passed / len(judged) if judged else None,
        "median_duration_s": statistics.median(durations) if durations else None,
        "cost_usd": round(sum(costs), 4) if costs else None,
        "costed_runs": len(costs),
    }


def summarize(rows: Iterable[Mapping[str, Any]], *, now: datetime, days: int, tz: tzinfo, cost_of: Callable[[str], float | None]) -> dict[str, Any]:
    """The dashboard for ``rows`` (from :meth:`RunStore.run_times`, covering at least twice ``days`` and the chart)."""
    listed = [row for row in rows if _when(row.get("created_at")) is not None]
    current_start, previous_start = now - timedelta(days=days), now - timedelta(days=2 * days)
    current = [row for row in listed if current_start <= _when(row["created_at"]) <= now]  # type: ignore[operator]
    previous = [row for row in listed if previous_start <= _when(row["created_at"]) < current_start]  # type: ignore[operator]
    newest_first = sorted((row for row in current + previous if row["status"] in FINAL_STATUSES), key=lambda row: row["created_at"], reverse=True)
    costed = {row["id"] for row in newest_first[:MAX_COSTED_RUNS]}

    today = now.astimezone(tz).date()
    first_day = today - timedelta(days=CHART_DAYS - 1)
    chart = {first_day + timedelta(days=offset): {"passed": 0, "failed": 0, "other": 0} for offset in range(CHART_DAYS)}
    for row in listed:
        day = _when(row["created_at"]).astimezone(tz).date()  # type: ignore[union-attr]
        if day in chart:
            bucket = "passed" if row["status"] == "passed" else "failed" if row["status"] in _FAILED else "other"
            chart[day][bucket] += 1
    return {
        "days": days,
        "time_zone": str(tz),
        "generated_at": now.isoformat(timespec="seconds"),
        "current": _period(current, cost_of, costed),
        "previous": _period(previous, cost_of, costed),
        "daily": [{"date": day.isoformat(), **counts} for day, counts in chart.items()],
    }


def earliest_needed(now: datetime, days: int, tz: tzinfo) -> datetime:
    """How far back the rows must go: two periods, and the first chart day from its local midnight."""
    first_day = now.astimezone(tz).date() - timedelta(days=CHART_DAYS - 1)
    chart_start = datetime.combine(first_day, datetime.min.time(), tzinfo=tz)
    return min(now - timedelta(days=2 * days), chart_start)
