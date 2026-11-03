"""Permanent offline end-to-end integration tests for the whole Hercules pipeline.

Unlike the per-module suites, these tests wire the REAL modules together across
package boundaries and stub only the expensive edges (LLM responses via a fake
LLM, the browser/agent runner via a fake SingleCommandInputRunner — the same
stubbing style as tests/test_exit_code.py and tests/test_core_loop_safety.py).
Everything runs in-process against a tmp PROJECT_SOURCE_ROOT: no network, no
real LLM, no real browser, nothing written into the repo's opt/ or .hercules/.

Covered scenarios:
1. test_full_pipeline_prd_to_history — PRD -> normalized PRD -> test plan ->
   Gherkin -> sequential execution (1 PASS / 1 FAIL) -> merged JUnit + HTML +
   markdown report + failure bundle -> run-history DB -> dashboard routes.
2. test_full_pipeline_mcp_path_isolated_sessions — the MCP tool chain
   (configure_session -> standardized PRD -> test plan -> generate_and_execute)
   driven for two sessions with different PRDs; per-session artifacts must
   never leak across sessions and a broken run must fail cleanly.
3. test_parallel_runs_share_history_db — two concurrent record_run_summary
   calls sharing one RunHistory instance (WAL + batching under concurrency).
4. test_recovery_from_interrupted_run — recording a run whose row already
   exists (crash between junit write and history record) replaces it
   idempotently instead of leaving phantom duplicates.
"""

import asyncio
import json
import os
import re
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest
import testzeus_hercules.__main__ as main_module
import testzeus_hercules.mcp_server as mcp_server
from starlette.testclient import TestClient
from testzeus_hercules.__main__ import sequential_process
from testzeus_hercules.config import SingletonConfigManager, set_global_conf
from testzeus_hercules.dashboard.app import create_app
from testzeus_hercules.intelligence.normalized_prd import prd_from_json
from testzeus_hercules.intelligence.prd_parser import build_normalized_prd
from testzeus_hercules.intelligence.test_plan import (
    compile_plan_to_gherkin,
    generate_test_plan,
    plan_from_json,
)
from testzeus_hercules.orchestrator.session import SessionManager
from testzeus_hercules.reporting import run_db
from testzeus_hercules.reporting.bundle import parse_junit_testcases, sanitize_name
from testzeus_hercules.reporting.run_db import RunHistory, record_run_summary
from testzeus_hercules.utils.gherkin_helper import split_feature_file

DB_FILENAME = "hercules_history.db"

PASSING_PLANNER_SUMMARY = json.dumps(
    {
        "terminate": "yes",
        "is_passed": True,
        "final_response": "ok",
        "is_assert": True,
        "assert_summary": "ok",
    }
)

FAILURE_MESSAGE = "expected status code 200 but got 500"
FAILED_PLANNER_SUMMARY = json.dumps(
    {
        "terminate": "yes",
        "is_passed": False,
        "final_response": "the checkout endpoint returned an error",
        "is_assert": True,
        "assert_summary": FAILURE_MESSAGE,
    }
)

PRD_X_TEXT = "# Demo Project PRD\n\n## User login\n\nThe system shall let registered users log in with email and password.\n\n## Order checkout\n\nThe system shall let logged-in users check out a cart and receive an order confirmation.\n"
PRD_Y_TEXT = "# Other Project PRD\n\n## Export report\n\nThe system shall let users export their billing report as CSV.\n"

SMOKE_FEATURE = "Feature: MCP smoke\n\n  Scenario: MCP smoke passes\n    Given the app is up\n"


class FakeIntelligenceLLM:
    """Offline LLM double for the PRD/test-plan intelligence pipeline.

    Inspects each prompt: chunk-extraction prompts get a JSON array of
    requirements, summary prompts get a project JSON object, and test-plan
    prompts get one valid test case per ``REQ-`` id mentioned in the prompt.
    """

    def __init__(self, requirement_titles: list[str]) -> None:
        self.requirement_titles = list(requirement_titles)
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> SimpleNamespace:
        self.prompts.append(prompt)
        if "<chunk>" in prompt:
            payload = [{"title": title, "description": f"{title} description", "acceptance_criteria": [f"{title} works"]} for title in self.requirement_titles]
        elif '"project_name"' in prompt:
            payload = {"project_name": "Demo Project", "summary": "offline demo project"}
        else:  # test-plan prompt: one case per requirement found in the prompt text
            payload = [
                {
                    "requirement_id": requirement_id,
                    "title": f"{requirement_id} happy path",
                    "description": f"verifies {requirement_id}",
                    "category": "functional",
                    "priority": "high",
                    "tags": ["frontend"],
                    "steps": [{"type": "action", "description": f"open the {requirement_id} screen"}, {"type": "assertion", "description": f"{requirement_id} behaves as specified"}],
                    "data_refs": [],
                }
                for requirement_id in sorted(set(re.findall(r"REQ-\d+", prompt)))
            ]
        return SimpleNamespace(content=json.dumps(payload))


class _FakeResult:
    def __init__(self, summary: str) -> None:
        self.summary = summary
        self.cost = {}
        self.terminate = "yes"


class _FakeBrowserManager:
    """Browser manager stub whose evidence paths actually exist on disk."""

    def __init__(self, stake_id: str, evidence: dict[str, dict[str, str]]) -> None:
        paths = evidence.get(stake_id, {"video": "", "screenshots": "", "network": ""})
        self.stake_id = stake_id
        self._screenshots_dir = paths["screenshots"]
        self.request_response_log_file = paths["network"]
        self._video_path = paths["video"]

    def get_latest_video_path(self) -> str:
        return self._video_path


def _make_fake_runner(results_by_stake: dict[str, str], evidence: dict[str, dict[str, str]]):
    class _FakeRunner:
        def __init__(self, stake_id: str, command: str, dont_terminate_browser_after_run: bool = False) -> None:
            self.stake_id = stake_id
            self.command = command
            self.result = _FakeResult(results_by_stake.get(stake_id, PASSING_PLANNER_SUMMARY))
            self.execution_time = 1.23
            self.browser_manager = _FakeBrowserManager(stake_id, evidence)

        async def start(self) -> None:
            return None

    return _FakeRunner


@pytest.fixture(autouse=True)
def _enable_opt_in_reporting(monkeypatch) -> None:
    """These tests cover the opt-in reporting / history / bundle / classification pipeline."""
    for flag in ("ENABLE_RUN_REPORTING", "ENABLE_FAILURE_BUNDLES", "ENABLE_RUN_HISTORY", "ENABLE_FAILURE_CLASSIFICATION", "ENABLE_SELECTOR_RECORDING"):
        monkeypatch.setenv(flag, "true")


@pytest.fixture()
def hermetic_root(monkeypatch, tmp_path):
    """Rebuild the global config against a tmp PROJECT_SOURCE_ROOT.

    The real config singleton is used (so junit_helper, gherkin_helper, run_db
    and __main__ all resolve hermetic paths), the run-history DB is pointed at
    a tmp file, and both are restored after the test.
    """
    monkeypatch.setenv("PROJECT_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setenv("HERCULES_HISTORY_DB", str(tmp_path / DB_FILENAME))
    monkeypatch.delenv("EXIT_CODE_COMPAT", raising=False)
    run_db.reset_default_history()
    monkeypatch.setattr(SingletonConfigManager, "_instance", None)
    conf = set_global_conf({"MODE": "prod"}, override=True)
    yield conf
    run_db.reset_default_history()


def _seed_evidence(conf, scenario_titles: list[str]) -> dict[str, dict[str, str]]:
    """Create on-disk proof/log files for each scenario and map stake_id -> paths."""
    evidence: dict[str, dict[str, str]] = {}
    for title in scenario_titles:
        stake_id = sanitize_name(title)
        proofs = Path(conf.get_proof_path(stake_id))
        logs = Path(conf.get_source_log_folder_path(stake_id))
        video = proofs / "recording.webm"
        video.write_bytes(b"\x1a\x45\xdf\xa3fake-webm-payload")
        screenshots = proofs / "screenshots"
        screenshots.mkdir(parents=True, exist_ok=True)
        (screenshots / "step_01.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-png")
        network = proofs / "network_logs.json"
        network.write_text('{"method": "GET", "url": "https://example.test/api", "status": 200, "duration": 12.5}\n', encoding="utf-8")
        (logs / "agent_inner_thoughts.json").write_text("planner thought: open the page\n", encoding="utf-8")
        evidence[stake_id] = {"video": str(video), "screenshots": str(screenshots), "network": str(network), "logs": str(logs)}
    return evidence


def _assert_report_evidence_links_exist(report_path: str) -> None:
    """Every file:// evidence link in the markdown report must resolve on disk."""
    report = Path(report_path).read_text(encoding="utf-8")
    links = re.findall(r"\]\(file://([^)]+)\)", report)
    assert links, "the report should carry file:// evidence links"
    for link in links:
        target = urllib.parse.unquote(link)
        assert os.path.exists(target), f"evidence link target missing on disk: {target}"
    assert any(os.path.isfile(urllib.parse.unquote(link)) for link in links), "at least one evidence link should be a real file"


# ── scenario 1: PRD -> plan -> gherkin -> run -> reports -> history -> dashboard ─


def test_full_pipeline_prd_to_history(hermetic_root, tmp_path, monkeypatch):
    conf = hermetic_root

    # Intelligence pipeline: PRD file -> normalized PRD (2 requirements) -> plan -> Gherkin.
    prd_path = tmp_path / "prd.md"
    prd_path.write_text(PRD_X_TEXT, encoding="utf-8")

    async def run_intelligence():
        prd = await build_normalized_prd(str(prd_path), llm=FakeIntelligenceLLM(["User login", "Order checkout"]))
        plan = await generate_test_plan(prd, llm=FakeIntelligenceLLM([]), cases_per_requirement=1)
        return prd, plan

    prd, plan = asyncio.run(run_intelligence())
    assert [requirement.id for requirement in prd.requirements] == ["REQ-001", "REQ-002"]
    assert [case.id for case in plan.test_cases] == ["TC001", "TC002"]

    feature_text = compile_plan_to_gherkin(plan)
    input_path = conf.get_input_gherkin_file_path()
    Path(input_path).write_text(feature_text, encoding="utf-8")

    # The compiled feature must be parseable by the real splitter: 2 scenarios.
    scenarios = asyncio.run(split_feature_file(input_path, str(tmp_path / "split_check")))
    scenario_titles = [scenario["scenario"] for scenario in scenarios]
    assert scenario_titles == ["TC001 REQ-001 happy path", "TC002 REQ-002 happy path"]

    # Execution: stub only the browser/agent runner; one scenario PASS, one FAIL.
    evidence = _seed_evidence(conf, scenario_titles)
    failing_stake = sanitize_name(scenario_titles[1])
    results_by_stake = {failing_stake: FAILED_PLANNER_SUMMARY}
    monkeypatch.setattr(main_module, "SingleCommandInputRunner", _make_fake_runner(results_by_stake, evidence))

    summary = asyncio.run(sequential_process())

    assert summary["total"] == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert summary["errors"] == 0
    assert summary["run_id"]

    # Merged JUnit: 2 cases, exactly 1 failure carrying the assert summary.
    cases = parse_junit_testcases(summary["junit_xml"])
    assert [case["name"] for case in cases] == scenario_titles
    assert [case["status"] for case in cases] == ["passed", "failed"]
    failed_case = next(case for case in cases if case["status"] == "failed")
    assert FAILURE_MESSAGE in (failed_case["failure_message"] or "")

    assert os.path.isfile(summary["html"])
    assert os.path.isfile(summary["report_md"]) and os.path.basename(summary["report_md"]) == "Hercules_Test_Report.md"

    # Markdown report: both case names, the failure message inside a code fence,
    # and file:// evidence links that all resolve to existing paths.
    report = Path(summary["report_md"]).read_text(encoding="utf-8")
    for name in scenario_titles:
        assert name in report
    failure_lines = report.split("**Failure message:**", 1)[1].strip().splitlines()
    assert failure_lines[0].startswith("```"), "failure message must open a code fence"
    closing_index = next(index for index in range(1, len(failure_lines)) if failure_lines[index].startswith("```"))
    assert FAILURE_MESSAGE in "\n".join(failure_lines[1:closing_index])
    _assert_report_evidence_links_exist(summary["report_md"])

    # Failure bundles: exactly one, for the failed case only.
    bundles = summary["bundles"]
    assert len(bundles) == 1
    assert os.path.basename(bundles[0]) == sanitize_name(scenario_titles[1])
    assert os.path.isdir(bundles[0])
    assert not (Path(bundles[0]).parent / sanitize_name(scenario_titles[0])).exists()

    # The auto-classifier recorded the failure as a contract violation.
    assert summary["classifications"].get("contract_violation") == 1

    # Run history (fresh RunHistory instance proves durable on-disk records).
    history = RunHistory(db_url=f"sqlite:///{tmp_path / DB_FILENAME}")
    run = history.get_run(summary["run_id"])
    assert run["status"] == "failed"
    assert run["total"] == 2 and run["passed"] == 1 and run["failed"] == 1
    for key in ("junit_xml", "html", "report_md", "feature_file"):
        assert run[key] and os.path.exists(run[key]), f"run.{key} must point at an existing file"

    assert len(run["cases"]) == 2
    cases_by_name = {case["case_key"]: case for case in run["cases"]}
    passed_row = cases_by_name[scenario_titles[0]]
    failed_row = cases_by_name[scenario_titles[1]]
    assert passed_row["status"] == "passed" and failed_row["status"] == "failed"
    assert FAILURE_MESSAGE in (failed_row["failure_message"] or "")
    assert passed_row["bundle_dir"] is None
    assert failed_row["bundle_dir"] and os.path.isdir(failed_row["bundle_dir"])
    assert failed_row["bundle_dir"] == bundles[0]
    assert any(event["kind"] == "classification" for event in failed_row["healing_events"])

    for case in run["cases"]:
        assert case["artifacts"], f"case {case['case_key']} should carry recorded artifacts"
        for artifact in case["artifacts"]:
            assert os.path.exists(artifact["path"]), f"artifact path missing on disk: {artifact['path']}"
    assert any(artifact["kind"] == "report" for artifact in run["artifacts"])

    # Dashboard over the same DB: runs list, run drill-down, :path case route, trend.
    app = create_app(db_url=f"sqlite:///{tmp_path / DB_FILENAME}", project_root=str(tmp_path))
    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert summary["run_id"] in home.text

        run_page = client.get(f"/run/{urllib.parse.quote(summary['run_id'], safe='')}")
        assert run_page.status_code == 200
        assert scenario_titles[1] in run_page.text

        case_url = f"/case/{urllib.parse.quote(summary['run_id'], safe='')}/{urllib.parse.quote(scenario_titles[1], safe='')}"
        case_page = client.get(case_url)  # exercises the {case_key:path} route with spaces in the name
        assert case_page.status_code == 200
        assert scenario_titles[1] in case_page.text
        assert FAILURE_MESSAGE in case_page.text

        trend = client.get("/trend")
        assert trend.status_code == 200
        assert "<svg" in trend.text


# ── scenario 2: the MCP tool chain with isolated sessions ───────────────────────


def test_full_pipeline_mcp_path_isolated_sessions(hermetic_root, tmp_path, monkeypatch):
    monkeypatch.setenv("TESTZEUS_ROOT", str(tmp_path))
    manager = SessionManager()
    monkeypatch.setattr(mcp_server, "get_session_manager", lambda: manager)

    real_build = mcp_server.build_normalized_prd
    real_plan = mcp_server.generate_test_plan

    async def fake_build(source: str, llm=None):
        titles = ["User login", "Order checkout"] if source.endswith("prd_x.md") else ["Export report"]
        return await real_build(source, llm=FakeIntelligenceLLM(titles))

    async def fake_plan(prd, llm=None, cases_per_requirement=3, include_categories=None):
        return await real_plan(prd, llm=FakeIntelligenceLLM([]), cases_per_requirement=1, include_categories=include_categories)

    monkeypatch.setattr(mcp_server, "build_normalized_prd", fake_build)
    monkeypatch.setattr(mcp_server, "generate_test_plan", fake_plan)
    # The MCP execution path lazy-imports __main__.sequential_process, whose
    # runner symbol resolves at call time — stub the browser/agent layer here.
    monkeypatch.setattr(main_module, "SingleCommandInputRunner", _make_fake_runner({}, {}))

    prd_x = tmp_path / "prd_x.md"
    prd_x.write_text(PRD_X_TEXT, encoding="utf-8")
    prd_y = tmp_path / "prd_y.md"
    prd_y.write_text(PRD_Y_TEXT, encoding="utf-8")

    async def drive_tools():
        session_a = (await mcp_server.hercules_configure_session(app_url=""))["session_id"]
        session_b = (await mcp_server.hercules_configure_session(app_url=""))["session_id"]
        prd_a = await mcp_server.hercules_generate_standardized_prd(session_id=session_a, prd_path=str(prd_x))
        prd_b = await mcp_server.hercules_generate_standardized_prd(session_id=session_b, prd_path=str(prd_y))
        plan_a = await mcp_server.hercules_generate_test_plan(session_id=session_a)
        plan_b = await mcp_server.hercules_generate_test_plan(session_id=session_b)
        executed = await mcp_server.hercules_generate_and_execute(session_id=session_a, gherkin=SMOKE_FEATURE)
        broken = await mcp_server.hercules_generate_and_execute(session_id=session_a, gherkin="Feature: Broken no scenarios here")
        return session_a, session_b, prd_a, prd_b, plan_a, plan_b, executed, broken

    session_a, session_b, prd_a, prd_b, plan_a, plan_b, executed, broken = asyncio.run(drive_tools())

    for result in (prd_a, prd_b, plan_a, plan_b, executed):
        assert "error" not in result, result

    # Each session normalized its OWN PRD into its own artifact directory.
    assert prd_a["requirement_count"] == 2 and prd_b["requirement_count"] == 1
    # Tool results carry display paths relative to the Hercules root (TESTZEUS_ROOT).
    assert prd_a["standard_prd_path"] == os.path.join(".hercules", session_a, "standard_prd.json")
    assert prd_b["standard_prd_path"] == os.path.join(".hercules", session_b, "standard_prd.json")
    assert prd_from_json(str(tmp_path / prd_a["standard_prd_path"])).requirements[0].title == "User login"
    assert prd_from_json(str(tmp_path / prd_b["standard_prd_path"])).requirements[0].title == "Export report"
    assert not (tmp_path / ".hercules" / "standard_prd.json").exists(), "no global artifact file may exist"

    # Session A's plan references X's requirements (2 cases over REQ-001/002);
    # session B's plan references only Y's single requirement — no cross-use.
    assert len(plan_a["test_cases"]) == 2 and len(plan_b["test_cases"]) == 1
    assert [row["id"] for row in plan_a["coverage"]["requirements"]] == ["REQ-001", "REQ-002"]
    assert [row["id"] for row in plan_b["coverage"]["requirements"]] == ["REQ-001"]
    assert [row["description"] for row in plan_a["coverage"]["requirements"]] == ["User login description", "Order checkout description"]
    assert plan_b["coverage"]["requirements"][0]["description"] == "Export report description"
    plan_a_model = plan_from_json(str(tmp_path / plan_a["test_plan_path"]))
    assert {requirement_id for case in plan_a_model.test_cases for requirement_id in case.requirement_ids} == {"REQ-001", "REQ-002"}
    assert not (tmp_path / ".hercules" / "test_plan.json").exists()

    # A tiny feature executed through the real runner bridge: full summary dict
    # plus a recorded history row.
    assert executed["success"] is True
    assert executed["source"] == "gherkin"
    assert executed["feature_file"].startswith(os.path.join("opt", "input", "hercules_mcp_run_"))
    run_summary = executed["summaries"][0]
    assert run_summary["total"] == 1 and run_summary["passed"] == 1 and run_summary["failed"] == 0

    history = RunHistory(db_url=f"sqlite:///{tmp_path / DB_FILENAME}")
    run = history.get_run(run_summary["run_id"])
    assert run["status"] == "passed"
    assert [case["case_key"] for case in run["cases"]] == ["MCP smoke passes"]

    # Invalid Gherkin (no Scenario block) fails cleanly: {"error": ...} result,
    # no crash, and nothing appended to the session's run summaries.
    assert broken["success"] is False
    assert "no scenarios" in broken["error"]
    assert len(manager.get_session(session_a).last_run_summaries) == 1


# ── scenario 3: concurrent record_run_summary calls share one history DB ────────


def _write_single_case_junit(junit_path: Path, case_name: str, failure_message: str | None) -> None:
    failure_xml = f'<failure message="{escape(failure_message)}" type="AssertionError"></failure>' if failure_message else ""
    junit_path.parent.mkdir(parents=True, exist_ok=True)
    junit_path.write_text(
        f"""<testsuites>
  <testsuite name="Feature: Parallel" tests="1" failures="{1 if failure_message else 0}" skipped="0" time="1.0">
    <testcase name="{escape(case_name)}" classname="Feature: Parallel" time="1.0">
      {failure_xml}
      <system-out>Final Response: done</system-out>
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )


def test_parallel_runs_share_history_db(tmp_path):
    db = RunHistory(db_url=f"sqlite:///{tmp_path / DB_FILENAME}")

    specs = [
        ("run_parallel_a", "Scenario: Parallel pass", "passed", None),
        ("run_parallel_b", "Scenario: Parallel fail", "failed", "expected 3 bikes, got 2"),
    ]
    summaries = []
    for run_id, case_name, status, failure_message in specs:
        junit_path = tmp_path / run_id / "result.xml"
        _write_single_case_junit(junit_path, case_name, failure_message)
        summaries.append(
            {
                "total": 1,
                "passed": 1 if status == "passed" else 0,
                "failed": 1 if status == "failed" else 0,
                "errors": 0,
                "junit_xml": str(junit_path),
                "status": status,
            }
        )

    async def record_both():
        return await asyncio.gather(
            asyncio.to_thread(record_run_summary, summaries[0], specs[0][0], db),
            asyncio.to_thread(record_run_summary, summaries[1], specs[1][0], db),
        )

    run_ids = asyncio.run(record_both())
    assert run_ids == ["run_parallel_a", "run_parallel_b"]

    runs = db.list_runs(limit=10)
    assert {entry["run_id"] for entry in runs} == {"run_parallel_a", "run_parallel_b"}
    for entry in runs:
        detail = db.get_run(entry["run_id"])
        assert len(detail["cases"]) == 1
    assert db.get_run("run_parallel_a")["cases"][0]["status"] == "passed"
    assert db.get_run("run_parallel_b")["cases"][0]["status"] == "failed"

    trend = db.get_trend()
    assert len(trend["runs"]) == 2
    assert trend["aggregate"]["avg_pass_rate"] == pytest.approx(0.5)


# ── scenario 4: recovery from an interrupted run ─────────────────────────────────


def test_recovery_from_interrupted_run(tmp_path):
    db = RunHistory(db_url=f"sqlite:///{tmp_path / DB_FILENAME}")
    junit_path = tmp_path / "output" / "run_interrupted" / "result.xml"
    _write_two_case_junit(junit_path)

    summary = {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "errors": 0,
        "junit_xml": str(junit_path),
        "status": "failed",
    }

    # Crash simulation: the run row exists (the process died between the junit
    # write and the history record), but no cases/status were ever written.
    db.start_run("run_interrupted")
    assert db.has_run("run_interrupted")

    # Recovery: re-record the SAME run_id — the stale row must be replaced.
    run_id = record_run_summary(summary, run_id="run_interrupted", db=db)
    assert run_id == "run_interrupted"

    run = db.get_run("run_interrupted")
    assert run["status"] == "failed"
    assert len(run["cases"]) == 2

    # Recording again stays idempotent: no phantom "<id>_1" duplicates, no doubled cases.
    record_run_summary(summary, run_id="run_interrupted", db=db)

    matching = [entry for entry in db.list_runs(limit=50) if entry["run_id"].startswith("run_interrupted")]
    assert len(matching) == 1
    assert db.get_run("run_interrupted")["cases"] == run["cases"]  # same two rows, not four
    assert len(db.get_trend()["runs"]) == 1


def _write_two_case_junit(junit_path: Path) -> None:
    junit_path.parent.mkdir(parents=True, exist_ok=True)
    junit_path.write_text(
        """<testsuites>
  <testsuite name="Feature: Recovery" tests="2" failures="1" skipped="0" time="2.0">
    <testcase name="Scenario: Crash recovery pass" classname="Feature: Recovery" time="1.0">
      <system-out>Final Response: ok</system-out>
    </testcase>
    <testcase name="Scenario: Crash recovery fail" classname="Feature: Recovery" time="1.0">
      <failure message="boom after restart" type="AssertionError"></failure>
      <system-out>Final Response: failed</system-out>
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )
