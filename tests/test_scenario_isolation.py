"""Offline scenario-isolation tests for the ``__main__`` batch loop.

The per-scenario containment must hold for every failure class:
1. a runner whose ``start()`` raises (crash class) is recorded as a failed
   junit entry, the loop continues to the next scenario, and the crashed
   runner's ``shutdown()`` is attempted so its browser cannot leak into the
   next scenario;
2. a runner cancelled mid-run (``asyncio.CancelledError`` — a BaseException)
   is isolated the same way instead of killing the whole run;
3. a failing best-effort ``shutdown()`` must not break the loop either.

All collaborators are stubbed; no network, no LLM, no browser.
"""

from __future__ import annotations

import asyncio
import os

import pytest
import testzeus_hercules.__main__ as main_module
from testzeus_hercules.__main__ import sequential_process
from testzeus_hercules.reporting.bundle import parse_junit_testcases

FEATURE_TEXT = """Feature: Scenario Isolation

  Scenario: Placeholder
    Given the user is on the home page
"""


class _FakeConf:
    """Minimal stand-in for the global config used by __main__ and junit_helper."""

    def __init__(self, tmp_path) -> None:
        self.junit_base = tmp_path / "output"
        self.junit_base.mkdir(parents=True, exist_ok=True)
        input_dir = tmp_path / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        self.input_path = input_dir / "test.feature"
        self.input_path.write_text(FEATURE_TEXT, encoding="utf-8")
        self.tmp_dir = str(tmp_path)

    def get_dont_close_browser(self) -> bool:
        return False

    def get_input_gherkin_file_path(self) -> str:
        return str(self.input_path)

    def get_junit_xml_base_path(self) -> str:
        return str(self.junit_base)

    def get_proof_path(self, test_id=None) -> str:
        return os.path.join(self.tmp_dir, "proofs", test_id or "default")

    def get_source_log_folder_path(self, test_id=None) -> str:
        path = os.path.join(self.tmp_dir, "log_files", test_id or "default")
        os.makedirs(path, exist_ok=True)
        return path

    def get_token_verbose(self) -> bool:
        return False

    def get_mode(self) -> str:
        return "prod"

    def set_default_test_id(self, test_id: str = "default") -> None:
        return None


class _CrashingRunner:
    """Runner whose start() crashes with a regular exception."""

    instances: list["_CrashingRunner"] = []
    shutdown_error: Exception | None = None

    def __init__(self, stake_id: str, command: str, dont_terminate_browser_after_run: bool = False) -> None:
        self.stake_id = stake_id
        self.command = command
        self.result = None
        self.execution_time = 0.0
        self.browser_manager = None  # never got to initialize
        self.shutdown_calls = 0
        _CrashingRunner.instances.append(self)

    async def start(self) -> None:
        raise RuntimeError(f"runner exploded for {self.stake_id}")

    async def shutdown(self) -> None:
        self.shutdown_calls += 1
        if _CrashingRunner.shutdown_error is not None:
            raise _CrashingRunner.shutdown_error


class _CancelledRunner(_CrashingRunner):
    """Runner cancelled mid-run: start() raises asyncio.CancelledError."""

    async def start(self) -> None:
        raise asyncio.CancelledError()


def _install_two_scenario_run(monkeypatch, tmp_path, runner_class) -> _FakeConf:
    """Point sequential_process at two scenarios running through runner_class."""

    async def fake_process_feature_file(dont_append_header: bool = False):
        return [
            {
                "output_file": str(tmp_path / "gherkin_files" / "scenario_one.feature"),
                "feature": "Feature: Scenario Isolation",
                "scenario": "Scenario: First scenario",
            },
            {
                "output_file": str(tmp_path / "gherkin_files" / "scenario_two.feature"),
                "feature": "Feature: Scenario Isolation",
                "scenario": "Scenario: Second scenario",
            },
        ]

    async def fake_serialize_feature_file(file_path: str) -> str:
        return "command text"

    conf = _FakeConf(tmp_path)
    monkeypatch.setattr(main_module, "get_global_conf", lambda: conf)
    monkeypatch.setattr(main_module, "process_feature_file", fake_process_feature_file)
    monkeypatch.setattr(main_module, "serialize_feature_file", fake_serialize_feature_file)
    monkeypatch.setattr(main_module, "SingleCommandInputRunner", runner_class)
    # build_junit_xml / merge_junit_xml resolve the junit base path through this
    # module-level name, so patch it to the same fake conf.
    import testzeus_hercules.utils.junit_helper as junit_helper

    monkeypatch.setattr(junit_helper, "get_global_conf", lambda: conf)
    return conf


@pytest.fixture(autouse=True)
def _reset_runner_instances():
    _CrashingRunner.instances = []
    _CrashingRunner.shutdown_error = None
    yield
    _CrashingRunner.instances = []
    _CrashingRunner.shutdown_error = None


def test_crashing_scenario_is_isolated_and_shut_down(monkeypatch, tmp_path) -> None:
    _install_two_scenario_run(monkeypatch, tmp_path, _CrashingRunner)

    summary = asyncio.run(sequential_process())

    # Both scenarios were attempted instead of aborting at the first crash...
    assert len(_CrashingRunner.instances) == 2
    # ...each crashed runner had its shutdown attempted exactly once...
    assert [runner.shutdown_calls for runner in _CrashingRunner.instances] == [1, 1]
    # ...and both were recorded as failed junit entries in the merged report.
    assert summary["total"] == 2
    assert summary["failed"] == 2
    assert summary["passed"] == 0
    assert os.path.isfile(summary["junit_xml"])

    cases = parse_junit_testcases(summary["junit_xml"])
    assert [case["name"] for case in cases] == ["Scenario: First scenario", "Scenario: Second scenario"]
    assert all(case["status"] == "failed" for case in cases)
    assert any("runner exploded" in (case["failure_message"] or "") for case in cases)
    assert any("EXPECTED RESULT" in (case["failure_message"] or "") for case in cases)


def test_cancelled_scenario_is_isolated_not_fatal(monkeypatch, tmp_path) -> None:
    _install_two_scenario_run(monkeypatch, tmp_path, _CancelledRunner)

    summary = asyncio.run(sequential_process())

    # The cancelled first scenario must not kill the run: both ran, both are red.
    assert len(_CancelledRunner.instances) == 2
    assert [runner.shutdown_calls for runner in _CancelledRunner.instances] == [1, 1]
    assert summary["total"] == 2
    assert summary["failed"] == 2
    assert summary["passed"] == 0

    cases = parse_junit_testcases(summary["junit_xml"])
    assert all(case["status"] == "failed" for case in cases)
    assert any("CancelledError" in (case["failure_message"] or "") for case in cases)


def test_failing_best_effort_shutdown_does_not_break_the_loop(monkeypatch, tmp_path) -> None:
    _install_two_scenario_run(monkeypatch, tmp_path, _CrashingRunner)
    _CrashingRunner.shutdown_error = OSError("browser teardown exploded")

    summary = asyncio.run(sequential_process())

    # The shutdown failure was absorbed: the loop still ran both scenarios
    # and produced the full merged artifacts.
    assert len(_CrashingRunner.instances) == 2
    assert summary["total"] == 2
    assert summary["failed"] == 2
    assert os.path.isfile(summary["junit_xml"])
    assert summary["report_md"] is not None
    assert os.path.isfile(summary["report_md"])


def test_junit_build_failure_is_contained_as_red_entry(monkeypatch, tmp_path) -> None:
    """A junit-generation error for one scenario (e.g. an unserializable LLM
    response) must not kill the batch: the loop continues and a minimal red
    testcase entry records the scenario so reporting cannot hide the run."""

    _install_two_scenario_run(monkeypatch, tmp_path, _CrashingRunner)

    async def exploding_build_junit_xml(*args, **kwargs):
        raise ValueError("All strings must be XML compatible: Unicode or ASCII, no NULL bytes or control characters")

    monkeypatch.setattr(main_module, "build_junit_xml", exploding_build_junit_xml)

    summary = asyncio.run(sequential_process())

    # Both scenarios were still attempted...
    assert len(_CrashingRunner.instances) == 2
    # ...and both were reported — as red entries, never silently dropped.
    assert summary["total"] == 2
    assert summary["failed"] == 2
    assert summary["passed"] == 0
    assert os.path.isfile(summary["junit_xml"])

    cases = parse_junit_testcases(summary["junit_xml"])
    assert [case["name"] for case in cases] == ["Scenario: First scenario", "Scenario: Second scenario"]
    assert all(case["status"] == "failed" for case in cases)
    assert all("junit" in (case["failure_message"] or "").lower() for case in cases)


def test_failing_fallback_junit_build_does_not_kill_the_batch(monkeypatch, tmp_path) -> None:
    """Even the synthesized fallback entry can fail to build (e.g. ENOSPC);
    the batch must still complete: the other scenarios keep their reports and
    the run summary is produced instead of the whole run dying with zero
    merged output."""
    _install_two_scenario_run(monkeypatch, tmp_path, _CrashingRunner)

    real_build_junit_xml = main_module.build_junit_xml

    async def build_fails_for_first_scenario(json_data, execution_time, cost_metric, feature, scenario, **kwargs):
        if scenario == "Scenario: First scenario":
            raise ValueError("All strings must be XML compatible")
        return await real_build_junit_xml(json_data, execution_time, cost_metric, feature, scenario, **kwargs)

    async def exploding_build_fallback(feature, scenario, error):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(main_module, "build_junit_xml", build_fails_for_first_scenario)
    monkeypatch.setattr(main_module, "build_fallback_junit_xml", exploding_build_fallback)

    summary = asyncio.run(sequential_process())

    # Both scenarios were still attempted and the run completed.
    assert len(_CrashingRunner.instances) == 2
    # The first scenario has no per-file report (both of its junit paths
    # failed), but the second scenario was still reported through the normal
    # path and the merged artifacts + run summary are intact.
    assert summary["total"] == 1
    assert summary["failed"] == 1
    assert summary["passed"] == 0
    assert os.path.isfile(summary["junit_xml"])
    cases = parse_junit_testcases(summary["junit_xml"])
    assert [case["name"] for case in cases] == ["Scenario: Second scenario"]
    assert cases[0]["status"] == "failed"
