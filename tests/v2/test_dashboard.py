"""The home page's numbers: the period and the one before, pass rate, median duration, cost, runs per day."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from testzeus_hercules.daemon import dashboard as home
from tests.v2.fixtures.daemon_support import client_for, make_config, submit, wait_for

NOW = datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc)


def _row(run_id: str, status: str, hours_ago: float, seconds: float | None = 60.0) -> dict[str, Any]:
    created = NOW - timedelta(hours=hours_ago)
    started = created + timedelta(seconds=2)
    finished = started + timedelta(seconds=seconds) if seconds is not None else None
    return {
        "id": run_id,
        "status": status,
        "created_at": created.isoformat(timespec="milliseconds"),
        "started_at": started.isoformat(timespec="milliseconds"),
        "finished_at": finished.isoformat(timespec="milliseconds") if finished else None,
    }


ROWS = [
    _row("a", "passed", 1, 30),
    _row("b", "passed", 5, 90),
    _row("c", "failed", 30, 120),
    _row("d", "error", 50, 10),
    _row("e", "cancelled", 60, 5),
    _row("f", "running", 0.2, None),
    _row("g", "passed", 8 * 24, 60),  # the week before
    _row("h", "failed", 10 * 24, 60),
    _row("i", "passed", 20 * 24, 60),  # older than both periods: only too old for the chart as well
]
COSTS = {"a": 0.05, "b": 0.10, "c": 0.20, "g": 0.40}


def _summary(rows: list[dict[str, Any]] = ROWS, **kwargs: Any) -> dict[str, Any]:
    options: dict[str, Any] = {"now": NOW, "days": 7, "tz": timezone.utc, "cost_of": COSTS.get, **kwargs}
    return home.summarize(rows, **options)


def test_the_period_counts_runs_pass_rate_duration_and_cost() -> None:
    current = _summary()["current"]

    assert current["runs"] == 6 and current["finished"] == 5
    assert (current["passed"], current["failed"]) == (2, 2)
    assert current["pass_rate"] == 0.5  # cancelled runs are left out of the rate
    assert current["median_duration_s"] == 30  # of 30, 90, 120, 10, 5 seconds
    assert current["cost_usd"] == 0.35 and current["costed_runs"] == 3


def test_the_period_before_is_counted_on_its_own() -> None:
    previous = _summary()["previous"]

    assert previous["runs"] == 2 and previous["pass_rate"] == 0.5
    assert previous["cost_usd"] == 0.4 and previous["costed_runs"] == 1


def test_an_empty_history_has_no_rates() -> None:
    summary = _summary([])

    assert summary["current"] == {"runs": 0, "finished": 0, "passed": 0, "failed": 0, "pass_rate": None, "median_duration_s": None, "cost_usd": None, "costed_runs": 0}
    assert len(summary["daily"]) == 14 and all(day["passed"] == day["failed"] == day["other"] == 0 for day in summary["daily"])


def test_runs_per_day_cover_fourteen_days_ending_today() -> None:
    daily = _summary()["daily"]

    assert [day["date"] for day in daily][0] == "2026-11-05" and daily[-1]["date"] == "2026-11-18"
    assert daily[-1] == {"date": "2026-11-18", "passed": 2, "failed": 0, "other": 1}  # a, b and the running f
    assert daily[-2] == {"date": "2026-11-17", "passed": 0, "failed": 1, "other": 0}  # c, 30 hours ago
    assert sum(day["passed"] + day["failed"] + day["other"] for day in daily) == 8  # i is older than the chart


def test_days_are_the_viewers_calendar_days() -> None:
    late = [_row("late", "passed", 19.5)]  # 22:30 UTC on the 17th: the 18th in Kolkata

    assert _summary(late)["daily"][-2]["passed"] == 1
    kolkata = _summary(late, tz=ZoneInfo("Asia/Kolkata"))
    assert kolkata["daily"][-1] == {"date": "2026-11-18", "passed": 1, "failed": 0, "other": 0}
    assert kolkata["time_zone"] == "Asia/Kolkata"


def test_unknown_time_zones_fall_back_to_utc() -> None:
    for name in (None, "", "Mars/Olympus", "../../etc/passwd", "x" * 200):
        assert home.time_zone(name) == timezone.utc
    assert str(home.time_zone("Europe/Berlin")) == "Europe/Berlin"


def test_only_the_newest_runs_are_costed(monkeypatch: Any) -> None:
    monkeypatch.setattr(home, "MAX_COSTED_RUNS", 2)
    asked: list[str] = []

    def cost_of(run_id: str) -> float | None:
        asked.append(run_id)
        return COSTS.get(run_id)

    summary = _summary(cost_of=cost_of)

    assert sorted(asked) == ["a", "b"] and summary["current"]["cost_usd"] == 0.15


def test_the_earliest_row_needed_covers_two_periods_and_the_chart() -> None:
    assert home.earliest_needed(NOW, 7, timezone.utc) == NOW - timedelta(days=14)  # 4 Nov 18:00, before the chart's 5 Nov
    assert home.earliest_needed(NOW, 3, timezone.utc) == datetime(2026, 11, 5, tzinfo=timezone.utc)  # the chart's first midnight
    assert home.earliest_needed(NOW, 30, timezone.utc) == NOW - timedelta(days=60)


def test_a_runs_cost_is_read_from_its_result_and_remembered(tmp_path: Path) -> None:
    run_dir = tmp_path / "r_cost"
    run_dir.mkdir()
    result = {"run_id": "r_cost", "status": "cancelled", "case_id": "demo", "steps": [], "duration_s": 1.0, "cost": {"tokens_in": 10, "tokens_out": 5, "usd": 0.25}}
    (run_dir / "run.json").write_text(json.dumps(result))

    assert home.run_cost(tmp_path, "r_cost") == 0.25
    (run_dir / "run.json").unlink()
    assert home.run_cost(tmp_path, "r_cost") == 0.25  # remembered
    assert home.run_cost(tmp_path, "r_missing") is None


def test_the_dashboard_route_counts_the_projects_runs(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        empty = client.get("/api/dashboard").json()
        passed = submit(client)
        failed = submit(client, "fake-fail")
        wait_for(client, passed)
        wait_for(client, failed)
        body = client.get("/api/dashboard", params={"days": 30, "tz": "Asia/Kolkata"}).json()
        bad = client.get("/api/dashboard", params={"days": 0})
        view = client.get(f"/api/runs/{passed}").json()

    assert empty["current"]["runs"] == 0 and empty["days"] == 7 and empty["time_zone"] == "UTC"
    assert body["days"] == 30 and body["time_zone"] == "Asia/Kolkata"
    assert body["current"]["runs"] == 2 and body["current"]["pass_rate"] == 0.5
    assert body["current"]["median_duration_s"] is not None
    assert body["daily"][-1]["passed"] == 1 and body["daily"][-1]["failed"] == 1
    assert bad.status_code == 422
    assert "environment" in view and view["environment"] is None  # the demo case names no saved environment
