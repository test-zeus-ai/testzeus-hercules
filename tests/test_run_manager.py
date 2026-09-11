"""Offline tests for the parallel run manager.

No LLM, no browsers, no real Hercules runs: the subprocess command is
monkeypatched to trivial ``python -c`` children so parallelism, timeouts,
env wiring and junit merging are exercised with real (fast) subprocesses.
"""

import asyncio
import logging
import os
import signal
import sys
import time

import pytest
import testzeus_hercules.orchestrator.run_manager as run_manager_module
from testzeus_hercules.orchestrator.run_manager import (
    FeatureResult,
    _fold_missing_results_into_totals,
    _prepare_workspace,
    main,
    merge_results,
    merge_results_async,
    run_feature_file_parallel,
    run_feature_isolated,
    run_scenarios_parallel,
)
from testzeus_hercules.reporting.bundle import parse_junit_testcases

FAST_EXIT_CODE = "import sys, os; sys.exit(int(os.environ.get('FAIL_ME', '0')))"
ECHO_MARKER_CODE = "import os; print(os.environ.get('MARKER', 'marker-missing')); print('to stderr', file=os.sys.stderr)"
SLEEP_CODE = "import time; time.sleep(0.5)"

MINIMAL_JUNIT = (
    '<testsuites><testsuite name="Feature: demo" tests="1" failures="0" errors="0" time="0.1">' '<testcase name="Scenario: demo" classname="Feature: demo" time="0.1"/>' "</testsuite></testsuites>"
)
FAILED_JUNIT = (
    '<testsuites><testsuite name="Feature: demo" tests="1" failures="1" errors="0" time="0.2">'
    '<testcase name="Scenario: demo" classname="Feature: demo" time="0.2"><failure message="boom">boom</failure></testcase>'
    "</testsuite></testsuites>"
)


def _command(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _junit_writer_command(xml: str = MINIMAL_JUNIT) -> list[str]:
    # cwd of the child is the workspace, so the relative path lands under
    # <workspace>/opt/output/<run>/<name>_result.xml where discovery looks.
    code = f"import pathlib; p = pathlib.Path('opt/output/run1'); p.mkdir(parents=True, exist_ok=True); (p / 'demo_result.xml').write_text({xml!r})"
    return _command(code)


def _make_feature(directory, name: str = "demo.feature", scenarios: int = 1):
    body = "".join(f"  Scenario: s{i}\n    Given a step\n\n" for i in range(scenarios))
    path = os.path.join(str(directory), name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"Feature: demo\n\n{body}")
    return path


def _make_items(directory, count: int, scenario_prefix: str = "s") -> list[dict]:
    items = []
    for index in range(count):
        path = _make_feature(directory, name=f"scenario_{index}.feature")
        items.append({"feature": "demo", "scenario": f"{scenario_prefix}{index}", "output_file": path})
    return items


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Isolate cwd so default workroots (.hercules/...) land under tmp."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ── _prepare_workspace ────────────────────────────────────────────────────────


def test_prepare_workspace_copies_feature_and_sibling_test_data(tmp_path) -> None:
    feature = _make_feature(tmp_path)
    test_data = tmp_path / "test_data"
    test_data.mkdir()
    (test_data / "users.json").write_text('{"user": "alice"}', encoding="utf-8")

    result = _prepare_workspace(str(tmp_path / "runs"), feature, 3)

    assert result == str(tmp_path / "runs" / "scenario_3")
    copied = os.path.join(result, "opt", "input", "test.feature")
    assert os.path.isfile(copied)
    with open(copied, encoding="utf-8") as handle:
        assert "Feature: demo" in handle.read()
    assert (tmp_path / "runs" / "scenario_3" / "opt" / "test_data" / "users.json").is_file()


def test_prepare_workspace_without_test_data(tmp_path) -> None:
    feature = _make_feature(tmp_path)

    result = _prepare_workspace(str(tmp_path), feature, 0)

    assert os.path.isdir(os.path.join(result, "opt", "input"))
    assert not os.path.exists(os.path.join(result, "opt", "test_data"))


# ── run_feature_isolated ──────────────────────────────────────────────────────


def test_run_feature_isolated_success_and_env_wiring(workspace, monkeypatch) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command(ECHO_MARKER_CODE))
    feature = _make_feature(workspace)

    result = asyncio.run(run_feature_isolated(feature, str(workspace / "runs"), env_extra={"MARKER": "env-marker-42"}))

    assert isinstance(result, FeatureResult)
    assert result.ok is True
    assert result.exit_code == 0
    assert result.error is None
    assert "env-marker-42" in result.stdout_tail
    assert "to stderr" in result.stdout_tail  # stderr is captured too
    assert result.duration_s >= 0
    assert os.path.isdir(os.path.join(result.workspace, "opt", "input"))


def test_run_feature_isolated_nonzero_exit_marks_failure(workspace, monkeypatch) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command(FAST_EXIT_CODE))
    feature = _make_feature(workspace)

    result = asyncio.run(run_feature_isolated(feature, str(workspace / "runs"), env_extra={"FAIL_ME": "1"}))

    assert result.ok is False
    assert result.exit_code == 1
    assert "exited with code 1" in result.error


def test_run_feature_isolated_discovers_newest_junit_xml(workspace, monkeypatch) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _junit_writer_command())
    feature = _make_feature(workspace)

    result = asyncio.run(run_feature_isolated(feature, str(workspace / "runs")))

    assert result.ok is True
    assert result.junit_xml is not None and os.path.isfile(result.junit_xml)
    assert result.junit_xml.endswith("demo_result.xml")
    assert os.path.join("opt", "output") in result.junit_xml


def test_run_feature_isolated_without_junit_xml(workspace, monkeypatch) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command("pass"))
    feature = _make_feature(workspace)

    result = asyncio.run(run_feature_isolated(feature, str(workspace / "runs")))

    assert result.junit_xml is None


def test_run_feature_isolated_timeout_kills_child(workspace, monkeypatch) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command("import time; time.sleep(30)"))
    feature = _make_feature(workspace)

    start = time.monotonic()
    result = asyncio.run(run_feature_isolated(feature, str(workspace / "runs"), timeout_s=0.4))

    assert time.monotonic() - start < 10  # child was killed, not waited out
    assert result.ok is False
    assert result.exit_code is None
    assert result.error is not None and "Timed out" in result.error


# ── run_scenarios_parallel ────────────────────────────────────────────────────


def test_run_scenarios_parallel_runs_concurrently_and_preserves_order(workspace, monkeypatch) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command(SLEEP_CODE))
    items = _make_items(workspace, count=4)

    start = time.monotonic()
    results = asyncio.run(run_scenarios_parallel(items, max_parallel=4, workroot=str(workspace / "runs")))
    duration = time.monotonic() - start

    assert [result.scenario for result in results] == ["s0", "s1", "s2", "s3"]
    assert all(result.ok for result in results)
    assert all(result.workspace.startswith(str(workspace / "runs")) for result in results)
    assert len({result.workspace for result in results}) == 4  # distinct workspaces
    # 4 x 0.5s sleeps at 4-way parallelism must beat the sequential 2.0s.
    assert duration < 1.9


def test_run_scenarios_parallel_bounded_by_semaphore(workspace, monkeypatch) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command(SLEEP_CODE))
    items = _make_items(workspace, count=4)

    start = time.monotonic()
    asyncio.run(run_scenarios_parallel(items, max_parallel=2, workroot=str(workspace / "runs")))
    duration = time.monotonic() - start

    # 4 x 0.5s with at most 2 concurrent takes at least 1.0s.
    assert duration >= 0.95


def test_run_scenarios_parallel_progress_cb_on_completion(workspace, monkeypatch) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command("pass"))
    items = _make_items(workspace, count=3)
    seen: list[tuple[str, FeatureResult]] = []

    results = asyncio.run(run_scenarios_parallel(items, max_parallel=2, workroot=str(workspace / "runs"), progress_cb=lambda name, result: seen.append((name, result))))

    assert len(seen) == 3
    assert {name for name, _ in seen} == {"s0", "s1", "s2"}
    assert all(isinstance(result, FeatureResult) for _, result in seen)
    assert sorted(result.scenario for result in results) == ["s0", "s1", "s2"]


def test_run_scenarios_parallel_broken_progress_cb_is_ignored(workspace, monkeypatch) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command("pass"))
    items = _make_items(workspace, count=2)

    def _broken(name, result):
        raise RuntimeError("sink exploded")

    results = asyncio.run(run_scenarios_parallel(items, max_parallel=2, workroot=str(workspace / "runs"), progress_cb=_broken))
    assert len(results) == 2 and all(result.ok for result in results)


def test_run_scenarios_parallel_empty_input(workspace) -> None:
    assert asyncio.run(run_scenarios_parallel([], max_parallel=4)) == []


# ── merge_results ─────────────────────────────────────────────────────────────


def _result_with_junit(junit_path: str, scenario: str = "s0") -> FeatureResult:
    return FeatureResult(scenario=scenario, feature_path="x.feature", workspace="ws", ok=True, exit_code=0, junit_xml=junit_path)


def _write_xml(path, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def test_merge_results_async_totals_and_source_preservation(workspace) -> None:
    junit_a = _write_xml(str(workspace / "a" / "a_result.xml"), MINIMAL_JUNIT)
    junit_b = _write_xml(str(workspace / "b" / "b_result.xml"), FAILED_JUNIT)
    results = [_result_with_junit(junit_a, "s0"), _result_with_junit(junit_b, "s1")]

    totals = asyncio.run(merge_results_async(results, str(workspace / "merged")))

    assert totals["total"] == 2
    assert totals["passed"] == 1
    assert totals["failed"] == 1
    assert totals["errors"] == 0
    merged = os.path.join(str(workspace / "merged"), "merged_result.xml")
    assert totals["junit_xml"] == merged and os.path.isfile(merged)
    # Staged copies are merged; the originals in each workspace survive.
    assert os.path.isfile(junit_a) and os.path.isfile(junit_b)


def test_merge_results_sync_wrapper(workspace) -> None:
    junit_a = _write_xml(str(workspace / "a_result.xml"), MINIMAL_JUNIT)
    totals = merge_results([_result_with_junit(junit_a)], str(workspace / "merged"))
    assert totals["passed"] == 1 and totals["total"] == 1


def test_merge_results_sync_wrapper_rejects_running_loop(workspace) -> None:
    junit_a = _write_xml(str(workspace / "a_result.xml"), MINIMAL_JUNIT)

    async def _call() -> None:
        merge_results([_result_with_junit(junit_a)], str(workspace / "merged"))

    with pytest.raises(RuntimeError, match="running event loop"):
        asyncio.run(_call())


def test_merge_results_async_without_junit_files(workspace) -> None:
    results = [FeatureResult(scenario="s0", feature_path="x.feature", workspace="ws", ok=False, exit_code=None, error="Timed out")]
    totals = asyncio.run(merge_results_async(results, str(workspace / "merged")))
    assert totals == {"total": 0, "passed": 0, "failed": 0, "errors": 0, "junit_xml": os.path.join(str(workspace / "merged"), "merged_result.xml")}
    assert os.path.isfile(totals["junit_xml"])


# ── run_feature_file_parallel ─────────────────────────────────────────────────


def test_run_feature_file_parallel_end_to_end(workspace, monkeypatch) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _junit_writer_command())
    feature = _make_feature(workspace, scenarios=2)

    summary = asyncio.run(run_feature_file_parallel(feature, max_parallel=2))

    assert summary["total"] == 2
    assert summary["passed"] == 2
    assert summary["failed"] == 0 and summary["errors"] == 0
    assert summary["merged_xml"] is not None and os.path.isfile(summary["merged_xml"])
    assert summary["merged_xml"] == summary["junit_xml"]
    assert [entry["scenario"] for entry in summary["results"]] == ["s0", "s1"]
    assert all(entry["ok"] for entry in summary["results"])
    # Defaults land under <cwd>/.hercules/parallel_<ts>.
    assert summary["workroot"].startswith(str(workspace / ".hercules" / "parallel_"))


def test_run_feature_file_parallel_reports_failures(workspace, monkeypatch) -> None:
    # Both children fail (nonzero exit); only s1 writes a junit (as if it
    # failed an assertion before crashing) — totals must count both.
    def _command_mixed(ws):
        return _command("import pathlib, sys, os; p = pathlib.Path('opt/output/r'); p.mkdir(parents=True, exist_ok=True); (p / 'demo_result.xml').write_text(" + repr(FAILED_JUNIT) + "); sys.exit(1)")

    monkeypatch.setattr(run_manager_module, "_subprocess_command", _command_mixed)
    feature = _make_feature(workspace, scenarios=2)

    summary = asyncio.run(run_feature_file_parallel(feature, max_parallel=2))

    assert summary["total"] == 2
    assert summary["failed"] == 2 and summary["passed"] == 0
    assert all(entry["ok"] is False for entry in summary["results"])
    # Failures keep their workspace, so the summary keeps real existing paths.
    assert all(entry["junit_xml"] and os.path.isfile(entry["junit_xml"]) for entry in summary["results"])


def test_run_feature_file_parallel_no_scenarios(workspace) -> None:
    feature = workspace / "empty.feature"
    feature.write_text("Feature: nothing here\n", encoding="utf-8")

    summary = asyncio.run(run_feature_file_parallel(str(feature)))

    assert summary["total"] == 0
    assert summary["merged_xml"] is None
    assert "No scenarios" in summary["error"]


def test_run_feature_file_parallel_missing_file(workspace) -> None:
    with pytest.raises(FileNotFoundError):
        asyncio.run(run_feature_file_parallel(str(workspace / "missing.feature")))


# ── CLI ───────────────────────────────────────────────────────────────────────


def _cli_summary(ok: bool, failed: int, errors: int) -> dict:
    return {
        "total": 1,
        "passed": 1 if ok else 0,
        "failed": failed,
        "errors": errors,
        "merged_xml": None,
        "workroot": "ws",
        "results": [{"scenario": "s0", "ok": ok, "exit_code": 0 if ok else 1, "duration_s": 0.1, "junit_xml": None, "error": None if ok else "boom"}],
    }


def test_main_cli_exit_codes(workspace, monkeypatch, capsys) -> None:
    async def _fake(feature_file, max_parallel=4, out_dir=None):
        return _cli_summary(ok=False, failed=1, errors=0)

    monkeypatch.setattr(run_manager_module, "run_feature_file_parallel", _fake)
    assert main(["whatever.feature"]) == 1
    out = capsys.readouterr().out
    assert "[FAILED] s0" in out and "Totals:" in out

    monkeypatch.setenv("EXIT_CODE_COMPAT", "1")
    assert main(["whatever.feature"]) == 0


def test_main_cli_success_exits_zero(workspace, monkeypatch) -> None:
    async def _fake(feature_file, max_parallel=4, out_dir=None):
        return _cli_summary(ok=True, failed=0, errors=0)

    monkeypatch.setattr(run_manager_module, "run_feature_file_parallel", _fake)
    assert main(["whatever.feature"]) == 0


def test_main_cli_parses_args(workspace, monkeypatch) -> None:
    captured: dict = {}

    async def _fake(feature_file, max_parallel=4, out_dir=None):
        captured.update(feature_file=feature_file, max_parallel=max_parallel, out_dir=out_dir)
        return _cli_summary(ok=True, failed=0, errors=0)

    monkeypatch.setattr(run_manager_module, "run_feature_file_parallel", _fake)
    assert main(["some.feature", "--max-parallel", "7", "--out-dir", "od", "--timeout", "12"]) == 0
    assert captured["max_parallel"] == 7
    assert captured["out_dir"] == "od"
    assert captured["feature_file"].endswith("some.feature")


# ── hardening: gather robustness, kill race, cleanup, env injection ──────────


def test_run_scenarios_parallel_prep_failure_keeps_sibling_results(workspace, monkeypatch) -> None:
    # One _prepare_workspace blows up (vanished feature file); the already
    # running siblings must still report instead of the whole gather failing.
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command("pass"))
    items = _make_items(workspace, count=3)
    real_prepare = run_manager_module._prepare_workspace

    def _flaky_prepare(base_dir, feature_path, index):
        if os.path.basename(feature_path) == "scenario_1.feature":
            raise OSError(2, "No such file or directory", feature_path)
        return real_prepare(base_dir, feature_path, index)

    monkeypatch.setattr(run_manager_module, "_prepare_workspace", _flaky_prepare)

    results = asyncio.run(run_scenarios_parallel(items, max_parallel=2, workroot=str(workspace / "runs")))

    assert [result.scenario for result in results] == ["s0", "s1", "s2"]
    crashed = results[1]
    assert crashed.ok is False and crashed.exit_code is None
    # OSError errno-2 subclasses to FileNotFoundError: error is "Type: message".
    assert "FileNotFoundError" in crashed.error and "No such file or directory" in crashed.error
    assert crashed.workspace == ""
    assert all(result.ok for result in (results[0], results[2]))


def test_run_feature_isolated_timeout_tolerates_process_lookup_error(workspace, monkeypatch) -> None:
    # Child gets reaped between the timeout firing and kill(): the kill raises
    # ProcessLookupError, which must not escape run_feature_isolated.
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command("import time; time.sleep(0.4)"))
    feature = _make_feature(workspace)

    real_exec = asyncio.create_subprocess_exec

    async def _exec_with_failing_kill(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)

        def _raise_lookup():
            raise ProcessLookupError("already reaped")

        proc.kill = _raise_lookup
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec_with_failing_kill)

    result = asyncio.run(run_feature_isolated(feature, str(workspace / "runs"), timeout_s=0.05))

    assert result.ok is False and result.exit_code is None
    assert result.error is not None and "Timed out" in result.error


def test_feature_file_parallel_cleans_successful_workspaces(workspace, monkeypatch) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _junit_writer_command())
    feature = _make_feature(workspace, scenarios=2)

    summary = asyncio.run(run_feature_file_parallel(feature, max_parallel=2))

    assert all(entry["ok"] for entry in summary["results"])
    scenario_dirs = [name for name in os.listdir(summary["workroot"]) if name.startswith("scenario_")]
    assert scenario_dirs == []  # merged report already staged, workspaces removed
    assert os.path.isfile(summary["merged_xml"])
    # The removed workspaces must not surface as dangling paths; the merged
    # report lives outside the workspaces and stays real.
    assert all(entry["junit_xml"] in (None, "") for entry in summary["results"])
    assert summary["merged_xml"] == summary["junit_xml"]


def test_feature_file_parallel_keeps_failed_workspaces(workspace, monkeypatch) -> None:
    monkeypatch.setenv("FAIL_ME", "1")
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command(FAST_EXIT_CODE))
    feature = _make_feature(workspace, scenarios=2)

    summary = asyncio.run(run_feature_file_parallel(feature, max_parallel=2))

    assert all(entry["ok"] is False for entry in summary["results"])
    scenario_dirs = [name for name in os.listdir(summary["workroot"]) if name.startswith("scenario_")]
    assert len(scenario_dirs) == 2  # failures always keep their workspace for debugging


def test_feature_file_parallel_keep_workspaces_opt_out(workspace, monkeypatch) -> None:
    monkeypatch.setenv("HERCULES_KEEP_WORKSPACES", "1")
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _junit_writer_command())
    feature = _make_feature(workspace, scenarios=2)

    summary = asyncio.run(run_feature_file_parallel(feature, max_parallel=2))

    assert all(entry["ok"] for entry in summary["results"])
    scenario_dirs = [name for name in os.listdir(summary["workroot"]) if name.startswith("scenario_")]
    assert len(scenario_dirs) == 2  # opt-out keeps even successful workspaces


def test_subprocess_env_gets_host_history_db_outside_workspace(workspace, monkeypatch) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command("import os; print(os.environ.get('HERCULES_HISTORY_DB', 'unset'))"))
    monkeypatch.delenv("HERCULES_HISTORY_DB", raising=False)
    host_url = f"sqlite:///{workspace / 'host_history.db'}"
    monkeypatch.setattr("testzeus_hercules.reporting.run_db.RunHistory._default_db_url", staticmethod(lambda: host_url))
    feature = _make_feature(workspace)

    result = asyncio.run(run_feature_isolated(feature, str(workspace / "runs")))

    assert result.ok is True
    echoed = result.stdout_tail.strip()
    assert echoed == host_url  # resolved via run_db's own resolution
    assert str(workspace / "runs") not in echoed  # never inside the throwaway workspace


def test_subprocess_env_leaves_parent_history_db_untouched(workspace, monkeypatch) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command("import os; print(os.environ.get('HERCULES_HISTORY_DB', 'unset'))"))
    monkeypatch.setenv("HERCULES_HISTORY_DB", "sqlite:////custom/history.db")
    feature = _make_feature(workspace)

    result = asyncio.run(run_feature_isolated(feature, str(workspace / "runs")))

    assert result.ok is True
    assert result.stdout_tail.strip() == "sqlite:////custom/history.db"


def test_history_db_helper_anchors_relative_fallback(monkeypatch) -> None:
    monkeypatch.delenv("HERCULES_HISTORY_DB", raising=False)
    monkeypatch.setattr("testzeus_hercules.reporting.run_db.RunHistory._default_db_url", staticmethod(lambda: "sqlite:///./opt/hercules_history.db"))

    value = run_manager_module._history_db_for_subprocess()

    assert value is not None and value.startswith("sqlite:///")
    assert os.path.isabs(value[len("sqlite:///") :])  # relative fallback anchored to the parent cwd


def test_history_db_helper_skips_injection_when_parent_sets_it(monkeypatch) -> None:
    monkeypatch.setenv("HERCULES_HISTORY_DB", "/some/path.db")
    assert run_manager_module._history_db_for_subprocess() is None


def test_default_workroot_names_are_unique_within_same_second(workspace, monkeypatch) -> None:
    monkeypatch.chdir(workspace)
    first = run_manager_module._default_workroot()
    second = run_manager_module._default_workroot()

    assert first != second  # same-second runs must not share a workroot
    assert first.startswith(str(workspace / ".hercules" / "parallel_"))
    assert second.startswith(str(workspace / ".hercules" / "parallel_"))
    for workroot in (first, second):
        suffix = os.path.basename(workroot).rsplit("_", 1)[1]
        assert len(suffix) == 32 and all(char in "0123456789abcdef" for char in suffix)  # full uuid4 hex, not [:8]


# ── hardening round 2: group kill, env collisions, dangling-path redaction ───


def test_kill_process_tree_kills_process_group_of_stub_proc(monkeypatch) -> None:
    group_kills: list[tuple[int, int]] = []
    direct_kills: list[int] = []
    monkeypatch.setattr(os, "getpgid", lambda pid: 424242)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: group_kills.append((pgid, sig)))

    class _StubProc:
        pid = 424242

        def kill(self):
            direct_kills.append(self.pid)

    run_manager_module._kill_process_tree(_StubProc())

    assert group_kills == [(424242, signal.SIGKILL)]  # whole group killed, not just the child
    assert direct_kills == [424242]  # direct-child backstop still runs


def test_kill_process_tree_falls_back_to_direct_kill_when_group_gone(monkeypatch) -> None:
    direct_kills: list[int] = []

    def _raise_lookup(pid):
        raise ProcessLookupError("already reaped")

    monkeypatch.setattr(os, "getpgid", _raise_lookup)

    class _StubProc:
        pid = 9999

        def kill(self):
            direct_kills.append(self.pid)

    run_manager_module._kill_process_tree(_StubProc())

    assert direct_kills == [9999]  # the existing proc.kill() fallback still fires


def test_kill_process_tree_without_posix_kill_apis_falls_back_to_direct_kill(monkeypatch) -> None:
    # Windows has no os.killpg/os.getpgid: an unguarded call raises AttributeError
    # (wider than the old ProcessLookupError catch) and the child would never die.
    direct_kills: list[int] = []
    monkeypatch.delattr(os, "killpg", raising=False)
    monkeypatch.delattr(os, "getpgid", raising=False)

    class _StubProc:
        pid = 5150

        def kill(self):
            direct_kills.append(self.pid)

    run_manager_module._kill_process_tree(_StubProc())

    assert direct_kills == [5150]  # no AttributeError; the direct kill still ran


def test_run_feature_isolated_timeout_kills_whole_process_group(workspace, monkeypatch) -> None:
    # The child spawns a grandchild that inherits stdout; a bare proc.kill()
    # would orphan it holding the pipes and stall reaping until it exits. The
    # process-group kill must take down both so the run returns promptly.
    code = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); time.sleep(30)"
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command(code))
    feature = _make_feature(workspace)

    start = time.monotonic()
    result = asyncio.run(run_feature_isolated(feature, str(workspace / "runs"), timeout_s=0.4))

    assert time.monotonic() - start < 10  # grandchild died with the group; pipes reaped immediately
    assert result.ok is False and result.exit_code is None
    assert result.error is not None and "Timed out" in result.error


def test_env_extra_reserved_key_collision_warns_and_override_wins(workspace, monkeypatch, caplog) -> None:
    monkeypatch.setattr(run_manager_module, "_subprocess_command", lambda ws: _command("import os; print(os.environ.get('HERCULES_HISTORY_DB', 'unset'))"))
    monkeypatch.delenv("HERCULES_HISTORY_DB", raising=False)
    monkeypatch.setattr("testzeus_hercules.reporting.run_db.RunHistory._default_db_url", staticmethod(lambda: f"sqlite:///{workspace / 'host_history.db'}"))
    feature = _make_feature(workspace)

    with caplog.at_level(logging.WARNING, logger="testzeus_hercules.orchestrator.run_manager"):
        result = asyncio.run(run_feature_isolated(feature, str(workspace / "runs"), env_extra={"HERCULES_HISTORY_DB": "sqlite:///custom/history.db"}))

    assert result.ok is True
    assert result.stdout_tail.strip() == "sqlite:///custom/history.db"  # operator override still honored
    assert any("HERCULES_HISTORY_DB" in record.message for record in caplog.records if record.levelno == logging.WARNING)


def test_redact_cleaned_workspace_paths_blanks_only_missing_dirs(tmp_path) -> None:
    live_ws = tmp_path / "live"
    live_ws.mkdir()
    results = [
        FeatureResult(scenario="cleaned", feature_path="f.feature", workspace=str(tmp_path / "gone"), ok=True, exit_code=0, junit_xml=str(tmp_path / "gone" / "r.xml")),
        FeatureResult(scenario="kept", feature_path="f.feature", workspace=str(live_ws), ok=True, exit_code=0, junit_xml=str(live_ws / "r.xml")),
        FeatureResult(scenario="failed", feature_path="f.feature", workspace=str(tmp_path / "failed_ws"), ok=False, exit_code=1, junit_xml=None),
    ]

    run_manager_module.redact_cleaned_workspace_paths(results)

    assert results[0].workspace == "" and results[0].junit_xml is None  # removed workspace → no dangling paths
    assert results[1].workspace == str(live_ws) and results[1].junit_xml == str(live_ws / "r.xml")  # kept workspace untouched
    assert results[2].workspace == str(tmp_path / "failed_ws")  # failures keep their real paths


# ── hardening round 3: junit-less runs must not vanish from the totals ───────


def test_fold_missing_results_into_totals_branches(tmp_path) -> None:
    # Mirror of suites._totals_from_results: junit-less runs count as
    # passed/errors/failed depending on the run outcome.
    live_junit = _write_xml(str(tmp_path / "live" / "r.xml"), MINIMAL_JUNIT)
    merged_xml = str(tmp_path / "merged" / "merged_result.xml")
    merged = {"total": 1, "passed": 1, "failed": 0, "errors": 0, "junit_xml": merged_xml}
    results = [
        _result_with_junit(live_junit, "counted"),  # junit on disk → already in merged
        FeatureResult(scenario="ok_no_junit", feature_path="f.feature", workspace="ws", ok=True, exit_code=0, junit_xml=None),
        FeatureResult(scenario="timeout", feature_path="f.feature", workspace="ws", ok=False, exit_code=None, error="Timed out"),
        FeatureResult(scenario="crashed", feature_path="f.feature", workspace="ws", ok=False, exit_code=1, error="exited 1"),
        FeatureResult(scenario="dangling", feature_path="f.feature", workspace="gone", ok=True, exit_code=0, junit_xml=str(tmp_path / "gone" / "r.xml")),
    ]

    totals = run_manager_module._fold_missing_results_into_totals(results, merged)

    assert totals == {"total": 5, "passed": 3, "failed": 1, "errors": 1, "junit_xml": merged_xml}


def _scripted_ok_and_timeout_results(workspace) -> list[FeatureResult]:
    junit = _write_xml(str(workspace / "ok_ws" / "ok_result.xml"), MINIMAL_JUNIT)
    return [
        FeatureResult(scenario="s0", feature_path="f.feature", workspace=str(workspace / "ok_ws"), ok=True, exit_code=0, junit_xml=junit),
        FeatureResult(scenario="s1", feature_path="f.feature", workspace=str(workspace / "to_ws"), ok=False, exit_code=None, error="Timed out after 0.4s; process killed."),
    ]


def test_run_feature_file_parallel_counts_timeout_without_junit(workspace, monkeypatch) -> None:
    # 1 passing scenario with junit + 1 timed-out scenario without: the merged
    # XML only holds the first, but the totals must count both — a timeout
    # must never report as a clean green run.
    scripted = _scripted_ok_and_timeout_results(workspace)

    async def _fake_scenarios(scenario_files, **kwargs):
        return scripted

    monkeypatch.setattr(run_manager_module, "run_scenarios_parallel", _fake_scenarios)
    feature = _make_feature(workspace, scenarios=2)

    summary = asyncio.run(run_feature_file_parallel(feature, max_parallel=2))

    assert summary["total"] == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 0
    assert summary["errors"] == 1
    # The merged junit reflects what exists on disk: only the one testcase.
    cases = parse_junit_testcases(summary["merged_xml"])
    assert [case["name"] for case in cases] == ["Scenario: demo"]
    # The summary still lists both scenarios; the timeout keeps its error.
    assert [entry["scenario"] for entry in summary["results"]] == ["s0", "s1"]
    assert summary["results"][0]["ok"] is True
    assert summary["results"][1]["ok"] is False and "Timed out" in summary["results"][1]["error"]


def test_main_exits_one_on_timeout_scenario_without_junit(workspace, monkeypatch, capsys) -> None:
    # CI must go red when a scenario times out: totals include the junit-less
    # run as an error, so the run-manager exits 1 (EXIT_CODE_COMPAT unset).
    monkeypatch.delenv("EXIT_CODE_COMPAT", raising=False)
    scripted = _scripted_ok_and_timeout_results(workspace)

    async def _fake_scenarios(scenario_files, **kwargs):
        return scripted

    monkeypatch.setattr(run_manager_module, "run_scenarios_parallel", _fake_scenarios)
    feature = _make_feature(workspace, scenarios=2)

    exit_code = main([feature])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "Totals: 2 total, 1 passed, 0 failed, 1 errors" in out
    assert "[error] s1" in out  # exit_code is None → reported as an error run


def test_main_exit_code_compat_still_zero_on_timeout_scenario(workspace, monkeypatch) -> None:
    monkeypatch.setenv("EXIT_CODE_COMPAT", "1")
    scripted = _scripted_ok_and_timeout_results(workspace)

    async def _fake_scenarios(scenario_files, **kwargs):
        return scripted

    monkeypatch.setattr(run_manager_module, "run_scenarios_parallel", _fake_scenarios)
    feature = _make_feature(workspace, scenarios=2)

    assert main([feature]) == 0  # the compat opt-out still forces exit 0
