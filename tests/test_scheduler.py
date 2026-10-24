"""Offline tests for the monitoring daemon (scheduler.py).

suites.run_suite and scheduler._post_webhook are monkeypatched, so cycle
aggregation, regression math, notification rules and state persistence run
without executing any tests, hitting the network or sleeping for real.
"""

import asyncio
import json
import logging
import os
import sys
import time

import pytest
import testzeus_hercules.orchestrator.run_manager as run_manager_module
import testzeus_hercules.orchestrator.scheduler as scheduler_module
import testzeus_hercules.orchestrator.suites as suites_module
from testzeus_hercules.orchestrator.run_manager import FeatureResult
from testzeus_hercules.orchestrator.scheduler import (
    evaluate_regression,
    load_monitor_config,
    load_state,
    main,
    monitor_loop,
    run_monitor_once,
    save_state,
)


def _write_yaml(directory, content: str, name: str = "monitor.yaml") -> str:
    path = os.path.join(str(directory), name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def _suite_aggregate(name: str, scenarios: list[tuple[str, bool]]) -> dict:
    """A run_suite-shaped summary for canned results."""
    return {
        "suite": name,
        "total": len(scenarios),
        "passed": sum(1 for _, ok in scenarios if ok),
        "failed": sum(1 for _, ok in scenarios if not ok),
        "errors": 0,
        "retries_used": 0,
        "per_scenario": [
            {"scenario": scenario, "feature": "f", "ok": ok, "retries": 0, "duration_s": 0.1, "junit_xml": None, "error": None if ok else "boom", "workspace": "ws"} for scenario, ok in scenarios
        ],
        "merged_xml": None,
        "workroot": "ws",
    }


def _summary(scenarios: list[tuple[str, bool]]) -> dict:
    return _suite_aggregate("s", scenarios)


def _valid_suites_config(workspace) -> str:
    """A real (tiny) suites file so the CLI's up-front configuration check passes."""
    feature = workspace / "ok.feature"
    feature.write_text("Feature: ok\n\n  Scenario: s\n    Given a step\n", encoding="utf-8")
    suites_yaml = workspace / "suites_ok.yaml"
    suites_yaml.write_text(f"smoke:\n  tests: [{feature}]\n", encoding="utf-8")
    return str(suites_yaml)


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Isolate cwd so monitor state files land under tmp."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ── load_monitor_config ───────────────────────────────────────────────────────


def test_load_monitor_config_fills_defaults(workspace) -> None:
    path = _write_yaml(workspace, "suites: [smoke, regression]\n")

    cfg = load_monitor_config(path)

    assert cfg["suites"] == ["smoke", "regression"]
    assert cfg["interval_hours"] == 24
    assert cfg["notify_on"] == "regression"
    assert cfg["baseline"] == "last_run"
    assert cfg["webhook_url"] is None
    # Missing DEFAULT paths stay cwd-relative (run the daemon from the project root).
    assert cfg["config"] == ".hercules/suites.yaml"
    assert cfg["state_file"] == ".hercules/monitor_state.json"


def test_load_monitor_config_rejects_invalid(workspace) -> None:
    with pytest.raises(FileNotFoundError):
        load_monitor_config(str(workspace / "missing.yaml"))

    path = _write_yaml(workspace, "notify_on: always\n")  # missing suites
    with pytest.raises(ValueError, match="suites"):
        load_monitor_config(path)

    path = _write_yaml(workspace, "suites: []\n")
    with pytest.raises(ValueError, match="suites"):
        load_monitor_config(path)

    path = _write_yaml(workspace, "suites: [a]\nnotify_on: sometimes\n")
    with pytest.raises(ValueError, match="notify_on"):
        load_monitor_config(path)

    path = _write_yaml(workspace, "suites: [a]\ninterval_hours: 0\n")
    with pytest.raises(ValueError, match="interval_hours"):
        load_monitor_config(path)

    path = _write_yaml(workspace, "suites: [a]\ninterval_hours: later\n")
    with pytest.raises(ValueError, match="interval_hours"):
        load_monitor_config(path)


def test_relative_state_file_is_anchored_to_the_config_directory(workspace, monkeypatch) -> None:
    conf_dir = workspace / "conf"
    conf_dir.mkdir()
    elsewhere = workspace / "elsewhere"
    elsewhere.mkdir()
    path = _write_yaml(conf_dir, "suites: [a]\nstate_file: state/monitor_state.json\n")
    monkeypatch.chdir(elsewhere)  # daemon started from another directory

    cfg = load_monitor_config(path)

    assert cfg["state_file"] == os.path.join(str(conf_dir), "state", "monitor_state.json")
    save_state(cfg["state_file"], {"runs": {}})
    assert (conf_dir / "state" / "monitor_state.json").is_file()
    assert not (elsewhere / "state").exists()


def test_absolute_state_file_is_unchanged(workspace) -> None:
    absolute = str(workspace / "abs" / "state.json")
    path = _write_yaml(workspace, f"suites: [a]\nstate_file: {absolute}\n")
    assert load_monitor_config(path)["state_file"] == absolute


def test_relative_state_file_existing_in_cwd_is_still_used(workspace, monkeypatch) -> None:
    """State written by older versions (relative to the cwd) must keep being found."""
    conf_dir = workspace / "conf"
    conf_dir.mkdir()
    legacy = workspace / "monitor_state.json"
    legacy.write_text("{}", encoding="utf-8")
    path = _write_yaml(conf_dir, "suites: [a]\nstate_file: monitor_state.json\n")
    assert load_monitor_config(path)["state_file"] == str(legacy)


# ── evaluate_regression ───────────────────────────────────────────────────────


def test_evaluate_regression_detects_new_failures_and_fixes() -> None:
    baseline = _summary([("A", True), ("B", False), ("C", True)])
    current = _summary([("A", False), ("B", True), ("C", True), ("D", False)])  # D is brand new

    result = evaluate_regression(current, baseline)

    assert result["regressed"] is True
    assert result["new_failures"] == ["A"]  # D is new, not a regression
    assert result["fixed"] == ["B"]
    assert result["pass_rate_current"] == pytest.approx(2 / 4)
    assert result["pass_rate_baseline"] == pytest.approx(2 / 3)


def test_evaluate_regression_suite_qualified_keys() -> None:
    baseline = {"suites": {"s1": _suite_aggregate("s1", [("A", True)])}, "total": 1, "passed": 1}
    current = {"suites": {"s1": _suite_aggregate("s1", [("A", False)])}, "total": 1, "passed": 0}

    result = evaluate_regression(current, baseline)

    assert result["new_failures"] == ["s1::A"]
    assert result["regressed"] is True


def test_evaluate_regression_no_baseline_first_run() -> None:
    result = evaluate_regression(_summary([("A", False)]), None)

    assert result == {"regressed": False, "new_failures": [], "fixed": [], "pass_rate_current": 0.0, "pass_rate_baseline": None}


def test_evaluate_regression_no_changes() -> None:
    baseline = _summary([("A", True), ("B", False)])
    result = evaluate_regression(_summary([("A", True), ("B", False)]), baseline)

    assert result["regressed"] is False
    assert result["new_failures"] == [] and result["fixed"] == []


# ── run_monitor_once ──────────────────────────────────────────────────────────


def _make_cfg(workspace, notify_on="regression", webhook=True, baseline="last_run") -> dict:
    webhook_line = f"webhook_url: https://hooks.example/test\n" if webhook else ""
    path = _write_yaml(workspace, f"suites: [smoke]\nnotify_on: {notify_on}\n{webhook_line}baseline: {baseline}\nstate_file: {workspace}/state/monitor_state.json\n")
    return load_monitor_config(path)


def test_run_monitor_once_aggregates_and_persists_state(workspace, monkeypatch) -> None:
    cfg = _make_cfg(workspace, notify_on="regression", webhook=True)
    posts: list[tuple[str, dict]] = []

    async def fake_run_suite(name, path=".hercules/suites.yaml", **kwargs):
        return _suite_aggregate(name, [("A", True), ("B", False)])

    async def fake_post(url, payload):
        posts.append((url, payload))

    monkeypatch.setattr(suites_module, "run_suite", fake_run_suite)
    monkeypatch.setattr(scheduler_module, "_post_webhook", fake_post)

    summary = asyncio.run(run_monitor_once(cfg, {}))

    assert summary["suites"]["smoke"]["total"] == 2
    assert summary["total"] == 2 and summary["passed"] == 1 and summary["failed"] == 1
    # First cycle has no baseline: not a regression, so notify_on=regression stays quiet.
    assert summary["regression"]["regressed"] is False
    assert summary["notified"] is False and posts == []

    state = load_state(cfg["state_file"])
    assert state["last_results"]["run_id"] == summary["run_id"]
    assert summary["run_id"] in state["runs"]


def test_run_monitor_once_regression_notifies_on_second_cycle(workspace, monkeypatch) -> None:
    cfg = _make_cfg(workspace, notify_on="regression")
    posts: list[tuple[str, dict]] = []
    scenario_state = [("A", True), ("B", True)]

    async def fake_run_suite(name, path=".hercules/suites.yaml", **kwargs):
        return _suite_aggregate(name, scenario_state)

    async def fake_post(url, payload):
        posts.append((url, payload))

    monkeypatch.setattr(suites_module, "run_suite", fake_run_suite)
    monkeypatch.setattr(scheduler_module, "_post_webhook", fake_post)

    state: dict = {}
    first = asyncio.run(run_monitor_once(cfg, state))
    assert first["notified"] is False

    scenario_state = [("A", False), ("B", True)]  # A regressed
    second = asyncio.run(run_monitor_once(cfg, state))

    assert second["regression"]["regressed"] is True
    assert second["regression"]["new_failures"] == ["smoke::A"]  # suite-qualified in combined summaries
    assert second["regression"]["pass_rate_current"] == pytest.approx(0.5)
    assert second["regression"]["pass_rate_baseline"] == pytest.approx(1.0)
    assert second["notified"] is True
    assert len(posts) == 1 and posts[0][0] == "https://hooks.example/test"
    assert posts[0][1]["text"].startswith("Hercules monitor run")
    assert "A" in posts[0][1]["text"]


def test_run_monitor_once_notify_on_failure_and_always(workspace, monkeypatch) -> None:
    posts: list[tuple[str, dict]] = []

    async def fake_run_suite(name, path=".hercules/suites.yaml", **kwargs):
        return _suite_aggregate(name, [("A", True)])

    async def fake_post(url, payload):
        posts.append((url, payload))

    monkeypatch.setattr(suites_module, "run_suite", fake_run_suite)
    monkeypatch.setattr(scheduler_module, "_post_webhook", fake_post)

    # notify_on=failure stays quiet when everything passes.
    cfg = _make_cfg(workspace, notify_on="failure")
    summary = asyncio.run(run_monitor_once(cfg, {}))
    assert summary["notified"] is False

    # ...and fires on a failure.
    async def fake_run_suite_failing(name, path=".hercules/suites.yaml", **kwargs):
        return _suite_aggregate(name, [("A", False)])

    monkeypatch.setattr(suites_module, "run_suite", fake_run_suite_failing)
    summary = asyncio.run(run_monitor_once(cfg, {}))
    assert summary["notified"] is True

    # notify_on=always fires even on a green run.
    posts.clear()
    monkeypatch.setattr(suites_module, "run_suite", fake_run_suite)
    cfg = _make_cfg(workspace, notify_on="always")
    summary = asyncio.run(run_monitor_once(cfg, {}))
    assert summary["notified"] is True


def test_run_monitor_once_broken_webhook_never_raises(workspace, monkeypatch) -> None:
    cfg = _make_cfg(workspace, notify_on="always")

    async def fake_run_suite(name, path=".hercules/suites.yaml", **kwargs):
        return _suite_aggregate(name, [("A", True)])

    async def broken_post(url, payload):
        raise RuntimeError("webhook down")

    monkeypatch.setattr(suites_module, "run_suite", fake_run_suite)
    monkeypatch.setattr(scheduler_module, "_post_webhook", broken_post)

    summary = asyncio.run(run_monitor_once(cfg, {}))
    assert summary["notified"] is False  # cycle still completes
    assert load_state(cfg["state_file"])["last_results"]["run_id"] == summary["run_id"]


def test_run_monitor_once_pinned_baseline_run_id(workspace, monkeypatch) -> None:
    # State always stores combined (suite-keyed) summaries, as produced by run_monitor_once.
    golden = {"suites": {"smoke": _suite_aggregate("smoke", [("A", True), ("B", True)])}, "total": 2, "passed": 2}
    state = {"runs": {"GOLDEN-RUN": golden}, "last_results": {"suites": {"smoke": _suite_aggregate("smoke", [("A", True)])}, "total": 1, "passed": 1}}
    cfg = _make_cfg(workspace, baseline="GOLDEN-RUN")

    async def fake_run_suite(name, path=".hercules/suites.yaml", **kwargs):
        return _suite_aggregate(name, [("A", False)])  # A fails vs golden, but passes vs last

    monkeypatch.setattr(suites_module, "run_suite", fake_run_suite)

    summary = asyncio.run(run_monitor_once(cfg, state))

    assert summary["regression"]["new_failures"] == ["smoke::A"]  # compared to GOLDEN-RUN, not last_results
    assert summary["regression"]["pass_rate_baseline"] == 1.0
    assert "baseline_missing" not in summary["regression"]  # pinned baseline found: no flag


def test_run_monitor_once_warns_when_pinned_baseline_pruned(workspace, monkeypatch, caplog) -> None:
    # A pinned run_id pushed out of the 10-run window must not silently turn
    # regression detection off: the cycle warns and flags baseline_missing.
    older = {"suites": {"smoke": _suite_aggregate("smoke", [("A", True)])}, "total": 1, "passed": 1}
    state = {"runs": {"OTHER-RUN": older}, "last_results": older}
    cfg = _make_cfg(workspace, webhook=False, baseline="PRUNED-RUN")

    async def fake_run_suite(name, path=".hercules/suites.yaml", **kwargs):
        return _suite_aggregate(name, [("A", False)])  # would be a regression vs the (missing) baseline

    monkeypatch.setattr(suites_module, "run_suite", fake_run_suite)

    with caplog.at_level(logging.WARNING):
        summary = asyncio.run(run_monitor_once(cfg, state))

    assert "PRUNED-RUN" in caplog.text and "regression detection disabled" in caplog.text
    assert summary["regression"]["baseline_missing"] is True
    assert summary["regression"]["regressed"] is False  # detection disabled, not "no regression"


def test_run_monitor_once_trims_run_history(workspace, monkeypatch) -> None:
    cfg = _make_cfg(workspace, webhook=False)
    counter = {"n": 0}

    async def fake_run_suite(name, path=".hercules/suites.yaml", **kwargs):
        return _suite_aggregate(name, [("A", True)])

    async def fake_post(url, payload):
        return None

    monkeypatch.setattr(suites_module, "run_suite", fake_run_suite)
    monkeypatch.setattr(scheduler_module, "_post_webhook", fake_post)

    state: dict = {}
    for _ in range(scheduler_module.MAX_KEPT_RUNS + 3):
        counter["n"] += 1
        asyncio.run(run_monitor_once(cfg, state))

    assert len(state["runs"]) == scheduler_module.MAX_KEPT_RUNS
    assert state["last_run_id"] == max(state["runs"])


# ── state round trip ──────────────────────────────────────────────────────────


def test_state_round_trip_and_corrupt_recovery(workspace) -> None:
    state_path = str(workspace / "deep" / "state.json")

    assert load_state(state_path) == {}

    save_state(state_path, {"last_run_id": "r1", "runs": {"r1": {"total": 3}}})
    assert load_state(state_path) == {"last_run_id": "r1", "runs": {"r1": {"total": 3}}}

    with open(state_path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert load_state(state_path) == {}


def test_save_state_atomic_failure_keeps_previous_state(workspace, monkeypatch) -> None:
    # A crash mid-write must never truncate the previous state (the regression
    # baseline lives in it): the tmp+os.replace dance keeps the old file intact.
    state_path = str(workspace / "state.json")
    save_state(state_path, {"last_run_id": "r1"})

    def _boom(*args, **kwargs):
        raise RuntimeError("mid-write crash")

    monkeypatch.setattr(scheduler_module.json, "dump", _boom)
    with pytest.raises(RuntimeError, match="mid-write"):
        save_state(state_path, {"last_run_id": "r2"})

    assert load_state(state_path) == {"last_run_id": "r1"}  # old state survives
    assert not os.path.exists(state_path + ".tmp")  # partial tmp file cleaned up


# ── retention sweep ───────────────────────────────────────────────────────────


def _make_suite_run_tree(base, name, age_s: float = 0.0) -> str:
    path = os.path.join(str(base), name)
    os.makedirs(os.path.join(path, "scenario_1", "opt", "output"), exist_ok=True)
    with open(os.path.join(path, "scenario_1", "opt", "output", "video.webm"), "w", encoding="utf-8") as handle:
        handle.write("fake video")
    if age_s:
        old = time.time() - age_s
        os.utime(path, (old, old))
    return path


def test_sweep_old_suite_runs_removes_only_stale_trees(workspace) -> None:
    base = workspace / ".hercules" / "suite_runs"
    stale = _make_suite_run_tree(base, "smoke_20200101_000000", age_s=100 * 3600)
    fresh = _make_suite_run_tree(base, "smoke_20990101_000000")

    removed = scheduler_module.sweep_old_suite_runs(base_dir=str(base), retention_hours_override=72)

    assert removed == [stale]
    assert not os.path.exists(stale)
    assert os.path.isdir(fresh)  # recent tree untouched


def test_sweep_retention_env_var_and_default(workspace, monkeypatch) -> None:
    base = workspace / ".hercules" / "suite_runs"
    two_hours_old = _make_suite_run_tree(base, "regression_a", age_s=2 * 3600)

    monkeypatch.setenv("HERCULES_RUN_RETENTION_HOURS", "1")
    assert scheduler_module.retention_hours() == 1
    assert scheduler_module.sweep_old_suite_runs(base_dir=str(base)) == [two_hours_old]

    two_hours_old = _make_suite_run_tree(base, "regression_b", age_s=2 * 3600)
    monkeypatch.setenv("HERCULES_RUN_RETENTION_HOURS", "not-a-number")
    assert scheduler_module.retention_hours() == scheduler_module.DEFAULT_RETENTION_HOURS  # 72h default
    assert scheduler_module.sweep_old_suite_runs(base_dir=str(base)) == []  # 2h-old tree survives the default

    monkeypatch.delenv("HERCULES_RUN_RETENTION_HOURS")
    assert scheduler_module.retention_hours() == scheduler_module.DEFAULT_RETENTION_HOURS


def test_sweep_old_parallel_workroots_only_touches_stale_parallel_trees(workspace) -> None:
    base = workspace / ".hercules"
    stale = _make_suite_run_tree(base, "parallel_20200101_000000_abc", age_s=100 * 3600)
    fresh = _make_suite_run_tree(base, "parallel_20990101_000000_def")
    other = _make_suite_run_tree(base, "my_session_dir", age_s=100 * 3600)  # MCP session dirs etc. are not ours

    removed = scheduler_module.sweep_old_parallel_workroots(retention_hours_override=72)

    assert removed == [stale]
    assert os.path.isdir(fresh) and os.path.isdir(other)


def test_run_manager_sweeps_old_parallel_workroots(workspace, monkeypatch) -> None:

    stale = _make_suite_run_tree(workspace / ".hercules", "parallel_old", age_s=100 * 3600)
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: [sys.executable, "-c", "pass"])
    feature = workspace / "f.feature"
    feature.write_text("Feature: f\n\n  Scenario: s\n    Given a step\n", encoding="utf-8")
    asyncio.run(run_manager_module.run_feature_file_parallel(str(feature)))
    assert not os.path.exists(stale)


def test_sweep_missing_base_dir_is_noop(workspace) -> None:
    assert scheduler_module.sweep_old_suite_runs(base_dir=str(workspace / "nope")) == []


def test_sweep_retention_zero_grace_keeps_fresh_trees(workspace, monkeypatch) -> None:
    # HERCULES_RUN_RETENTION_HOURS=0 must not delete a concurrent manual
    # hercules-suite run's seconds-old workspace: sweeps never touch trees
    # younger than the 1-hour grace window.
    base = workspace / ".hercules" / "suite_runs"
    fresh = _make_suite_run_tree(base, "smoke_fresh")
    old = _make_suite_run_tree(base, "smoke_old", age_s=2 * 3600)

    monkeypatch.setenv("HERCULES_RUN_RETENTION_HOURS", "0")
    assert scheduler_module.retention_hours() == 0  # 0 parses; the grace lives in the sweep
    removed = scheduler_module.sweep_old_suite_runs(base_dir=str(base))

    assert removed == [old]
    assert os.path.isdir(fresh)

    removed = scheduler_module.sweep_old_suite_runs(base_dir=str(base), retention_hours_override=0)
    assert removed == [] and os.path.isdir(fresh)  # override path gets the same grace


# ── cycle-level dependency gating (real run_suite, fake subprocesses) ─────────


def _write_feature(directory, name: str) -> str:
    path = os.path.join(str(directory), name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"Feature: {name}\n\n  Scenario: s0\n    Given a step\n\n")
    return path


def _make_cycle_cfg(workspace, suites_yaml: str) -> dict:
    path = _write_yaml(workspace, f"suites: [smoke, regression]\nconfig: {suites_yaml}\nstate_file: {workspace}/monitor_state.json\nwebhook_url: null\n")
    return load_monitor_config(path)


def _fake_isolated(calls: list[str], ok_names: tuple[str, ...] = ("smoke_a.feature", "reg_b.feature")):
    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        name = os.path.basename(feature_path)
        calls.append(name)
        return FeatureResult(
            scenario=name.replace(".feature", ""),
            feature_path=feature_path,
            workspace=os.path.join(workroot, "fake_ws"),
            ok=name in ok_names,
            exit_code=0 if name in ok_names else 1,
            duration_s=0.1,
            junit_xml=None,
            stdout_tail="",
            error=None if name in ok_names else "Subprocess exited with code 1.",
        )

    return fake_isolated


def test_monitor_cycle_executes_dependency_features_once(workspace, monkeypatch) -> None:
    _write_feature(workspace, "smoke_a.feature")
    _write_feature(workspace, "reg_b.feature")
    suites_yaml = _write_yaml(workspace, "smoke:\n  tests: [smoke_a.feature]\nregression:\n  tests: [reg_b.feature]\n  depends_on: [smoke]\n", name="suites.yaml")
    cfg = _make_cycle_cfg(workspace, suites_yaml)
    calls: list[str] = []

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", _fake_isolated(calls))

    summary = asyncio.run(run_monitor_once(cfg, {}))

    assert sorted(calls) == ["reg_b.feature", "smoke_a.feature"]  # smoke's feature ran exactly once
    assert summary["suites"]["smoke"]["total"] == 1
    assert summary["suites"]["regression"]["total"] == 1  # regression contributes only its own feature
    assert summary["total"] == 2 and summary["passed"] == 2  # no duplicated scenarios in the totals


def test_monitor_cycle_skips_dependent_suite_when_dependency_fails(workspace, monkeypatch) -> None:
    _write_feature(workspace, "smoke_a.feature")
    _write_feature(workspace, "reg_b.feature")
    suites_yaml = _write_yaml(workspace, "smoke:\n  tests: [smoke_a.feature]\nregression:\n  tests: [reg_b.feature]\n  depends_on: [smoke]\n", name="suites.yaml")
    cfg = _make_cycle_cfg(workspace, suites_yaml)
    calls: list[str] = []

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", _fake_isolated(calls, ok_names=("reg_b.feature",)))

    summary = asyncio.run(run_monitor_once(cfg, {}))

    assert calls == ["smoke_a.feature"]  # regression's own feature never executed
    regression = summary["suites"]["regression"]
    assert regression["total"] == 1 and regression["errors"] == 1
    assert regression["per_scenario"][0]["error"] == "skipped: dependency 'smoke' failed"
    assert summary["failed"] == 1 and summary["errors"] == 1


def test_run_monitor_once_sweeps_stale_suite_runs(workspace, monkeypatch) -> None:
    stale = _make_suite_run_tree(workspace / ".hercules" / "suite_runs", "regression_20200101_000000", age_s=100 * 3600)
    cfg = _make_cfg(workspace, webhook=False)

    async def fake_run_suite(name, path=".hercules/suites.yaml", **kwargs):
        return _suite_aggregate(name, [("A", True)])

    monkeypatch.setattr(suites_module, "run_suite", fake_run_suite)

    asyncio.run(run_monitor_once(cfg, {}))

    assert not os.path.exists(stale)  # every cycle starts by sweeping old trees


# ── monitor_loop ──────────────────────────────────────────────────────────────


def test_monitor_loop_runs_repeated_cycles_and_is_cancellable(workspace, monkeypatch) -> None:
    cfg = _make_cfg(workspace, webhook=False)
    cfg["interval_hours"] = 0.000001  # ~3.6ms between cycles
    cycles = {"n": 0}

    async def fake_once(cfg_arg, state):
        cycles["n"] += 1
        return {"run_id": f"r{cycles['n']}", "total": 0, "passed": 0, "failed": 0, "errors": 0}

    async def fake_post(url, payload):
        return None

    monkeypatch.setattr(scheduler_module, "run_monitor_once", fake_once)
    monkeypatch.setattr(scheduler_module, "_post_webhook", fake_post)

    async def _drive() -> None:
        task = asyncio.create_task(monitor_loop(cfg))
        try:
            while cycles["n"] < 3:
                await asyncio.sleep(0.01)
                assert not task.done()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(_drive())
    assert cycles["n"] >= 3


def test_monitor_loop_survives_failing_cycles(workspace, monkeypatch) -> None:
    cfg = _make_cfg(workspace, webhook=False)
    cfg["interval_hours"] = 0.000001
    attempts = {"n": 0}

    async def flaky_once(cfg_arg, state):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("suite exploded")
        return {"run_id": "ok", "total": 0, "passed": 0, "failed": 0, "errors": 0}

    monkeypatch.setattr(scheduler_module, "run_monitor_once", flaky_once)

    async def _drive() -> None:
        task = asyncio.create_task(monitor_loop(cfg))
        while attempts["n"] < 2:
            await asyncio.sleep(0.01)
            assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_drive())
    assert attempts["n"] >= 2


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_scheduler_cli_once_exit_codes(workspace, monkeypatch, capsys) -> None:
    cfg_path = _write_yaml(workspace, f"suites: [smoke]\nconfig: {_valid_suites_config(workspace)}\nstate_file: state.json\n")

    async def failing_once(cfg, state):
        return {
            "run_id": "r",
            "total": 2,
            "passed": 1,
            "failed": 1,
            "errors": 0,
            "regression": {"regressed": True, "new_failures": ["A"], "fixed": [], "pass_rate_current": 0.5, "pass_rate_baseline": 1.0},
            "notified": False,
        }

    async def green_once(cfg, state):
        return {
            "run_id": "r",
            "total": 1,
            "passed": 1,
            "failed": 0,
            "errors": 0,
            "regression": {"regressed": False, "new_failures": [], "fixed": [], "pass_rate_current": 1.0, "pass_rate_baseline": None},
            "notified": False,
        }

    monkeypatch.setattr(scheduler_module, "run_monitor_once", failing_once)
    assert main(["--once", "--config", cfg_path]) == 1
    out = capsys.readouterr().out
    assert "Hercules monitor run" in out and "Regressed: True" in out

    monkeypatch.setenv("EXIT_CODE_COMPAT", "1")
    assert main(["--once", "--config", cfg_path]) == 0
    monkeypatch.delenv("EXIT_CODE_COMPAT")

    monkeypatch.setattr(scheduler_module, "run_monitor_once", green_once)
    assert main(["--once", "--config", cfg_path]) == 0


@pytest.mark.parametrize(
    "content",
    [None, "suites: [unclosed\n", "- just\n- a list\n", "notify_on: always\n", "suites: [a]\ninterval_hours: later\n"],
    ids=["missing", "invalid-yaml", "not-a-mapping", "no-suites", "bad-interval"],
)
def test_scheduler_cli_bad_config_is_a_clean_error(workspace, capsys, content) -> None:
    path = str(workspace / "missing.yaml") if content is None else _write_yaml(workspace, content)
    for extra in ([], ["--once"]):
        assert main(["--config", path, *extra]) == 2
        err = capsys.readouterr().err
        assert err.startswith("hercules-monitor: error:")
        assert "Traceback" not in err
        assert len(err.strip().splitlines()) == 1


def test_scheduler_cli_unreadable_config_is_a_clean_error(workspace, capsys) -> None:
    path = _write_yaml(workspace, "suites: [a]\n")
    os.chmod(path, 0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("running with privileges that ignore file modes")
        assert main(["--config", path, "--once"]) == 2
    finally:
        os.chmod(path, 0o600)
    err = capsys.readouterr().err
    assert err.startswith("hercules-monitor: error:") and "Traceback" not in err


def test_scheduler_cli_once_missing_suites_config_is_a_clean_error(workspace, capsys) -> None:
    path = _write_yaml(workspace, "suites: [smoke]\nconfig: nope/suites.yaml\n")
    assert main(["--once", "--config", path]) == 2
    err = capsys.readouterr().err
    assert err.startswith("hercules-monitor: error:") and "Traceback" not in err


def test_scheduler_cli_once_overrides_interval(workspace, monkeypatch) -> None:
    cfg_path = _write_yaml(workspace, f"suites: [smoke]\nconfig: {_valid_suites_config(workspace)}\ninterval_hours: 99\n")
    captured: dict = {}

    async def fake_once(cfg, state):
        captured["interval_hours"] = cfg["interval_hours"]
        return {"run_id": "r", "total": 0, "passed": 0, "failed": 0, "errors": 0, "regression": {"regressed": False, "new_failures": [], "fixed": []}, "notified": False}

    monkeypatch.setattr(scheduler_module, "run_monitor_once", fake_once)
    assert main(["--once", "--config", cfg_path, "--interval-hours", "2.5"]) == 0
    assert captured["interval_hours"] == 2.5


# ── TES-1947 review follow-ups ────────────────────────────────────────────────

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def test_shipped_monitor_example_resolves_state_next_to_the_copied_config(workspace) -> None:
    """Copying .hercules/monitor.example.yaml to .hercules/monitor.yaml must not nest .hercules/.hercules/."""
    import shutil

    conf_dir = workspace / ".hercules"
    conf_dir.mkdir()
    target = conf_dir / "monitor.yaml"
    shutil.copy(REPO_ROOT / ".hercules" / "monitor.example.yaml", target)

    cfg = load_monitor_config(str(target))

    assert os.path.realpath(cfg["state_file"]) == os.path.realpath(conf_dir / "monitor_state.json")


def test_state_file_naming_a_path_inside_the_config_dir_is_not_nested_again(workspace) -> None:
    """The previous example shipped `.hercules/monitor_state.json` inside .hercules/monitor.yaml."""
    conf_dir = workspace / ".hercules"
    conf_dir.mkdir()
    path = _write_yaml(conf_dir, "suites: [a]\nstate_file: .hercules/monitor_state.json\n")

    cfg = load_monitor_config(path)

    assert os.path.realpath(cfg["state_file"]) == os.path.realpath(conf_dir / "monitor_state.json")


def test_scheduler_cli_once_unreadable_suites_config_is_a_clean_error(workspace, capsys) -> None:
    suites_yaml = workspace / "suites.yaml"
    suites_yaml.write_text("smoke:\n  tests: [a.feature]\n", encoding="utf-8")
    path = _write_yaml(workspace, f"suites: [smoke]\nconfig: {suites_yaml}\n")
    os.chmod(suites_yaml, 0)
    try:
        if os.access(suites_yaml, os.R_OK):
            pytest.skip("running with privileges that ignore file modes")
        assert main(["--once", "--config", path]) == 2
    finally:
        os.chmod(suites_yaml, 0o600)
    err = capsys.readouterr().err
    assert err.startswith("hercules-monitor: error:") and "Traceback" not in err


def test_scheduler_cli_once_dependency_cycle_is_a_clean_error(workspace, capsys) -> None:
    feature = workspace / "a.feature"
    feature.write_text("Feature: a\n\n  Scenario: s\n    Given a step\n", encoding="utf-8")
    suites_yaml = workspace / "suites.yaml"
    suites_yaml.write_text(f"a:\n  tests: [{feature}]\n  depends_on: [b]\nb:\n  tests: [{feature}]\n  depends_on: [a]\n", encoding="utf-8")
    path = _write_yaml(workspace, f"suites: [a]\nconfig: {suites_yaml}\n")

    assert main(["--once", "--config", path]) == 2
    err = capsys.readouterr().err
    assert "cycle" in err and "Traceback" not in err


def test_scheduler_cli_once_runtime_failure_is_not_reported_as_a_config_error(workspace, monkeypatch, capsys) -> None:
    """A bug or I/O failure inside the cycle must surface with its traceback, not as exit 2 'config error'."""
    feature = workspace / "a.feature"
    feature.write_text("Feature: a\n\n  Scenario: s\n    Given a step\n", encoding="utf-8")
    suites_yaml = workspace / "suites.yaml"
    suites_yaml.write_text(f"smoke:\n  tests: [{feature}]\n", encoding="utf-8")
    path = _write_yaml(workspace, f"suites: [smoke]\nconfig: {suites_yaml}\n")

    async def boom(cfg, state):
        raise ValueError("bug in the cycle")

    monkeypatch.setattr(scheduler_module, "run_monitor_once", boom)

    with pytest.raises(ValueError, match="bug in the cycle"):
        main(["--once", "--config", path])
    assert "hercules-monitor: error:" not in capsys.readouterr().err
