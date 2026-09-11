"""Offline tests for testzeus_hercules.reporting.run_db.

Builds a synthetic merged JUnit XML (same pattern as tests/test_reporting_bundle.py),
records a full run lifecycle into a tmp_path SQLite DB and verifies queries, trend
aggregation and the record_run_summary bridge. No network, no LLM.
"""

import asyncio
import os
import sqlite3
import threading
from xml.sax.saxutils import escape

import pytest
from testzeus_hercules.config import SingletonConfigManager, set_global_conf
from testzeus_hercules.reporting import run_db
from testzeus_hercules.reporting.bundle import parse_junit_testcases
from testzeus_hercules.reporting.run_db import (
    RunHistory,
    get_default_history,
    record_run_summary,
)

PROOFS_PROPERTY_NAME = "Proofs Base Folder, includes screenshots, recording, netwrok logs, api logs, sec logs, accessibility logs"

FAILED_CASE_NAME = "Scenario: Search fails: bike count wrong"
PASSED_CASE_NAME = "Scenario: User filters bikes with max price"
BUNDLE_DIR_NAME = "Scenario__Search_fails__bike_count_wrong"


def _property_xml(name: str, value: str) -> str:
    return f'        <property name="{escape(name, {chr(34): "&quot;"})}" value="{escape(value, {chr(34): "&quot;"})}"/>'


def _write_junit_xml(tmp_path, junit_path) -> None:
    """Two testcases: one passed (flat cost props, empty video prop), one failed (evidence + nested cost prop)."""
    bundle_dir = junit_path.parent / "bundles" / BUNDLE_DIR_NAME
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "bundle.md").write_text("# Failure bundle", encoding="utf-8")

    passed_props = "\n".join(
        [
            _property_xml("Terminate", "yes"),
            _property_xml("total_cost", "0.05"),
            _property_xml("usage_including_cached_inference.total_tokens", "100"),
            _property_xml("Proofs Video", ""),
        ]
    )
    failed_props = "\n".join(
        [
            _property_xml("Terminate", "no"),
            _property_xml("Proofs Video", str(tmp_path / "proofs" / "recording.webm")),
            _property_xml("Proofs Screenshot", str(tmp_path / "proofs" / "screenshots")),
            _property_xml("Network Logs", str(tmp_path / "network_logs.json")),
            _property_xml("Agents Internal Logs", str(tmp_path / "logs")),
            _property_xml("Planner Thoughts", str(tmp_path / "planner_thoughts.json")),
            _property_xml(PROOFS_PROPERTY_NAME, str(tmp_path / "proofs")),
            _property_xml("usage_including_cached_inference.total_cost", "0.12"),
        ]
    )
    xml = f"""<testsuites>
  <testsuite name="Feature: Filter Bikes" tests="2" failures="1" skipped="0" time="9.0">
    <testcase name="{escape(PASSED_CASE_NAME)}" classname="Feature: Filter Bikes" time="1.5">
      <properties>
{passed_props}
      </properties>
      <system-out>Final Response: ok</system-out>
    </testcase>
    <testcase name="{escape(FAILED_CASE_NAME)}" classname="Feature: Filter Bikes" time="7.5">
      <properties>
{failed_props}
      </properties>
      <failure message="Assertion failed: expected 3 bikes, got 2" type="AssertionError"></failure>
      <system-out>Final Response: got 2 bikes</system-out>
      <system-out>usage_including_cached_inference.total_cost: 0.12</system-out>
    </testcase>
  </testsuite>
</testsuites>
"""
    junit_path.write_text(xml, encoding="utf-8")


def _summary(junit_path) -> dict:
    """Summary dict shaped like __main__.sequential_process output, plus cost/token meta."""
    return {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "errors": 0,
        "junit_xml": str(junit_path),
        "html": str(junit_path).replace(".xml", ".html"),
        "report_md": str(junit_path.parent / "report.md"),
        "bundles": [str(junit_path.parent / "bundles" / BUNDLE_DIR_NAME)],
        "feature_file": str(junit_path.parent / "login.feature"),
        "meta": {"total_cost": 0.17, "total_tokens": "321"},
    }


def _make_db(tmp_path) -> RunHistory:
    return RunHistory(db_url=f"sqlite:///{tmp_path / 'history.db'}")


def test_full_lifecycle(tmp_path) -> None:
    db = _make_db(tmp_path)
    junit_path = tmp_path / "final_result.xml"
    _write_junit_xml(tmp_path, junit_path)

    run_id = db.start_run(meta={"note": "lifecycle"})
    assert run_id.startswith("run_")

    cases = parse_junit_testcases(str(junit_path))
    assert [db.record_case(run_id, case) for case in cases] == [1, 2]

    db.record_steps(
        run_id,
        FAILED_CASE_NAME,
        [
            {"idx": 1, "type": "navigation", "description": "open page", "status": "passed", "duration_s": 1.2, "error": None},
            {"idx": 2, "type": "assertion", "description": "check bikes", "status": "failed", "duration_s": 0.3, "error": "expected 3 got 2"},
        ],
    )
    db.record_artifact(run_id, None, "report", str(tmp_path / "extra_report.md"))
    db.record_healing_event(run_id, FAILED_CASE_NAME, "heal_proposal", canonical_name="submit_button", detail="css=#submit", status="proposed")
    db.record_healing_event(run_id, None, "classification", detail="network related")

    db.finish_run(run_id, summary=_summary(junit_path))

    got = db.get_run(run_id)
    assert got["status"] == "failed"
    assert got["total"] == 2 and got["passed"] == 1 and got["failed"] == 1 and got["errors"] == 0
    assert got["total_cost"] == pytest.approx(0.17)
    assert got["total_tokens"] == 321
    assert got["junit_xml"] == str(junit_path)
    assert got["feature_file"] == str(tmp_path / "login.feature")
    # finish_run merges the summary meta into the run meta recorded at start.
    assert got["meta"] == {"note": "lifecycle", "total_cost": 0.17, "total_tokens": "321"}
    assert got["finished_at"] is not None
    assert got["duration_s"] is not None

    by_name = {case["case_key"]: case for case in got["cases"]}
    failed_case = by_name[FAILED_CASE_NAME]
    passed_case = by_name[PASSED_CASE_NAME]

    assert failed_case["status"] == "failed"
    assert failed_case["failure_message"] == "Assertion failed: expected 3 bikes, got 2"
    assert failed_case["final_response"] == "got 2 bikes"
    # Cost from the nested ".total_cost" property key. bundle_dir stays None: the junit
    # path is only written by finish_run, after the cases were recorded here.
    assert failed_case["cost"] == pytest.approx(0.12)
    assert failed_case["bundle_dir"] is None
    assert {artifact["kind"] for artifact in failed_case["artifacts"]} == {"video", "screenshot", "network", "log", "bundle"}
    assert len(failed_case["healing_events"]) == 1
    assert failed_case["healing_events"][0]["canonical_name"] == "submit_button"

    # Cost from the flat "total_cost" property; empty evidence value records no artifact.
    assert passed_case["cost"] == pytest.approx(0.05)
    assert passed_case["artifacts"] == []
    assert passed_case["final_response"] == "ok"
    assert passed_case["duration_s"] == pytest.approx(1.5)

    assert [artifact["kind"] for artifact in got["artifacts"]] == ["report"]
    # Run-level healing only; the case-level event is nested under its case.
    assert len(got["healing_events"]) == 1

    detail = db.get_case_detail(run_id, FAILED_CASE_NAME)
    assert [step["idx"] for step in detail["steps"]] == [1, 2]
    assert detail["steps"][1]["status"] == "failed"
    assert detail["steps"][1]["error"] == "expected 3 got 2"
    # Six evidence properties have values; Internal Logs and Planner Thoughts both map to kind "log".
    assert len(detail["artifacts"]) == 6


def test_record_run_summary_end_to_end(tmp_path) -> None:
    db = _make_db(tmp_path)
    run_dir = tmp_path / "run_20260101_120000"
    run_dir.mkdir()
    junit_path = run_dir / "final_result.xml"
    _write_junit_xml(tmp_path, junit_path)

    run_id = record_run_summary(_summary(junit_path), db=db)
    # run_id defaults to the junit output dir name.
    assert run_id == "run_20260101_120000"

    got = db.get_run(run_id)
    assert sorted(case["status"] for case in got["cases"]) == ["failed", "passed"]
    assert got["status"] == "failed"
    assert got["finished_at"] is not None

    failed_case = next(case for case in got["cases"] if case["status"] == "failed")
    # bundle_dir derived from <junit_dir>/bundles/<name> because run outputs were set before recording.
    assert failed_case["bundle_dir"] == str(junit_path.parent / "bundles" / BUNDLE_DIR_NAME)
    assert failed_case["artifacts"]

    # Run-level artifacts: failure bundles (kind=bundle) and the markdown report (kind=report).
    kinds = {artifact["kind"] for artifact in got["artifacts"]}
    assert kinds == {"bundle", "report"}


def test_record_run_summary_missing_junit_does_not_raise(tmp_path) -> None:
    db = _make_db(tmp_path)
    summary = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "junit_xml": str(tmp_path / "missing.xml"), "html": None, "report_md": None, "bundles": [], "feature_file": None}

    run_id = record_run_summary(summary, db=db)

    got = db.get_run(run_id)
    assert got["status"] == "passed"
    assert got["cases"] == []
    assert got["artifacts"] == []


def test_list_runs_newest_first_with_counts(tmp_path) -> None:
    db = _make_db(tmp_path)
    db.start_run(run_id="run_old")
    db.start_run(run_id="run_new")
    db.record_case("run_new", {"name": "Scenario: A", "classname": "Feature: F", "status": "passed", "time": 1.0, "properties": {}})
    db.record_case("run_new", {"name": "Scenario: B", "classname": "Feature: F", "status": "failed", "time": 2.0, "properties": {}})
    db.finish_run("run_old", summary={"total": 1, "passed": 1, "failed": 0, "errors": 0})

    runs = db.list_runs()

    assert [entry["run_id"] for entry in runs] == ["run_new", "run_old"]
    new_entry, old_entry = runs
    assert new_entry["case_count"] == 2 and new_entry["case_passed"] == 1 and new_entry["case_failed"] == 1
    assert new_entry["pass_rate"] == pytest.approx(0.5)
    # Run without recorded cases falls back to the counters written by finish_run.
    assert old_entry["case_count"] == 0
    assert old_entry["passed"] == 1 and old_entry["pass_rate"] == pytest.approx(1.0)


def test_get_trend_pass_rate_and_flaky_detection(tmp_path) -> None:
    db = _make_db(tmp_path)
    db.start_run(run_id="run_1")
    db.start_run(run_id="run_2")
    db.record_case("run_1", {"name": "Scenario: Flaky", "classname": "Feature: F", "status": "passed", "time": 1.0, "properties": {}})
    db.record_case("run_1", {"name": "Scenario: Stable", "classname": "Feature: F", "status": "passed", "time": 1.0, "properties": {}})
    db.record_case("run_2", {"name": "Scenario: Flaky", "classname": "Feature: F", "status": "failed", "time": 1.0, "properties": {}})
    db.record_case("run_2", {"name": "Scenario: Stable", "classname": "Feature: F", "status": "passed", "time": 1.0, "properties": {}})

    trend = db.get_trend()

    by_run = {entry["run_id"]: entry for entry in trend["runs"]}
    assert by_run["run_1"]["pass_rate"] == pytest.approx(1.0)
    assert by_run["run_2"]["pass_rate"] == pytest.approx(0.5)
    assert trend["aggregate"]["avg_pass_rate"] == pytest.approx(0.75)

    flaky = trend["aggregate"]["flakiest_cases"]
    assert len(flaky) == 1
    assert flaky[0]["case_key"] == "Scenario: Flaky"
    assert flaky[0]["runs"] == 2 and flaky[0]["passed"] == 1 and flaky[0]["failed"] == 1


def test_start_run_generates_unique_ids_and_raises_on_unknown_run(tmp_path) -> None:
    db = _make_db(tmp_path)
    first = db.start_run()
    second = db.start_run()
    assert first != second and first.startswith("run_") and second.startswith("run_")

    with pytest.raises(ValueError):
        db.get_run("no_such_run")
    with pytest.raises(ValueError):
        db.record_case("no_such_run", {"name": "x", "status": "passed", "properties": {}})


def test_default_db_uses_project_source_root(tmp_path) -> None:
    SingletonConfigManager.reset_instance()
    os.environ.setdefault("IS_TEST_ENV", "true")
    try:
        set_global_conf({"PROJECT_SOURCE_ROOT": str(tmp_path), "LLM_MODEL_NAME": "test-model", "LLM_MODEL_API_KEY": "test-key"}, ignore_env=True)
        db = RunHistory()
        assert db.db_url == f"sqlite:///{tmp_path / 'hercules_history.db'}"
        assert (tmp_path / "hercules_history.db").exists()
    finally:
        SingletonConfigManager.reset_instance()


def test_get_default_history_is_cached_singleton(tmp_path, monkeypatch) -> None:
    SingletonConfigManager.reset_instance()
    monkeypatch.setattr(run_db, "_default_history", None)
    try:
        set_global_conf({"PROJECT_SOURCE_ROOT": str(tmp_path), "LLM_MODEL_NAME": "test-model", "LLM_MODEL_API_KEY": "test-key"}, ignore_env=True)
        first = get_default_history()
        second = get_default_history()
        assert first is second
        assert isinstance(first, RunHistory)
        assert first.db_url == f"sqlite:///{tmp_path / 'hercules_history.db'}"
    finally:
        run_db._default_history = None
        SingletonConfigManager.reset_instance()


# ── hardening: thread-safe singleton, dispose-on-reset, WAL ───────────────────


def test_concurrent_threaded_writers_succeed_with_wal(tmp_path) -> None:
    # Four threads hammering one engine must not trip "database is locked"
    # (WAL journaling + busy timeout from the connect listener).
    db = _make_db(tmp_path)
    db.start_run(run_id="shared")
    errors: list[Exception] = []

    def _worker(worker_id: int) -> None:
        try:
            for index in range(25):
                db.record_case("shared", {"name": f"Scenario: w{worker_id}-{index}", "classname": "Feature: F", "status": "passed", "time": 0.1, "properties": {}})
        except Exception as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(worker_id,)) for worker_id in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    got = db.get_run("shared")
    assert len(got["cases"]) == 100

    # WAL is active on the database file itself.
    db.engine.dispose()

    connection = sqlite3.connect(tmp_path / "history.db")
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        connection.close()


def test_get_default_history_thread_safe_single_creation(tmp_path, monkeypatch) -> None:
    SingletonConfigManager.reset_instance()
    monkeypatch.setattr(run_db, "_default_history", None)
    creation_lock = threading.Lock()
    creations = {"n": 0}

    class _CountingHistory(RunHistory):
        def __init__(self, db_url=None):
            with creation_lock:
                creations["n"] += 1
            super().__init__(db_url=db_url)

    monkeypatch.setattr(run_db, "RunHistory", _CountingHistory)
    os.environ.setdefault("IS_TEST_ENV", "true")
    try:
        set_global_conf({"PROJECT_SOURCE_ROOT": str(tmp_path), "LLM_MODEL_NAME": "test-model", "LLM_MODEL_API_KEY": "test-key"}, ignore_env=True)

        barrier = threading.Barrier(4)
        results: list[RunHistory] = []

        def _worker() -> None:
            barrier.wait()
            results.append(get_default_history())

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert creations["n"] == 1  # exactly one instance despite the race
        assert all(entry is results[0] for entry in results)
    finally:
        run_db.reset_default_history()
        SingletonConfigManager.reset_instance()


def test_reset_default_history_disposes_engine(tmp_path, monkeypatch) -> None:
    SingletonConfigManager.reset_instance()
    monkeypatch.setattr(run_db, "_default_history", None)
    os.environ.setdefault("IS_TEST_ENV", "true")
    try:
        set_global_conf({"PROJECT_SOURCE_ROOT": str(tmp_path), "LLM_MODEL_NAME": "test-model", "LLM_MODEL_API_KEY": "test-key"}, ignore_env=True)
        history = get_default_history()
        dispose_calls: list[int] = []
        monkeypatch.setattr(type(history.engine), "dispose", lambda self, close=True: dispose_calls.append(1))

        run_db.reset_default_history()

        assert dispose_calls == [1]  # dropped singleton's engine was disposed
        assert get_default_history() is not history  # next call rebinds fresh
    finally:
        run_db._default_history = None
        SingletonConfigManager.reset_instance()


# ── hardening: schema migration, idempotent re-record, paths, pass rate, DB location ──


def _create_legacy_v1_runs_table(db_path) -> None:
    """Hand-create a v1 `runs` table whose DDL predates the newer columns."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE runs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "run_id VARCHAR(200), "
            "started_at DATETIME, "
            "finished_at DATETIME, "
            "status VARCHAR(16), "
            "total INTEGER, "
            "passed INTEGER, "
            "failed INTEGER, "
            "errors INTEGER)"
        )
        connection.execute("INSERT INTO runs (run_id, started_at, status, total, passed, failed, errors) " "VALUES ('run_legacy', '2026-01-01 00:00:00', 'passed', 1, 1, 0, 0)")
        connection.commit()
    finally:
        connection.close()


def test_migrates_legacy_runs_table_missing_new_columns(tmp_path) -> None:
    """A v1 `runs` table without newer columns must accept inserts instead of losing history.

    Before the additive migration this passed construction and then failed on every
    insert (sqlite3.OperationalError: table runs has no column named total_tokens),
    which __main__ swallowed into a warning — silently dropping the run forever.
    """
    db_path = tmp_path / "legacy_history.db"
    _create_legacy_v1_runs_table(db_path)

    db = RunHistory(db_url=f"sqlite:///{db_path}")

    junit_path = tmp_path / "final_result.xml"
    _write_junit_xml(tmp_path, junit_path)
    run_id = record_run_summary(_summary(junit_path), run_id="run_new", db=db)
    assert run_id == "run_new"

    # The pre-existing legacy row survived the migration and every column reads back.
    legacy = db.get_run("run_legacy")
    for key in (
        "run_id",
        "started_at",
        "finished_at",
        "status",
        "total",
        "passed",
        "failed",
        "errors",
        "duration_s",
        "total_cost",
        "total_tokens",
        "junit_xml",
        "html",
        "report_md",
        "feature_file",
        "meta",
        "cases",
        "artifacts",
        "healing_events",
    ):
        assert key in legacy
    assert legacy["status"] == "passed" and legacy["total"] == 1
    assert legacy["total_tokens"] is None and legacy["total_cost"] is None

    fresh = db.get_run("run_new")
    assert fresh["status"] == "failed"
    assert fresh["total_tokens"] == 321 and fresh["total_cost"] == pytest.approx(0.17)
    assert sorted(case["status"] for case in fresh["cases"]) == ["failed", "passed"]


def test_record_run_summary_is_idempotent_on_duplicate_run_id(tmp_path) -> None:
    db = _make_db(tmp_path)
    run_dir = tmp_path / "run_20260102_120000"
    run_dir.mkdir()
    junit_path = run_dir / "final_result.xml"
    _write_junit_xml(tmp_path, junit_path)
    summary = _summary(junit_path)

    first = record_run_summary(summary, db=db)
    second = record_run_summary(summary, db=db)

    # Same run_id returned (previously start_run renamed the second call to <id>_1).
    assert first == second == "run_20260102_120000"

    # Exactly one run row — not double-counted in list/trend.
    runs = db.list_runs()
    assert [entry["run_id"] for entry in runs] == ["run_20260102_120000"]
    assert len(db.get_trend()["runs"]) == 1

    got = db.get_run(first)
    # Case rows and run-level artifacts were replaced, not duplicated.
    assert len(got["cases"]) == 2
    assert len(got["artifacts"]) == 2  # bundle + report
    failed_case = next(case for case in got["cases"] if case["status"] == "failed")
    passed_case = next(case for case in got["cases"] if case["status"] == "passed")
    assert len(failed_case["artifacts"]) == 6
    assert passed_case["artifacts"] == []


def test_recorded_paths_are_stored_absolute(tmp_path) -> None:
    """Relative paths from junit properties must not become dead pointers in the DB."""
    db = _make_db(tmp_path)
    db.start_run(run_id="run_paths")
    db.record_artifact("run_paths", None, "bundle", "relative/bundle_dir")
    db.record_case("run_paths", {"name": "Scenario: P", "classname": "Feature: F", "status": "passed", "time": 1.0, "properties": {"Proofs Video": "relative/recording.webm"}})
    db.finish_run(
        "run_paths",
        summary={
            "total": 1,
            "passed": 1,
            "failed": 0,
            "errors": 0,
            "junit_xml": "relative/final_result.xml",
            "html": "relative/final_result.html",
            "report_md": "relative/report.md",
            "feature_file": "relative/login.feature",
        },
    )

    got = db.get_run("run_paths")
    assert got["junit_xml"] == os.path.abspath("relative/final_result.xml")
    assert got["html"] == os.path.abspath("relative/final_result.html")
    assert got["report_md"] == os.path.abspath("relative/report.md")
    assert got["feature_file"] == os.path.abspath("relative/login.feature")
    assert got["artifacts"][0]["path"] == os.path.abspath("relative/bundle_dir")
    assert got["cases"][0]["artifacts"][0]["path"] == os.path.abspath("relative/recording.webm")


def test_pass_rate_is_clamped_when_counters_inconsistent(tmp_path) -> None:
    db = _make_db(tmp_path)
    db.start_run(run_id="run_inconsistent")
    # Inconsistent counters (passed > total) previously produced pass_rate 5.0.
    db.finish_run("run_inconsistent", summary={"total": 1, "passed": 5, "failed": 0, "errors": 0})
    db.start_run(run_id="run_negative")
    db.finish_run("run_negative", summary={"total": 2, "passed": -4, "failed": 0, "errors": 0})

    entries = {entry["run_id"]: entry for entry in db.list_runs()}
    assert entries["run_inconsistent"]["pass_rate"] == pytest.approx(1.0)
    assert entries["run_negative"]["pass_rate"] == pytest.approx(0.0)

    trend = db.get_trend()
    by_run = {entry["run_id"]: entry for entry in trend["runs"]}
    assert by_run["run_inconsistent"]["pass_rate"] == pytest.approx(1.0)
    assert trend["aggregate"]["avg_pass_rate"] == pytest.approx(0.5)


def test_get_default_history_recovers_from_corrupt_db_file(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "hercules_history.db"
    corrupt_bytes = b"\x00\x01this is definitely not a sqlite database" * 64
    db_path.write_bytes(corrupt_bytes)
    monkeypatch.setenv("HERCULES_HISTORY_DB", f"sqlite:///{db_path}")
    run_db.reset_default_history()
    try:
        # Previously: sqlite3.DatabaseError('file is not a database') forever.
        history = get_default_history()
        assert history.db_url == f"sqlite:///{db_path}"
        history.start_run()

        # The corrupt file was quarantined intact, a fresh usable DB took its place.
        quarantined = list(tmp_path.glob("hercules_history.db.corrupt-*"))
        assert len(quarantined) == 1
        assert quarantined[0].read_bytes() == corrupt_bytes
        assert db_path.is_file()
        assert db_path.read_bytes() != corrupt_bytes
    finally:
        run_db.reset_default_history()


def test_get_default_history_creates_missing_parent_dir(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "deep" / "nested" / "hercules_history.db"
    monkeypatch.setenv("HERCULES_HISTORY_DB", f"sqlite:///{db_path}")
    run_db.reset_default_history()
    try:
        # Previously: sqlite3.OperationalError: unable to open database file.
        history = get_default_history()
        assert history.db_url == f"sqlite:///{db_path}"
        history.start_run()
        assert db_path.is_file()
    finally:
        run_db.reset_default_history()


def test_explicit_run_history_stays_strict_on_corrupt_file(tmp_path) -> None:
    """The recovery leniency is only for get_default_history; explicit RunHistory(db) raises."""
    db_path = tmp_path / "explicit.db"
    db_path.write_bytes(b"\x00\x01not a database at all" * 32)
    with pytest.raises(Exception) as excinfo:  # noqa: B017 - sqlite3.DatabaseError, raw or wrapped
        RunHistory(db_url=f"sqlite:///{db_path}")
    assert "file is not a database" in str(excinfo.value).lower()
    # No quarantine side effects for explicit construction.
    assert list(tmp_path.glob("*.corrupt-*")) == []


def test_bundle_dir_for_case_resolves_collision_suffixes(tmp_path) -> None:
    """A case whose name collides with another failing case attaches to its OWN hash-suffixed bundle dir."""
    from testzeus_hercules.reporting.bundle import create_failure_bundles

    junit = tmp_path / "final_result.xml"
    junit.write_text(
        "<testsuite name='s'>"
        "<testcase name='a/b' classname='f'><failure message='message of slash'/></testcase>"
        "<testcase name='a_b' classname='f'><failure message='message of underscore'/></testcase>"
        "</testsuite>",
        encoding="utf-8",
    )
    bundles_root = tmp_path / "bundles"
    created = asyncio.run(create_failure_bundles(str(junit), str(bundles_root)))
    assert len(set(created)) == 2

    db = _make_db(tmp_path)
    summary = _summary(junit)
    summary["total"], summary["passed"], summary["failed"] = 2, 0, 2
    summary["bundles"] = created
    run_id = record_run_summary(summary, run_id="run_collide", db=db)

    slash_case = db.get_case_detail(run_id, "a/b")
    underscore_case = db.get_case_detail(run_id, "a_b")
    assert slash_case["bundle_dir"] and slash_case["bundle_dir"].endswith(os.path.join("bundles", "a_b"))
    # The colliding name must NOT attach to the other case's legacy bundle dir.
    assert underscore_case["bundle_dir"] and not underscore_case["bundle_dir"].endswith(os.path.join("bundles", "a_b"))
    assert "a_b_" in underscore_case["bundle_dir"]


# ── hardening: batched case recording, list_runs total_cost ──────────────────


def _comparable_cases(run: dict) -> list[dict]:
    """Strip identity fields so runs recorded via different paths can be compared."""
    view = []
    for case in run["cases"]:
        stripped = {key: value for key, value in case.items() if key not in ("id", "run_id")}
        stripped["artifacts"] = [(artifact["kind"], artifact["path"]) for artifact in case["artifacts"]]
        view.append(stripped)
    return view


def test_record_cases_matches_record_case_rows(tmp_path) -> None:
    """The batched path must produce the same rows and artifacts as per-case calls."""
    db = _make_db(tmp_path)
    cases = [
        {
            "name": "Scenario: A",
            "classname": "Feature: F",
            "status": "failed",
            "time": 2.0,
            "failure_message": "boom",
            "system_out": "Final Response: nope",
            "properties": {"total_cost": "0.10", "Proofs Video": str(tmp_path / "recording.webm"), "Terminate": "yes"},
        },
        {"name": "Scenario: B", "classname": "Feature: F", "status": "passed", "time": 1.5, "properties": {}},
    ]

    db.start_run(run_id="run_per_case")
    per_case_ids = [db.record_case("run_per_case", case) for case in cases]
    db.start_run(run_id="run_batched")
    batched_ids = db.record_cases("run_batched", cases)

    assert per_case_ids == [1, 2]
    assert batched_ids == [3, 4]

    per_case_view = _comparable_cases(db.get_run("run_per_case"))
    batched_view = _comparable_cases(db.get_run("run_batched"))
    assert batched_view == per_case_view

    failed_case = per_case_view[0]
    assert failed_case["status"] == "failed"
    assert failed_case["cost"] == pytest.approx(0.10)
    assert failed_case["final_response"] == "nope"
    assert ("video", os.path.abspath(str(tmp_path / "recording.webm"))) in failed_case["artifacts"]
    # "Terminate" maps to no artifact kind; the passed case has no evidence at all.
    assert len(failed_case["artifacts"]) == 1
    assert per_case_view[1]["artifacts"] == []


def test_record_cases_empty_list_and_unknown_run(tmp_path) -> None:
    db = _make_db(tmp_path)
    assert db.record_cases("no_such_run", []) == []

    db.start_run(run_id="run_x")
    with pytest.raises(ValueError):
        db.record_cases("no_such_run", [{"name": "Scenario: A", "status": "passed", "properties": {}}])
    assert db.record_cases("run_x", [{"name": "Scenario: A", "status": "passed", "properties": {}}]) == [1]


def test_record_run_summary_batches_case_writes(tmp_path, monkeypatch) -> None:
    """record_run_summary must use the batched path, not one record_case call per testcase."""
    db = _make_db(tmp_path)
    junit_path = tmp_path / "final_result.xml"
    _write_junit_xml(tmp_path, junit_path)

    def _must_not_be_called(*_args, **_kwargs):
        raise AssertionError("record_run_summary must batch cases via record_cases")

    monkeypatch.setattr(db, "record_case", _must_not_be_called)

    run_id = record_run_summary(_summary(junit_path), run_id="run_batched_summary", db=db)

    got = db.get_run(run_id)
    assert sorted(case["status"] for case in got["cases"]) == ["failed", "passed"]
    failed_case = next(case for case in got["cases"] if case["status"] == "failed")
    assert failed_case["artifacts"]  # evidence artifacts still recorded per case


def test_list_runs_includes_total_cost(tmp_path) -> None:
    db = _make_db(tmp_path)
    db.start_run(run_id="run_costed")
    db.finish_run("run_costed", summary={"total": 1, "passed": 1, "failed": 0, "errors": 0, "meta": {"total_cost": 0.42}})
    db.start_run(run_id="run_uncosted")
    db.finish_run("run_uncosted", summary={"total": 1, "passed": 0, "failed": 1, "errors": 0})

    entries = {entry["run_id"]: entry for entry in db.list_runs()}

    # Additive key so callers (dashboard) can read cost without a per-row get_run.
    assert entries["run_costed"]["total_cost"] == pytest.approx(0.42)
    assert entries["run_uncosted"]["total_cost"] is None
    assert set(entries["run_uncosted"]) >= {"run_id", "started_at", "status", "duration_s", "total", "passed", "failed", "errors", "case_count", "pass_rate", "total_cost"}
