"""Offline tests for CI exit-code wiring in testzeus_hercules.__main__.

compute_exit_code is pure; sequential_process is exercised end-to-end with the
runner mocked out while the JUnit XML build/merge, HTML conversion, markdown
report and bundle pipeline run for real against a tmp directory.
No network, no LLM, no browser.
"""

import asyncio
import json
import os

import pytest
import testzeus_hercules.__main__ as main_module
from testzeus_hercules.__main__ import compute_exit_code, sequential_process

PLANNER_SUMMARY = json.dumps(
    {
        "terminate": "yes",
        "is_passed": True,
        "final_response": "ok",
        "is_assert": True,
        "assert_summary": "ok",
    }
)

# is_assert + is_passed False → the JUnit testcase gets a Failure → summary["failed"] == 1.
FAILED_PLANNER_SUMMARY = json.dumps(
    {
        "terminate": "yes",
        "is_passed": False,
        "final_response": "expected status code 200 but got 500",
        "is_assert": True,
        "assert_summary": "expected status code 200 but got 500",
    }
)

FEATURE_TEXT = """Feature: Offline Demo

  Scenario: Passing scenario
    Given the user is on the home page
"""


class _FakeResult:
    def __init__(self, summary: str) -> None:
        self.summary = summary
        self.cost = {}
        self.terminate = "yes"


class _FakeBrowserManager:
    def __init__(self, stake_id: str, tmp_dir: str) -> None:
        self.stake_id = stake_id
        self._screenshots_dir = os.path.join(tmp_dir, "screenshots")
        self.request_response_log_file = ""

    def get_latest_video_path(self) -> str:
        return ""


def _make_fake_runner(tmp_dir: str, planner_summary: str = PLANNER_SUMMARY):
    class _FakeRunner:
        def __init__(self, stake_id: str, command: str, dont_terminate_browser_after_run: bool = False) -> None:
            self.stake_id = stake_id
            self.command = command
            self.result = _FakeResult(planner_summary)
            self.execution_time = 1.23
            self.browser_manager = _FakeBrowserManager(stake_id, tmp_dir)

        async def start(self) -> None:
            return None

    return _FakeRunner


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
        return os.path.join(self.tmp_dir, "log_files", test_id or "default")

    def get_token_verbose(self) -> bool:
        return False

    def get_mode(self) -> str:
        return "prod"

    def set_default_test_id(self, test_id: str = "default") -> None:
        return None

    # Seams a_main() consults before dispatching to sequential_process:
    # the fake env always takes the plain single-test path.

    def get_guided_test_description(self) -> str | None:
        return None

    def should_run_guided(self) -> bool:
        return False

    def should_dry_run(self) -> bool:
        return False

    def should_execute_bulk(self) -> bool:
        return False

    def get_project_source_root(self) -> str:
        return self.tmp_dir


def _install_fake_run(monkeypatch, tmp_path, planner_summary: str) -> _FakeConf:
    conf = _FakeConf(tmp_path)

    async def fake_process_feature_file(dont_append_header: bool = False):
        return [
            {
                "output_file": str(tmp_path / "gherkin_files" / "passing_scenario.feature"),
                "feature": "Feature: Offline Demo",
                "scenario": "Scenario: Passing scenario",
            }
        ]

    async def fake_serialize_feature_file(file_path: str) -> str:
        return "command text"

    monkeypatch.setattr(main_module, "get_global_conf", lambda: conf)
    monkeypatch.setattr(main_module, "process_feature_file", fake_process_feature_file)
    monkeypatch.setattr(main_module, "serialize_feature_file", fake_serialize_feature_file)
    monkeypatch.setattr(main_module, "SingleCommandInputRunner", _make_fake_runner(str(tmp_path), planner_summary))
    # build_junit_xml / merge_junit_xml resolve the junit base path through this
    # module-level name, so patch it to the same fake conf.
    import testzeus_hercules.utils.junit_helper as junit_helper

    monkeypatch.setattr(junit_helper, "get_global_conf", lambda: conf)
    return conf


@pytest.fixture()
def fake_run_env(monkeypatch, tmp_path):
    return _install_fake_run(monkeypatch, tmp_path, PLANNER_SUMMARY)


@pytest.fixture()
def fake_failed_run_env(monkeypatch, tmp_path):
    return _install_fake_run(monkeypatch, tmp_path, FAILED_PLANNER_SUMMARY)


def test_compute_exit_code_mixed_summaries() -> None:
    summaries = [
        {"total": 2, "passed": 2, "failed": 0, "errors": 0},
        {"total": 1, "passed": 0, "failed": 1, "errors": 0},
        {"total": 1, "passed": 0, "failed": 0, "errors": 1},
    ]
    assert compute_exit_code(summaries) == 1


def test_compute_exit_code_all_passed() -> None:
    summaries = [{"total": 3, "passed": 3, "failed": 0, "errors": 0}]
    assert compute_exit_code(summaries) == 0


def test_compute_exit_code_empty_summaries() -> None:
    assert compute_exit_code([]) == 0
    assert compute_exit_code(None) == 0  # type: ignore[arg-type]


def test_sequential_process_returns_populated_summary(fake_run_env) -> None:
    summary = asyncio.run(sequential_process())

    assert summary["total"] == 1
    assert summary["passed"] == 1
    assert summary["failed"] == 0
    assert summary["errors"] == 0
    assert summary["feature_file"] == fake_run_env.get_input_gherkin_file_path()

    assert os.path.isfile(summary["junit_xml"])
    assert os.path.isfile(summary["html"])

    # Passing run: markdown report written next to the junit xml, no failure bundles.
    assert summary["report_md"] is not None
    assert os.path.isfile(summary["report_md"])
    assert os.path.basename(summary["report_md"]) == "Hercules_Test_Report.md"
    assert summary["bundles"] == []

    with open(summary["junit_xml"], encoding="utf-8") as handle:
        assert "Scenario: Passing scenario" in handle.read()


# --- main() exit-code wiring --------------------------------------------------
# main() runs a_main() in-process (fake runner above provides the run) and then
# funnels the summaries through _finalize_exit_code: SystemExit(1) on failures,
# legacy all-zero exit when EXIT_CODE_COMPAT=1.


def test_main_raises_system_exit_1_when_run_failed(fake_failed_run_env, monkeypatch) -> None:
    monkeypatch.delenv("EXIT_CODE_COMPAT", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        main_module.main()

    assert excinfo.value.code == 1


def test_main_exit_code_compat_exits_zero_despite_failures(fake_failed_run_env, monkeypatch) -> None:
    monkeypatch.setenv("EXIT_CODE_COMPAT", "1")

    assert main_module.main() is None  # no SystemExit: process would exit 0


def test_main_passing_run_exits_zero(fake_run_env, monkeypatch) -> None:
    monkeypatch.delenv("EXIT_CODE_COMPAT", raising=False)

    assert main_module.main() is None


def test_main_failed_run_writes_failed_junit_summary(fake_failed_run_env) -> None:
    # The failing summary flows all the way into the merged JUnit XML before the
    # exit code is computed, so the artifact records the failure too.
    junit_dir = fake_failed_run_env.junit_base
    with pytest.raises(SystemExit):
        main_module.main()

    junit_files = list(junit_dir.glob("*_result.xml"))
    assert junit_files
    content = junit_files[0].read_text(encoding="utf-8")
    assert "<failure" in content
    assert "expected status code 200 but got 500" in content


# --- best-effort HTML conversion (junit2html) ----------------------------------


def _raise_no_space(_args: list[str]) -> None:
    raise OSError(28, "No space left on device")


def test_sequential_process_survives_html_conversion_failure(fake_run_env, monkeypatch) -> None:
    monkeypatch.setattr(main_module, "prepare_html", _raise_no_space)

    summary = asyncio.run(sequential_process())

    # The failed step degrades to None instead of crashing the run...
    assert summary["html"] is None
    # ...and every other post-test step still ran.
    assert os.path.isfile(summary["junit_xml"])
    assert summary["report_md"] is not None
    assert os.path.isfile(summary["report_md"])
    assert isinstance(summary["bundles"], list)


def test_main_exit_code_logic_survives_html_conversion_failure(fake_run_env, monkeypatch) -> None:
    monkeypatch.delenv("EXIT_CODE_COMPAT", raising=False)
    monkeypatch.setattr(main_module, "prepare_html", _raise_no_space)

    # Passing run + html failure: _finalize_exit_code still runs and exits 0.
    assert main_module.main() is None


def test_main_failed_run_still_exits_1_when_html_conversion_fails(fake_failed_run_env, monkeypatch) -> None:
    monkeypatch.delenv("EXIT_CODE_COMPAT", raising=False)
    monkeypatch.setattr(main_module, "prepare_html", _raise_no_space)

    with pytest.raises(SystemExit) as excinfo:
        main_module.main()

    assert excinfo.value.code == 1


# --- colliding run output dirs --------------------------------------------------


def test_sequential_process_disambiguates_existing_output_dir(fake_run_env) -> None:
    output_dir = fake_run_env.junit_base
    # Simulate a same-second run that already populated the shared output dir.
    (output_dir / "stale_result.xml").write_text("<testsuites/>", encoding="utf-8")

    summary = asyncio.run(sequential_process())

    used_dir = os.path.dirname(os.path.abspath(summary["junit_xml"]))
    assert used_dir != str(output_dir)
    assert used_dir.startswith(f"{output_dir}_")
    # New artifacts land in the fresh dir, not on top of the stale ones.
    assert os.path.isfile(summary["junit_xml"])
    assert os.path.isfile(summary["report_md"])
    assert not (output_dir / "Hercules_Test_Report.md").exists()
