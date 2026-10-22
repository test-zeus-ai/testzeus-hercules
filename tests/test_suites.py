"""Offline tests for named suites (suites.py).

run_manager.run_feature_isolated is monkeypatched with fakes returning real
FeatureResult dataclass instances, so resolution, splitting, retries, shared
state wiring and aggregation are tested without launching Hercules (no LLM,
no browsers). resolve_suite/load_suites run against tmp YAML fixtures.
"""

import asyncio
import json
import logging
import os
import tempfile
import threading

import pytest
import testzeus_hercules.orchestrator.run_manager as run_manager_module
import testzeus_hercules.orchestrator.suites as suites_module
from testzeus_hercules.intelligence.test_plan import (
    PlanStep,
    TestCase,
    TestPlan,
    plan_to_json,
)
from testzeus_hercules.orchestrator.run_manager import FeatureResult
from testzeus_hercules.orchestrator.suites import (
    SHARED_STATE_ENV_VAR,
    SUITE_STATE_FILENAME,
    append_suite_state,
    load_suites,
    main,
    resolve_suite,
    run_suite,
)

PASSED_JUNIT = (
    '<testsuites><testsuite name="Feature: demo" tests="1" failures="0" errors="0" time="0.1">' '<testcase name="Scenario: demo" classname="Feature: demo" time="0.1"/>' "</testsuite></testsuites>"
)
FAILED_JUNIT = (
    '<testsuites><testsuite name="Feature: demo" tests="1" failures="1" errors="0" time="0.2">'
    '<testcase name="Scenario: demo" classname="Feature: demo" time="0.2"><failure message="boom">boom</failure></testcase>'
    "</testsuite></testsuites>"
)


def _make_feature(directory, name: str, scenarios: int = 1) -> str:
    body = "".join(f"  Scenario: s{i}\n    Given a step\n\n" for i in range(scenarios))
    path = os.path.join(str(directory), name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"Feature: {name}\n\n{body}")
    return path


def _write_suites_yaml(directory, content: str) -> str:
    path = os.path.join(str(directory), "suites.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def _fake_result(scenario: str, feature_path: str, workroot: str, ok: bool, junit_xml: str | None = None, call_no: int = 1) -> FeatureResult:
    return FeatureResult(
        scenario=os.path.splitext(os.path.basename(feature_path))[0] if not scenario else scenario,
        feature_path=feature_path,
        workspace=os.path.join(workroot, "fake_ws"),
        ok=ok,
        exit_code=0 if ok else 1,
        duration_s=0.25,
        junit_xml=junit_xml,
        stdout_tail="",
        error=None if ok else f"Subprocess exited with code 1 (call {call_no}).",
    )


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Isolate cwd so suite workroots (.hercules/suite_runs/...) land under tmp."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ── load_suites ───────────────────────────────────────────────────────────────


def test_load_suites_normalizes_and_fills_defaults(workspace) -> None:
    features = workspace / "features"
    features.mkdir()
    _make_feature(features, "a.feature")

    path = _write_suites_yaml(workspace, "smoke:\n  description: Quick\n  tests: [features]\n  retry: 2\n  env: {N: 5}\n  extra_unknown: x\n")

    suites = load_suites(path)
    assert set(suites) == {"smoke"}
    cfg = suites["smoke"]
    assert cfg["description"] == "Quick"
    assert cfg["tests"] == [str(features)]  # anchored to the yaml dir, absolute
    assert cfg["retry"] == 2 and cfg["max_parallel"] == 1
    assert cfg["depends_on"] == [] and cfg["shared_state"] is False
    assert cfg["env"] == {"N": "5"}  # values coerced to strings
    assert "extra_unknown" not in cfg  # unknown keys dropped (with a warning)


def test_load_suites_rejects_invalid_shapes(workspace) -> None:
    with pytest.raises(FileNotFoundError):
        load_suites(str(workspace / "missing.yaml"))

    path = _write_suites_yaml(workspace, "- just\n- a\n- list\n")
    with pytest.raises(ValueError, match="mapping"):
        load_suites(path)

    path = _write_suites_yaml(workspace, "empty_suite: {}\n")
    with pytest.raises(ValueError, match="tests.*plan"):
        load_suites(path)

    path = _write_suites_yaml(workspace, "bad_retry:\n  tests: [x.feature]\n  retry: -1\n")
    with pytest.raises(ValueError, match="retry"):
        load_suites(path)

    path = _write_suites_yaml(workspace, "bad_parallel:\n  tests: [x.feature]\n  max_parallel: 0\n")
    with pytest.raises(ValueError, match="max_parallel"):
        load_suites(path)

    path = _write_suites_yaml(workspace, "bad_env:\n  tests: [x.feature]\n  env: [not, a, mapping]\n")
    with pytest.raises(ValueError, match="env"):
        load_suites(path)

    path = _write_suites_yaml(workspace, "bad_tests:\n  tests: [1, 2]\n")
    with pytest.raises(ValueError, match="tests"):
        load_suites(path)


def test_load_suites_warns_when_tests_and_plan_both_defined(workspace, caplog) -> None:
    _make_feature(workspace, "a.feature")
    path = _write_suites_yaml(workspace, "both:\n  tests: [a.feature]\n  plan: test_plan.json\n")

    with caplog.at_level(logging.WARNING):
        suites = load_suites(path)

    assert "Suite 'both' defines both tests and plan; plan is ignored" in caplog.text
    assert suites["both"]["tests"] and "plan" in suites["both"]  # tests win, plan kept but inert


def test_load_suites_rejects_non_boolean_skip_on_dependency_failure(workspace) -> None:
    path = _write_suites_yaml(workspace, "s:\n  tests: [a.feature]\n  skip_on_dependency_failure: sometimes\n")
    with pytest.raises(ValueError, match="skip_on_dependency_failure"):
        load_suites(path)


# ── resolve_suite ─────────────────────────────────────────────────────────────


def test_resolve_suite_dependency_first_order(workspace) -> None:
    fa = _make_feature(workspace, "a.feature")
    fb = _make_feature(workspace, "b.feature")
    suites = {"a": {"tests": [fa], "depends_on": ["b"]}, "b": {"tests": [fb]}}

    assert resolve_suite("a", suites) == [fb, fa]  # dependency features come first


def test_resolve_suite_cycle_raises(workspace) -> None:
    suites = {"a": {"tests": ["a.feature"], "depends_on": ["b"]}, "b": {"tests": ["b.feature"], "depends_on": ["a"]}}
    with pytest.raises(ValueError, match="[Cc]ycle"):
        resolve_suite("a", suites)


def test_resolve_suite_unknown_raises_with_known_names(workspace) -> None:
    with pytest.raises(ValueError, match="smoke"):
        resolve_suite("nope", {"smoke": {"tests": ["x.feature"]}})


def test_resolve_suite_expands_directories_sorted_and_dedupes(workspace) -> None:
    _make_feature(workspace, "a.feature")
    _make_feature(workspace, "c.feature")
    (workspace / "cases").mkdir()
    _make_feature(workspace / "cases", "b.feature")
    (workspace / "cases" / "notes.txt").write_text("not a feature", encoding="utf-8")

    shared = str(workspace / "a.feature")
    suites = {
        "base": {"tests": [shared]},
        "all": {"tests": [str(workspace / "cases"), shared], "depends_on": ["base"]},
    }

    resolved = resolve_suite("all", suites)
    assert resolved == [shared, str(workspace / "cases" / "b.feature")]
    assert resolved.count(shared) == 1  # dedup keeps first (dependency) position


def test_resolve_suite_missing_entry_raises(workspace) -> None:
    suites = {"ghost": {"tests": ["does_not_exist.feature"]}}
    with pytest.raises(ValueError, match="does_not_exist"):
        resolve_suite("ghost", suites)


def test_resolve_suite_compiles_plan_when_tests_empty(workspace) -> None:
    plan = TestPlan(
        meta={"project": "Demo"},
        requirements=[{"id": "REQ-001", "description": "d", "source": "prd", "test_ids": ["TC001"]}],
        test_cases=[TestCase(id="TC001", title="Login succeeds", steps=[PlanStep(type="action", description="open login")])],
    )
    plan_path = str(workspace / "test_plan.json")
    plan_to_json(plan, plan_path)
    suites = {"planned": {"plan": plan_path}}

    resolved = resolve_suite("planned", suites)

    assert len(resolved) == 1
    feature_path = resolved[0]
    assert os.path.isfile(feature_path) and feature_path.endswith(".feature")
    with open(feature_path, encoding="utf-8") as handle:
        content = handle.read()
    assert content.startswith("Feature:") and "TC001" in content


def test_resolve_suite_diamond_through_plan_compiles_once(workspace) -> None:
    # a -> [b, c] -> d(plan): the plan must compile once, not once per path
    # through the diamond (two temp files would duplicate its scenarios).
    plan = TestPlan(
        meta={"project": "Demo"},
        requirements=[{"id": "REQ-001", "description": "d", "source": "prd", "test_ids": ["TC001"]}],
        test_cases=[TestCase(id="TC001", title="Login succeeds", steps=[PlanStep(type="action", description="open login")])],
    )
    plan_path = str(workspace / "test_plan.json")
    plan_to_json(plan, plan_path)
    fa = _make_feature(workspace, "a.feature")
    suites = {
        "a": {"tests": [fa], "depends_on": ["b", "c"]},
        "b": {"depends_on": ["d"]},
        "c": {"depends_on": ["d"]},
        "d": {"plan": plan_path},
    }

    compiled_dirs: list[str] = []
    resolved = resolve_suite("a", suites, compiled_dirs=compiled_dirs)

    assert len(compiled_dirs) == 1  # one compilation, not two
    compiled = os.path.join(compiled_dirs[0], "plan.feature")
    assert resolved == [compiled, fa]
    assert resolved.count(compiled) == 1


def test_resolve_suite_warns_when_dependency_config_is_ignored(workspace, caplog) -> None:
    fa = _make_feature(workspace, "a.feature")
    fb = _make_feature(workspace, "b.feature")
    suites = {
        "base": {"tests": [fb], "retry": 1, "max_parallel": 3, "env": {"WHO": "base"}},
        "top": {"tests": [fa], "depends_on": ["base"]},
    }

    with caplog.at_level(logging.WARNING):
        resolve_suite("top", suites)

    assert any("Dependency suite 'base' config (retry/env/max_parallel) is ignored when folded into 'top'" in record.getMessage() for record in caplog.records)


def test_resolve_suite_matching_dependency_config_stays_quiet(workspace, caplog) -> None:
    fa = _make_feature(workspace, "a.feature")
    fb = _make_feature(workspace, "b.feature")
    suites = {
        "base": {"tests": [fb], "retry": 2},
        "top": {"tests": [fa], "depends_on": ["base"], "retry": 2, "max_parallel": 1},  # all three keys match
    }

    with caplog.at_level(logging.WARNING):
        resolve_suite("top", suites)

    assert not any("ignored when folded" in record.getMessage() for record in caplog.records)


# ── run_suite ─────────────────────────────────────────────────────────────────


def test_run_suite_sequential_with_retry_until_pass(workspace, monkeypatch) -> None:
    feature = _make_feature(workspace, "flaky.feature")
    path = _write_suites_yaml(workspace, "s:\n  tests: [flaky.feature]\n  retry: 1\n")
    calls: list[str] = []

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        calls.append(feature_path)
        return _fake_result("", feature_path, workroot, ok=len(calls) > 1, call_no=len(calls))

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    summary = asyncio.run(run_suite("s", path=path))

    assert len(calls) == 2  # initial attempt + one retry
    assert summary["retries_used"] == 1
    assert summary["per_scenario"][0]["ok"] is True
    assert summary["per_scenario"][0]["retries"] == 1
    assert summary["passed"] == 1 and summary["failed"] == 0 and summary["errors"] == 0
    assert summary["merged_xml"] is not None and os.path.isfile(summary["merged_xml"])


def test_run_suite_retry_exhausted_marks_failure(workspace, monkeypatch) -> None:
    feature = _make_feature(workspace, "broken.feature")
    path = _write_suites_yaml(workspace, "s:\n  tests: [broken.feature]\n  retry: 2\n")
    calls: list[int] = []

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        calls.append(1)
        return _fake_result("", feature_path, workroot, ok=False, call_no=len(calls))

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    summary = asyncio.run(run_suite("s", path=path))

    assert len(calls) == 3  # 1 initial + 2 retries
    assert summary["retries_used"] == 2
    assert summary["per_scenario"][0]["ok"] is False
    assert summary["failed"] == 1


def test_run_suite_parallel_splits_features_and_applies_env(workspace, monkeypatch) -> None:
    feature = _make_feature(workspace, "multi.feature", scenarios=2)
    path = _write_suites_yaml(workspace, "s:\n  tests: [multi.feature]\n  max_parallel: 2\n  env: {SUITE_VAR: hello}\n")
    captured: list[dict] = []

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        captured.append({"feature_path": feature_path, "workroot": workroot, "env_extra": env_extra})
        return _fake_result(os.path.basename(feature_path).replace(".feature", ""), feature_path, workroot, ok=True)

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    summary = asyncio.run(run_suite("s", path=path))

    assert len(captured) == 2  # split into per-scenario runs
    assert all(call["feature_path"].startswith(os.path.join(call["workroot"], "split", "feature_000")) for call in captured)
    assert all(call["env_extra"] == {"SUITE_VAR": "hello"} for call in captured)
    assert [entry["scenario"] for entry in summary["per_scenario"]] == ["s0", "s1"]
    assert summary["passed"] == 2 and summary["retries_used"] == 0


def test_run_suite_max_parallel_override_switches_to_split(workspace, monkeypatch) -> None:
    feature = _make_feature(workspace, "multi.feature", scenarios=2)
    path = _write_suites_yaml(workspace, "s:\n  tests: [multi.feature]\n")  # default max_parallel=1
    calls: list[str] = []

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        calls.append(feature_path)
        return _fake_result("", feature_path, workroot, ok=True)

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    asyncio.run(run_suite("s", path=path))
    assert len(calls) == 1  # sequential: one subprocess per feature file

    asyncio.run(run_suite("s", path=path, max_parallel_override=2))
    assert len(calls) == 3  # 1 + 2 split scenarios


def test_run_suite_shared_state_env_wiring(workspace, monkeypatch) -> None:
    feature = _make_feature(workspace, "chained.feature")
    path = _write_suites_yaml(workspace, "s:\n  tests: [chained.feature]\n  shared_state: true\n")
    captured: list[dict] = []

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        captured.append({"workroot": workroot, "env_extra": env_extra})
        return _fake_result("", feature_path, workroot, ok=True)

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    summary = asyncio.run(run_suite("s", path=path))

    state_file = captured[0]["env_extra"][SHARED_STATE_ENV_VAR]
    assert state_file == os.path.join(summary["workroot"], "suite_state.json")
    assert state_file.startswith(str(workspace))


def test_run_suite_totals_merge_junit_and_count_junitless_runs(workspace, monkeypatch) -> None:
    fa = _make_feature(workspace, "ok.feature")
    fb = _make_feature(workspace, "crash.feature")

    def _xml(directory, content):
        os.makedirs(directory, exist_ok=True)
        junit = os.path.join(directory, "r_result.xml")
        with open(junit, "w", encoding="utf-8") as handle:
            handle.write(content)
        return junit

    junit_ok = _xml(str(workspace / "j" / "ok"), PASSED_JUNIT)

    async def fake_isolated2(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        if os.path.basename(feature_path) == "ok.feature":
            return _fake_result("", feature_path, workroot, ok=True, junit_xml=junit_ok)
        return FeatureResult(scenario="crash", feature_path=feature_path, workspace="ws", ok=False, exit_code=None, duration_s=0.1, error="Timed out after 1800s; process killed.")

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated2)
    path = _write_suites_yaml(workspace, "s:\n  tests: [ok.feature, crash.feature]\n")

    summary = asyncio.run(run_suite("s", path=path))

    # ok.feature's testcase counted via merged XML; crash.feature produced no junit
    # and is folded in as an error.
    assert summary["total"] == 2
    assert summary["passed"] == 1
    assert summary["errors"] == 1 and summary["failed"] == 0
    assert os.path.isfile(summary["merged_xml"])


def test_run_suite_unknown_suite_raises(workspace) -> None:
    path = _write_suites_yaml(workspace, "real:\n  tests: []\n  plan: {}/plan.json\n".format(workspace))
    with pytest.raises(ValueError, match="Unknown suite 'ghost'"):
        asyncio.run(run_suite("ghost", path=path))


# ── dependency gating (depends_on must pass before own features run) ──────────


def _dep_suite_config(directory, extra_top: str = "") -> str:
    """base(=dep.feature) <- top(=own.feature, depends_on base); extra keys go on top."""
    _make_feature(directory, "dep.feature")
    _make_feature(directory, "own.feature")
    return _write_suites_yaml(directory, f"base:\n  tests: [dep.feature]\ntop:\n  tests: [own.feature]\n  depends_on: [base]\n{extra_top}")


def test_run_suite_skips_own_features_when_dependency_fails(workspace, monkeypatch) -> None:
    path = _dep_suite_config(workspace)
    calls: list[str] = []

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        calls.append(os.path.basename(feature_path))
        return _fake_result("", feature_path, workroot, ok=os.path.basename(feature_path) != "dep.feature")

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    summary = asyncio.run(run_suite("top", path=path))

    assert calls == ["dep.feature"]  # own.feature never executed
    entries = {entry["feature"]: entry for entry in summary["per_scenario"]}
    assert entries["dep"]["ok"] is False
    assert entries["own"]["ok"] is False
    assert entries["own"]["error"] == "skipped: dependency 'base' failed"
    assert entries["own"]["retries"] == 0 and entries["own"]["duration_s"] == 0.0
    # skipped own features are counted (as errors) and flip the exit code via _exit_code_for
    assert summary["total"] == 2 and summary["failed"] == 1 and summary["errors"] == 1


def test_run_suite_dependency_passes_own_features_run(workspace, monkeypatch) -> None:
    path = _dep_suite_config(workspace)
    calls: list[str] = []

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        calls.append(os.path.basename(feature_path))
        return _fake_result("", feature_path, workroot, ok=True)

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    summary = asyncio.run(run_suite("top", path=path))

    assert calls == ["dep.feature", "own.feature"]  # dependency first, then own
    assert all(entry["ok"] for entry in summary["per_scenario"])
    assert summary["passed"] == 2 and summary["errors"] == 0 and summary["failed"] == 0


def test_run_suite_gating_disabled_runs_own_features_despite_dependency_failure(workspace, monkeypatch) -> None:
    path = _dep_suite_config(workspace, extra_top="  skip_on_dependency_failure: false\n")
    calls: list[str] = []

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        calls.append(os.path.basename(feature_path))
        return _fake_result("", feature_path, workroot, ok=os.path.basename(feature_path) != "dep.feature")

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    summary = asyncio.run(run_suite("top", path=path))

    assert calls == ["dep.feature", "own.feature"]  # ordering-only: own still runs
    assert not any(str(entry["error"]).startswith("skipped:") for entry in summary["per_scenario"])
    assert summary["failed"] == 1 and summary["errors"] == 0


def test_run_suite_dependency_results_map_gates_without_rerunning(workspace, monkeypatch) -> None:
    # Monitor contract: dependency features already executed earlier in the
    # cycle are taken from the map (not re-run); own results are written back.
    path = _dep_suite_config(workspace)
    dep_path = str(workspace / "dep.feature")
    own_path = str(workspace / "own.feature")
    calls: list[str] = []

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        calls.append(os.path.basename(feature_path))
        return _fake_result("", feature_path, workroot, ok=True)

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    dep_map = {dep_path: True}
    summary = asyncio.run(run_suite("top", path=path, dependency_results=dep_map))

    assert calls == ["own.feature"]  # dependency not re-executed
    assert summary["passed"] == 1
    assert dep_map[dep_path] is True  # pre-known result preserved
    assert dep_map[own_path] is True  # own result recorded for later suites

    dep_map = {dep_path: False}  # dependency failed earlier in the cycle
    summary = asyncio.run(run_suite("top", path=path, dependency_results=dep_map))

    assert calls == ["own.feature"]  # still no dependency execution
    own_entry = next(entry for entry in summary["per_scenario"] if entry["feature"] == "own")
    assert own_entry["ok"] is False and own_entry["error"] == "skipped: dependency 'base' failed"
    assert dep_map[own_path] is False  # skipped features are recorded as not-passed

    dep_map = {}  # dependency outside this cycle: it is executed here so gating stays honest
    summary = asyncio.run(run_suite("top", path=path, dependency_results=dep_map))

    assert calls == ["own.feature", "dep.feature", "own.feature"]
    assert summary["passed"] == 2 and dep_map == {dep_path: True, own_path: True}


def test_run_suite_dependency_cycle_still_detected(workspace) -> None:
    _make_feature(workspace, "a.feature")
    _make_feature(workspace, "b.feature")
    path = _write_suites_yaml(workspace, "a:\n  tests: [a.feature]\n  depends_on: [b]\nb:\n  tests: [b.feature]\n  depends_on: [a]\n")

    with pytest.raises(ValueError, match="[Cc]ycle"):
        asyncio.run(run_suite("a", path=path))


def test_run_suite_plan_dependency_failing_gates_own_features(workspace, monkeypatch) -> None:
    # Plan-mode suites participate in gating: the compiled plan feature of the
    # dependency fails, so the dependent suite's own features are skipped.
    plan = TestPlan(
        meta={"project": "Demo"},
        requirements=[{"id": "REQ-001", "description": "d", "source": "prd", "test_ids": ["TC001"]}],
        test_cases=[TestCase(id="TC001", title="Login succeeds", steps=[PlanStep(type="action", description="open login")])],
    )
    plan_to_json(plan, str(workspace / "test_plan.json"))
    _make_feature(workspace, "own.feature")
    path = _write_suites_yaml(workspace, "planned:\n  plan: test_plan.json\ntop:\n  tests: [own.feature]\n  depends_on: [planned]\n")
    calls: list[str] = []

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        calls.append(os.path.basename(feature_path))
        return _fake_result("", feature_path, workroot, ok=os.path.basename(feature_path) != "plan.feature")

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    summary = asyncio.run(run_suite("top", path=path))

    assert calls == ["plan.feature"]  # compiled plan of the dependency ran (and failed)
    own_entry = next(entry for entry in summary["per_scenario"] if entry["feature"] == "own")
    assert own_entry["ok"] is False and own_entry["error"] == "skipped: dependency 'planned' failed"


# ── hardening: diagnostics for silently-lossy suite runs ──────────────────────


def test_run_suite_retry_log_attempt_wording(workspace, monkeypatch, caplog) -> None:
    _make_feature(workspace, "broken.feature")
    path = _write_suites_yaml(workspace, "s:\n  tests: [broken.feature]\n  retry: 1\n")

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        return _fake_result("", feature_path, workroot, ok=False)

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    with caplog.at_level(logging.INFO):
        asyncio.run(run_suite("s", path=path))

    assert "attempt 2 of 2" in caplog.text  # retry 1 of 1: attempt 2 of 2 total
    assert "attempt 2/1" not in caplog.text  # the off-by-one wording never reappears


def test_run_suite_parallel_retry_round_wording(workspace, monkeypatch, caplog) -> None:
    _make_feature(workspace, "multi.feature", scenarios=2)
    path = _write_suites_yaml(workspace, "s:\n  tests: [multi.feature]\n  max_parallel: 2\n  retry: 1\n")

    async def fake_parallel(scenario_files, max_parallel=1, workroot=None, timeout_s=1800.0, env_extra=None, progress_cb=None):
        return [_fake_result(item["scenario"], item["output_file"], workroot, ok=False) for item in scenario_files]

    monkeypatch.setattr(run_manager_module, "run_scenarios_parallel", fake_parallel)

    with caplog.at_level(logging.INFO):
        asyncio.run(run_suite("s", path=path))

    assert "retry round 1 of 1 re-running 2 failed scenario(s)" in caplog.text


def test_run_suite_warns_on_junit_result_disagreement(workspace, monkeypatch, caplog) -> None:
    fa = _make_feature(workspace, "ok.feature")
    fb = _make_feature(workspace, "crash.feature")

    def _xml(directory, content):
        os.makedirs(directory, exist_ok=True)
        junit = os.path.join(directory, "r_result.xml")
        with open(junit, "w", encoding="utf-8") as handle:
            handle.write(content)
        return junit

    junit_failed = _xml(str(workspace / "j" / "ok"), FAILED_JUNIT)  # junit says failed...
    junit_passed = _xml(str(workspace / "j" / "crash"), PASSED_JUNIT)  # ...junit says passed

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        if os.path.basename(feature_path) == "ok.feature":
            return _fake_result("ok", feature_path, workroot, ok=True, junit_xml=junit_failed)  # ...subprocess says ok
        return _fake_result("crash", feature_path, workroot, ok=False, junit_xml=junit_passed)  # ...subprocess says failed

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)
    path = _write_suites_yaml(workspace, "s:\n  tests: [ok.feature, crash.feature]\n")

    with caplog.at_level(logging.WARNING):
        summary = asyncio.run(run_suite("s", path=path))

    assert "junit and subprocess result disagree (junit=failed, ok=True" in caplog.text
    assert "junit and subprocess result disagree (junit=passed, ok=False" in caplog.text
    # Totals stay junit-derived: failed testcase -> failed, passed testcase -> passed.
    assert summary["total"] == 2 and summary["passed"] == 1 and summary["failed"] == 1


def test_run_suite_warns_when_suite_env_redefines_shared_state_file(workspace, monkeypatch, caplog) -> None:
    _make_feature(workspace, "chained.feature")
    path = _write_suites_yaml(workspace, "s:\n  tests: [chained.feature]\n  shared_state: true\n  env: {HERCULES_SUITE_STATE_FILE: /tmp/custom_state.json}\n")
    captured: list[dict] = []

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        captured.append({"env_extra": env_extra})
        return _fake_result("", feature_path, workroot, ok=True)

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    with caplog.at_level(logging.WARNING):
        summary = asyncio.run(run_suite("s", path=path))

    assert any("HERCULES_SUITE_STATE_FILE" in record.getMessage() and "overridden" in record.getMessage() for record in caplog.records)
    # ...and the injected per-suite path still wins.
    assert captured[0]["env_extra"][SHARED_STATE_ENV_VAR] == os.path.join(summary["workroot"], SUITE_STATE_FILENAME)


# ── hardening: compiled-plan tempdir cleanup + unique workroots ───────────────


def _plan_suite_config(directory) -> str:
    """Write a plan-only suite config plus its test_plan.json; returns the yaml path."""
    plan = TestPlan(
        meta={"project": "Demo"},
        requirements=[{"id": "REQ-001", "description": "d", "source": "prd", "test_ids": ["TC001"]}],
        test_cases=[TestCase(id="TC001", title="Login succeeds", steps=[PlanStep(type="action", description="open login")])],
    )
    plan_path = os.path.join(str(directory), "test_plan.json")
    plan_to_json(plan, plan_path)
    return _write_suites_yaml(directory, "planned:\n  plan: test_plan.json\n")


def test_run_suite_cleans_compiled_plan_dirs_after_finish(workspace, monkeypatch) -> None:
    path = _plan_suite_config(workspace)
    captured: dict = {}

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        captured["compiled_dir"] = os.path.dirname(feature_path)  # the tempfile.mkdtemp of the compiled plan
        return _fake_result("", feature_path, workroot, ok=True)

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    asyncio.run(run_suite("planned", path=path))

    compiled_dir = captured["compiled_dir"]
    assert compiled_dir.startswith(tempfile.gettempdir())
    assert not os.path.isdir(compiled_dir)  # removed once the suite finished


def test_run_suite_compiled_plan_dirs_kept_with_keep_workspaces_opt_out(workspace, monkeypatch) -> None:
    monkeypatch.setenv("HERCULES_KEEP_WORKSPACES", "1")
    path = _plan_suite_config(workspace)
    captured: dict = {}

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        captured["compiled_dir"] = os.path.dirname(feature_path)
        return _fake_result("", feature_path, workroot, ok=True)

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    asyncio.run(run_suite("planned", path=path))

    assert os.path.isfile(os.path.join(captured["compiled_dir"], "plan.feature"))


def test_run_suite_workroot_names_are_unique_within_same_second(workspace, monkeypatch) -> None:
    path = _plan_suite_config(workspace)
    workroots: list[str] = []

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        workroots.append(workroot)
        return _fake_result("", feature_path, workroot, ok=True)

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    asyncio.run(run_suite("planned", path=path))
    asyncio.run(run_suite("planned", path=path))

    assert len(workroots) == 2
    assert workroots[0] != workroots[1]  # same-second suite runs must not share a workroot
    for workroot in workroots:
        suffix = os.path.basename(workroot).rsplit("_", 1)[1]
        assert len(suffix) == 32 and all(char in "0123456789abcdef" for char in suffix)  # full uuid4 hex, not [:8]


# ── hardening round 2: no dangling workspace paths in the summary ────────────


def test_run_suite_summary_blanks_paths_of_cleaned_successful_workspaces(workspace, monkeypatch) -> None:
    feature = _make_feature(workspace, "ok.feature")
    path = _write_suites_yaml(workspace, "s:\n  tests: [ok.feature]\n")

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        ws = os.path.join(workroot, "scenario_1")
        os.makedirs(ws, exist_ok=True)
        junit = os.path.join(ws, "r_result.xml")
        with open(junit, "w", encoding="utf-8") as handle:
            handle.write(PASSED_JUNIT)
        result = _fake_result("s0", feature_path, workroot, ok=True, junit_xml=junit)
        result.workspace = ws
        return result

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    summary = asyncio.run(run_suite("s", path=path))

    assert summary["passed"] == 1
    entry = summary["per_scenario"][0]
    assert entry["ok"] is True
    assert entry["workspace"] == "" and entry["junit_xml"] is None  # workspace was removed; no dangling paths
    assert os.path.isfile(summary["merged_xml"])  # merged report lives outside workspaces


def test_run_suite_summary_keeps_real_paths_for_failures(workspace, monkeypatch) -> None:
    feature = _make_feature(workspace, "broken.feature")
    path = _write_suites_yaml(workspace, "s:\n  tests: [broken.feature]\n")

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        ws = os.path.join(workroot, "scenario_1")
        os.makedirs(ws, exist_ok=True)
        junit = os.path.join(ws, "r_result.xml")
        with open(junit, "w", encoding="utf-8") as handle:
            handle.write(FAILED_JUNIT)
        result = _fake_result("s0", feature_path, workroot, ok=False, junit_xml=junit)
        result.workspace = ws
        return result

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    summary = asyncio.run(run_suite("s", path=path))

    assert summary["failed"] == 1
    entry = summary["per_scenario"][0]
    assert entry["ok"] is False
    assert entry["workspace"] and os.path.isdir(entry["workspace"])  # failures keep their workspace
    assert entry["junit_xml"] and os.path.isfile(entry["junit_xml"])


# ── suite state JSONL (append_suite_state) ────────────────────────────────────


def test_append_suite_state_writes_valid_jsonl_lines(tmp_path) -> None:
    workroot = str(tmp_path)
    append_suite_state(workroot, "smoke", {"total": 2, "passed": 2, "failed": 0, "errors": 0, "retries_used": 0})
    append_suite_state(workroot, "regression", {"total": 3, "passed": 1, "failed": 2, "errors": 0, "retries_used": 1})

    with open(os.path.join(workroot, SUITE_STATE_FILENAME), encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert len(lines) == 2  # append-only JSONL: one record per completion
    records = [json.loads(line) for line in lines]
    assert records[0]["suite"] == "smoke" and records[0]["passed"] == 2
    assert records[1]["suite"] == "regression" and records[1]["failed"] == 2


def test_append_suite_state_concurrent_appends_stay_intact(tmp_path) -> None:
    workroot = str(tmp_path)

    def _append(index: int) -> None:
        append_suite_state(workroot, f"suite_{index}", {"total": 1, "passed": 1, "failed": 0, "errors": 0, "retries_used": 0})

    threads = [threading.Thread(target=_append, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with open(os.path.join(workroot, SUITE_STATE_FILENAME), encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]  # both lines are whole, valid JSON
    assert sorted(record["suite"] for record in records) == ["suite_0", "suite_1"]


def test_run_suite_appends_path_free_completion_state(workspace, monkeypatch) -> None:
    _make_feature(workspace, "ok.feature")
    path = _write_suites_yaml(workspace, "s:\n  tests: [ok.feature]\n")

    async def fake_isolated(feature_path, workroot, env_extra=None, timeout_s=1800.0):
        return _fake_result("", feature_path, workroot, ok=True)

    monkeypatch.setattr(run_manager_module, "run_feature_isolated", fake_isolated)

    summary = asyncio.run(run_suite("s", path=path))

    with open(os.path.join(summary["workroot"], SUITE_STATE_FILENAME), encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle.read().splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["suite"] == "s"
    assert record["total"] == 1 and record["passed"] == 1 and record["failed"] == 0
    # The state entry is the summary sans paths: no path-bearing keys leak in.
    for path_key in ("per_scenario", "merged_xml", "workroot", "config"):
        assert path_key not in record


# ── example config + CLI ──────────────────────────────────────────────────────


def test_suites_example_yaml_is_valid() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    example = os.path.join(repo_root, ".hercules", "suites.example.yaml")

    suites = load_suites(example)

    assert set(suites) == {"smoke", "regression"}
    assert suites["smoke"]["retry"] == 1 and suites["smoke"]["max_parallel"] == 4
    assert suites["regression"]["depends_on"] == ["smoke"]
    assert suites["regression"]["shared_state"] is True
    assert suites["regression"]["env"] == {"HERCULES_RUN_MODE": "regression"}


def test_suites_cli_exit_codes(workspace, monkeypatch, capsys) -> None:
    path = _write_suites_yaml(workspace, "s:\n  tests: [x.feature]\n")
    summary = {
        "suite": "s",
        "total": 2,
        "passed": 1,
        "failed": 1,
        "errors": 0,
        "retries_used": 0,
        "merged_xml": None,
        "per_scenario": [
            {"scenario": "s0", "feature": "f", "ok": True, "retries": 0, "duration_s": 0.1, "junit_xml": None, "error": None, "workspace": "ws"},
            {"scenario": "s1", "feature": "f", "ok": False, "retries": 0, "duration_s": 0.2, "junit_xml": None, "error": "boom", "workspace": "ws"},
        ],
    }

    async def fake_run_suite(name, path=".hercules/suites.yaml", max_parallel_override=None, out_dir=None, timeout_s=1800.0):
        return summary

    monkeypatch.setattr(suites_module, "run_suite", fake_run_suite)

    assert main(["s", "--config", path]) == 1
    out = capsys.readouterr().out
    assert "[ok] s0" in out and "[FAILED] s1" in out and "Suite 's' totals:" in out

    monkeypatch.setenv("EXIT_CODE_COMPAT", "1")
    assert main(["s", "--config", path]) == 0


def test_suites_cli_success_exits_zero(workspace, monkeypatch) -> None:
    path = _write_suites_yaml(workspace, "s:\n  tests: [x.feature]\n")

    async def fake_run_suite(name, path=".hercules/suites.yaml", max_parallel_override=None, out_dir=None, timeout_s=1800.0):
        return {"suite": name, "total": 1, "passed": 1, "failed": 0, "errors": 0, "retries_used": 0, "merged_xml": None, "per_scenario": []}

    monkeypatch.setattr(suites_module, "run_suite", fake_run_suite)
    assert main(["s", "--config", path, "--max-parallel", "3"]) == 0


def test_suites_cli_prints_skipped_status_for_gated_features(workspace, monkeypatch, capsys) -> None:
    path = _write_suites_yaml(workspace, "s:\n  tests: [x.feature]\n")
    summary = {
        "suite": "s",
        "total": 2,
        "passed": 0,
        "failed": 1,
        "errors": 1,
        "retries_used": 0,
        "merged_xml": None,
        "per_scenario": [
            {"scenario": "dep", "feature": "dep", "ok": False, "retries": 0, "duration_s": 0.3, "junit_xml": None, "error": "Subprocess exited with code 1.", "workspace": "ws"},
            {"scenario": "own", "feature": "own", "ok": False, "retries": 0, "duration_s": 0.0, "junit_xml": None, "error": "skipped: dependency 'base' failed", "workspace": ""},
        ],
    }

    async def fake_run_suite(name, path=".hercules/suites.yaml", max_parallel_override=None, out_dir=None, timeout_s=1800.0):
        return summary

    monkeypatch.setattr(suites_module, "run_suite", fake_run_suite)

    assert main(["s", "--config", path]) == 1
    out = capsys.readouterr().out
    assert "[FAILED] dep" in out and "[SKIPPED] own" in out and "skipped: dependency 'base' failed" in out


def test_main_cli_missing_config_is_a_clean_error(tmp_path, capsys) -> None:
    assert main(["smoke", "--config", str(tmp_path / "missing.yaml")]) == 2
    err = capsys.readouterr().err
    assert "hercules-suite: error: Suites config not found" in err
    assert "Traceback" not in err


def test_main_cli_unknown_suite_is_a_clean_error(tmp_path, capsys) -> None:
    feature = tmp_path / "a.feature"
    feature.write_text("Feature: a\n\n  Scenario: s\n    Given a step\n", encoding="utf-8")
    config = tmp_path / "suites.yaml"
    config.write_text(f"smoke:\n  tests: [{feature}]\n", encoding="utf-8")
    assert main(["nope", "--config", str(config)]) == 2
    err = capsys.readouterr().err
    assert "Unknown suite 'nope'" in err and "Traceback" not in err


def test_main_cli_invalid_yaml_is_a_clean_error(tmp_path, capsys) -> None:
    config = tmp_path / "suites.yaml"
    config.write_text("smoke: [unclosed\n", encoding="utf-8")
    assert main(["smoke", "--config", str(config)]) == 2
    assert "hercules-suite: error:" in capsys.readouterr().err
