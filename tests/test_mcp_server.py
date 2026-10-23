"""Offline tests for the in-process Hercules MCP server (Phase 1).

Patching contract (important): mcp_server imports its collaborators at module
level ON PURPOSE so tests can patch the symbols in the ``mcp_server`` namespace
(e.g. ``mcp_server.execute_feature_file``, ``mcp_server.build_normalized_prd``,
``mcp_server.generate_test_plan``) and the tools must resolve those names at
call time for the patches to apply. Only the heavy core.runner/__main__ chain
is imported lazily inside orchestrator.runner_bridge.

No network (the app-url probe is patched), no LLM, no browsers.
"""

import asyncio
import json
import os

import pytest
import testzeus_hercules.mcp_server as mcp_server
import testzeus_hercules.orchestrator.session as session_module
from testzeus_hercules.core.notification_manager import (
    NotificationManager,
    get_notification_hub,
)
from testzeus_hercules.intelligence.normalized_prd import (
    NormalizedPRD,
    PRDRequirement,
    prd_to_json,
)
from testzeus_hercules.intelligence.test_plan import (
    PlanStep,
    TestCase,
    TestPlan,
    plan_to_json,
)
from testzeus_hercules.orchestrator.runner_bridge import ExecutionOutcome
from testzeus_hercules.orchestrator.session import SessionManager

EXPECTED_NEW_TOOLS = {
    "hercules_configure_session",
    "hercules_generate_code_summary",
    "hercules_generate_standardized_prd",
    "hercules_generate_test_plan",
    "hercules_generate_and_execute",
    "hercules_get_run_results",
    "hercules_open_dashboard",
    "hercules_check_config",
    "hercules_rerun",
}
EXPECTED_LEGACY_TOOLS = {"generate_gherkin", "run_test", "get_test_results"}


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Isolate cwd + session manager so _state_dir/_result_dir land in tmp."""
    monkeypatch.delenv("TESTZEUS_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    manager = SessionManager()
    monkeypatch.setattr(mcp_server, "get_session_manager", lambda: manager)
    return tmp_path, manager


def make_plan() -> TestPlan:
    return TestPlan(
        meta={"project": "Demo"},
        requirements=[{"id": "REQ-001", "description": "Login works", "source": "prd", "test_ids": ["TC001", "TC002"]}],
        test_cases=[
            TestCase(id="TC001", title="Login succeeds", steps=[PlanStep(type="action", description="open login"), PlanStep(type="assertion", description="home visible")]),
            TestCase(id="TC002", title="Login fails", steps=[PlanStep(type="action", description="open login"), PlanStep(type="assertion", description="error shown")]),
        ],
    )


def make_prd() -> NormalizedPRD:
    return NormalizedPRD(
        project_name="Demo App",
        summary="demo",
        requirements=[
            PRDRequirement(id="REQ-001", title="Login works", description="Users can log in", acceptance_criteria=["home shown"], source="prd"),
            PRDRequirement(id="REQ-002", title="Search works", description="Users can search", acceptance_criteria=["results shown"], source="prd"),
        ],
    )


def run(coro):
    return asyncio.run(coro)


# ── registration ──────────────────────────────────────────────────────────────


def test_all_tools_registered() -> None:
    tools = {tool.name: tool for tool in mcp_server.mcp._tool_manager.list_tools()}
    assert EXPECTED_NEW_TOOLS <= set(tools)
    assert EXPECTED_LEGACY_TOOLS <= set(tools)
    assert len(tools) == 12
    for name in EXPECTED_NEW_TOOLS:
        assert tools[name].description and len(tools[name].description) > 40, f"{name} needs a descriptive docstring"


# ── hercules_configure_session ────────────────────────────────────────────────


def test_configure_session_returns_session_dict(workspace, monkeypatch) -> None:
    tmp_path, _manager = workspace

    async def fake_probe(url: str, timeout: float = 5.0) -> bool | None:
        return True

    monkeypatch.setattr(mcp_server, "_check_app_url", fake_probe)
    result = run(mcp_server.hercules_configure_session("https://app.example.com", project_path=str(tmp_path)))

    assert result["session_id"].startswith("sess_")
    assert result["app_url_reachable"] is True
    assert result["next_action"]
    assert result["project_path"] == "."  # root-relative, never the absolute host path
    assert str(tmp_path) not in json.dumps(result)


def test_configure_session_rejects_bad_enums(workspace) -> None:
    result = run(mcp_server.hercules_configure_session("https://app.example.com", test_type="database"))
    assert "error" in result
    result = run(mcp_server.hercules_configure_session("https://app.example.com", test_scope="everything"))
    assert "error" in result


# ── hercules_generate_code_summary ────────────────────────────────────────────


def test_generate_code_summary_compact_output(workspace) -> None:
    tmp_path, manager = workspace
    project = tmp_path / "proj"
    (project / "routes").mkdir(parents=True)
    (project / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef health(): ...\n")
    (project / "routes").joinpath("x.js").write_text("router.post('/api/x', () => {})\n")
    state = manager.create_session("https://app.example.com", project_path=str(project))

    result = run(mcp_server.hercules_generate_code_summary(session_id=state.session_id))

    assert "error" not in result
    assert result["code_summary_path"].endswith("code_summary.json")
    assert os.path.isfile(result["code_summary_path"])
    assert result["summary"]["routes_count"] >= 1
    assert isinstance(result["summary"]["entry_points_count"], int)
    assert set(result["summary"]) == {"frameworks", "languages", "entry_points_count", "routes_count"}
    assert not os.path.isabs(result["code_summary_path"])  # display path, not the host layout
    assert state.code_summary_path == os.path.abspath(result["code_summary_path"])


def test_generate_code_summary_requires_project(workspace) -> None:
    _tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    result = run(mcp_server.hercules_generate_code_summary(session_id=state.session_id))
    assert "error" in result


# ── hercules_generate_standardized_prd ────────────────────────────────────────


def test_generate_standardized_prd_with_fake_llm(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    prd_file = tmp_path / "prd.md"
    prd_file.write_text("# Demo App\n\nUsers can log in.\n")
    state = manager.create_session("https://app.example.com", prd_path=str(prd_file))
    seen_sources: list[str] = []

    async def fake_build(source: str, llm=None) -> NormalizedPRD:
        seen_sources.append(source)
        return make_prd()

    monkeypatch.setattr(mcp_server, "build_normalized_prd", fake_build)

    result = run(mcp_server.hercules_generate_standardized_prd(session_id=state.session_id))

    assert seen_sources == [str(prd_file)]
    assert "error" not in result
    assert result["requirement_count"] == 2
    assert result["project_name"] == "Demo App"
    assert [requirement["id"] for requirement in result["requirements"]] == ["REQ-001", "REQ-002"]
    assert os.path.isfile(result["standard_prd_path"])
    assert state.standard_prd_path == os.path.abspath(result["standard_prd_path"])


def test_generate_standardized_prd_requires_source(workspace) -> None:
    _tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    result = run(mcp_server.hercules_generate_standardized_prd(session_id=state.session_id))
    assert "error" in result


def test_generate_standardized_prd_falls_back_to_readme(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    project = tmp_path / "proj"
    project.mkdir()
    (project / "README.md").write_text("# Readme App\n\nRequirements live here.\n")
    state = manager.create_session("https://app.example.com", project_path=str(project))

    async def fake_build(source: str, llm=None) -> NormalizedPRD:
        return make_prd()

    monkeypatch.setattr(mcp_server, "build_normalized_prd", fake_build)
    result = run(mcp_server.hercules_generate_standardized_prd(session_id=state.session_id))
    assert "error" not in result


def test_generate_standardized_prd_swallows_runtime_error(workspace, monkeypatch) -> None:
    """get_litellm_chat_model (AgentsLLMConfigManager.initialize) raises RuntimeError — it must become an error dict, never escape the tool."""
    _tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")

    async def broken_build(state_arg, prd_path: str) -> tuple[NormalizedPRD, str]:
        raise RuntimeError("AgentsLLMConfigManager not initialized; call initialize() first")

    monkeypatch.setattr(mcp_server, "_build_standard_prd", broken_build)

    result = run(mcp_server.hercules_generate_standardized_prd(session_id=state.session_id))

    assert "error" in result
    assert "AgentsLLMConfigManager" in result["error"]


# ── hercules_generate_test_plan ───────────────────────────────────────────────


def test_generate_test_plan_reuses_cached_prd(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")

    prd_file = tmp_path / ".hercules" / "standard_prd.json"
    prd_file.parent.mkdir(parents=True, exist_ok=True)
    prd_to_json(make_prd(), str(prd_file))
    state.standard_prd_path = str(prd_file)

    async def fail_build(source: str, llm=None) -> NormalizedPRD:
        raise AssertionError("cached PRD should be reused, not rebuilt")

    monkeypatch.setattr(mcp_server, "build_normalized_prd", fail_build)

    async def fake_generate(prd, llm=None, cases_per_requirement=3, include_categories=None):
        return make_plan()

    monkeypatch.setattr(mcp_server, "generate_test_plan", fake_generate)

    result = run(mcp_server.hercules_generate_test_plan(session_id=state.session_id))

    assert "error" not in result
    assert result["case_count"] == 2
    assert [case["id"] for case in result["test_cases"]] == ["TC001", "TC002"]
    assert result["coverage"]["totals"]["coverage_percent"] == 100
    assert os.path.isfile(result["test_plan_path"])
    assert state.test_plan_path == os.path.abspath(result["test_plan_path"])


# ── hercules_generate_and_execute ─────────────────────────────────────────────


def _fake_outcome(success: bool = True, steps: int = 3) -> ExecutionOutcome:
    return ExecutionOutcome(
        summaries=[
            {
                "total": 1,
                "passed": 1 if success else 0,
                "failed": 0 if success else 1,
                "errors": 0,
                "junit_xml": "/tmp/x_result.xml",
                "html": "/tmp/x_result.html",
                "report_md": "/tmp/Hercules_Test_Report.md",
                "bundles": ["/tmp/bundles/x"],
                "feature_file": "/tmp/hercules_mcp_run.feature",
            }
        ],
        success=success,
        duration_s=1.234,
        progress_events=[{"message": f"Step {index} completed via browser_nav_agent", "type": "step_completed"} for index in range(steps)],
        steps_completed=steps,
    )


def test_generate_and_execute_plan_subset(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")

    plan_file = tmp_path / "test_plan.json"
    plan_to_json(make_plan(), str(plan_file))
    state.test_plan_path = str(plan_file)

    captured: dict = {}

    async def fake_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        captured["feature_path"] = feature_path
        with open(feature_path, encoding="utf-8") as handle:
            captured["content"] = handle.read()
        return _fake_outcome()

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)

    result = run(mcp_server.hercules_generate_and_execute(session_id=state.session_id, test_ids=["TC001"], additional_instructions="Use the admin user", approve_plan=True))

    assert "error" not in result
    feature_name = os.path.basename(captured["feature_path"])
    assert feature_name.startswith("hercules_mcp_run_") and feature_name.endswith(".feature")  # unique per run
    assert os.path.dirname(captured["feature_path"]).endswith(os.path.join("opt", "input"))
    assert "TC001" in captured["content"] and "TC002" not in captured["content"]
    assert captured["content"].startswith("# Use the admin user")  # additional instructions become Gherkin comments
    assert result["success"] is True
    assert result["duration_s"] == 1.234
    assert result["steps_completed"] == 3
    assert result["bundles_count"] == 1
    # paths outside the Hercules root are reduced to their basename
    assert result["run_report_md"] == "Hercules_Test_Report.md"
    assert result["summaries"][0]["junit_xml"] == "x_result.xml"
    assert result["summaries"][0]["bundles"] == ["x"]
    assert result["source"] == "test_plan"
    assert "additional_instructions_note" in result
    assert len(state.last_run_summaries) == 1  # recorded on the session


def test_generate_and_execute_gherkin_beats_description(workspace, monkeypatch) -> None:
    _tmp_path, manager = workspace
    manager.create_session("https://app.example.com")

    async def fail_generator(description: str) -> str:
        raise AssertionError("description generator must not run when gherkin is given")

    monkeypatch.setattr(mcp_server, "generate_gherkin_from_description", fail_generator)

    async def fake_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        return _fake_outcome()

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)

    result = run(mcp_server.hercules_generate_and_execute(gherkin="Feature: G\n\n  Scenario: s\n    Given a\n", description="should be ignored"))
    assert "error" not in result
    assert result["source"] == "gherkin"


def test_generate_and_execute_from_description(workspace, monkeypatch) -> None:
    _tmp_path, manager = workspace
    manager.create_session("https://app.example.com")

    async def fake_generator(description: str) -> str:
        return f"Feature: from {description}\n"

    monkeypatch.setattr(mcp_server, "generate_gherkin_from_description", fake_generator)

    async def fake_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        with open(feature_path, encoding="utf-8") as handle:
            assert "from buy things" in handle.read()
        return _fake_outcome(success=False)

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)

    result = run(mcp_server.hercules_generate_and_execute(description="buy things"))
    assert "error" not in result
    assert result["source"] == "description"
    assert result["success"] is False


def test_generate_and_execute_description_generation_deadline(workspace, monkeypatch) -> None:
    """A hung description->Gherkin LLM call must error out at _GHERKIN_GENERATION_TIMEOUT_S and free _EXEC_LOCK."""
    _tmp_path, manager = workspace
    manager.create_session("https://app.example.com")

    async def hanging_generator(description: str) -> str:
        await asyncio.sleep(30)  # far beyond the patched timeout; would pin _EXEC_LOCK forever

    monkeypatch.setattr(mcp_server, "generate_gherkin_from_description", hanging_generator)
    monkeypatch.setattr(mcp_server, "_GHERKIN_GENERATION_TIMEOUT_S", 0.05)

    result = run(mcp_server.hercules_generate_and_execute(description="login flow works"))

    assert "error" in result
    assert "timed out" in result["error"]

    async def lock_is_released() -> bool:
        try:
            await asyncio.wait_for(mcp_server._exec_lock().acquire(), timeout=1.0)
        except asyncio.TimeoutError:
            return False
        mcp_server._exec_lock().release()
        return True

    assert run(lock_is_released()), "_EXEC_LOCK must be released after the gherkin-generation timeout"


def test_generate_and_execute_requires_something_to_run(workspace) -> None:
    _tmp_path, manager = workspace
    manager.create_session("https://app.example.com")
    result = run(mcp_server.hercules_generate_and_execute())
    assert "error" in result and "test plan" in result["error"]


def test_generate_and_execute_rejects_unknown_test_ids(workspace) -> None:
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    plan_file = tmp_path / "test_plan.json"
    plan_to_json(make_plan(), str(plan_file))
    state.test_plan_path = str(plan_file)

    result = run(mcp_server.hercules_generate_and_execute(test_ids=["TC999"], approve_plan=True))
    assert "error" in result and "TC999" in result["error"]


def test_generate_and_execute_reports_runner_error(workspace, monkeypatch) -> None:
    _tmp_path, manager = workspace
    manager.create_session("https://app.example.com")

    async def fake_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        return ExecutionOutcome(summaries=[], success=False, duration_s=0.5, progress_events=[], error="boom")

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)
    result = run(mcp_server.hercules_generate_and_execute(gherkin="Feature: G\n\n  Scenario: s\n    Given a\n"))
    assert result["success"] is False
    assert result["error"] == "boom"


# ── hercules_rerun ────────────────────────────────────────────────────────────


def test_rerun_uses_test_plan(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    plan_file = tmp_path / "test_plan.json"
    plan_to_json(make_plan(), str(plan_file))
    state.test_plan_path = str(plan_file)

    async def fake_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        return _fake_outcome()

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)
    result = run(mcp_server.hercules_rerun(session_id=state.session_id, test_ids=["TC002"], approve_plan=True))
    assert "error" not in result
    assert result["source"] == "test_plan"


def test_rerun_falls_back_to_last_feature(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    last_feature = tmp_path / "opt" / "input" / "last.feature"
    last_feature.parent.mkdir(parents=True)
    last_feature.write_text("Feature: last\n\n  Scenario: s\n    Given a\n")
    state.last_run_summaries.append({"feature_file": str(last_feature)})

    captured: dict = {}

    async def fake_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        captured["feature_path"] = feature_path
        return _fake_outcome()

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)
    result = run(mcp_server.hercules_rerun(session_id=state.session_id))
    assert "error" not in result
    assert captured["feature_path"] == str(last_feature)
    assert result["source"] == "last_run"


def test_rerun_with_nothing_to_rerun(workspace) -> None:
    _tmp_path, manager = workspace
    manager.create_session("https://app.example.com")
    result = run(mcp_server.hercules_rerun())
    assert "error" in result


def test_rerun_rejects_recorded_feature_outside_input_dir(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    outside = tmp_path / "elsewhere.feature"
    outside.write_text("Feature: outside\n\n  Scenario: s\n    Given a\n")
    state.last_run_summaries.append({"feature_file": str(outside)})

    async def fail_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        raise AssertionError(f"must not execute unvalidated path {feature_path}")

    monkeypatch.setattr(mcp_server, "execute_feature_file", fail_execute)
    result = run(mcp_server.hercules_rerun(session_id=state.session_id))
    assert result["error"] == "recorded feature file is no longer available; run a test first"


def test_rerun_rejects_recorded_non_feature_file(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    input_dir = tmp_path / "opt" / "input"
    input_dir.mkdir(parents=True)
    not_a_feature = input_dir / "payload.txt"
    not_a_feature.write_text("not gherkin")
    state.last_run_summaries.append({"feature_file": str(not_a_feature)})

    async def fail_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        raise AssertionError(f"must not execute unvalidated path {feature_path}")

    monkeypatch.setattr(mcp_server, "execute_feature_file", fail_execute)
    result = run(mcp_server.hercules_rerun(session_id=state.session_id))
    assert result["error"] == "recorded feature file is no longer available; run a test first"


def test_rerun_accepts_legacy_feature_name(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    legacy = tmp_path / "opt" / "input" / "hercules_mcp_run.feature"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("Feature: legacy\n\n  Scenario: s\n    Given a\n")
    state.last_run_summaries.append({"feature_file": str(legacy)})

    async def fake_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        return _fake_outcome()

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)
    result = run(mcp_server.hercules_rerun(session_id=state.session_id))
    assert "error" not in result
    assert result["source"] == "last_run"


# ── hercules_configure_session redaction ──────────────────────────────────────


def test_configure_session_echoes_redacted_url_but_keeps_state_raw(workspace, monkeypatch) -> None:
    _tmp_path, manager = workspace

    async def fake_probe(url: str, timeout: float = 5.0) -> bool | None:
        return True

    monkeypatch.setattr(mcp_server, "_check_app_url", fake_probe)
    secret_url = "https://alice:secret@host.example.com/path"

    result = run(mcp_server.hercules_configure_session(secret_url))

    assert "secret" not in result["app_url"]
    assert "alice" not in result["app_url"]
    assert result["app_url"] == "https://host.example.com/path"
    # In-memory state keeps the full URL so runs can still authenticate.
    assert manager.resolve_session().app_url == secret_url


def test_configure_session_redacts_url_in_unreachable_warning(workspace, monkeypatch) -> None:
    _tmp_path, _manager = workspace

    async def unreachable(url: str, timeout: float = 5.0) -> bool | None:
        return False

    monkeypatch.setattr(mcp_server, "_check_app_url", unreachable)
    result = run(mcp_server.hercules_configure_session("https://alice:secret@host.example.com/path"))
    assert "not reachable" in result["next_action"]
    assert "secret" not in result["next_action"]


# ── in-memory run-summary bound ───────────────────────────────────────────────


def test_last_run_summaries_trimmed_in_memory(workspace, monkeypatch) -> None:
    _tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    counter = {"n": 0}

    async def fake_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        counter["n"] += 1
        return ExecutionOutcome(summaries=[{"run": counter["n"]}], success=True, duration_s=0.1, progress_events=[], steps_completed=0)

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)

    for _ in range(12):
        result = run(mcp_server.hercules_generate_and_execute(gherkin="Feature: G\n\n  Scenario: s\n    Given a\n"))
        assert "error" not in result

    assert len(state.last_run_summaries) == session_module.MAX_RUN_SUMMARIES == 10
    assert state.last_run_summaries[-1] == {"run": 12}  # newest kept
    assert state.last_run_summaries[0] == {"run": 3}  # oldest trimmed


# ── hercules_get_run_results ──────────────────────────────────────────────────

JUNIT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="demo.feature" tests="2" failures="1" errors="0" time="1.5">
    <testcase name="passing scenario" classname="demo.feature" time="0.7"/>
    <testcase name="failing scenario" classname="demo.feature" time="0.8">
      <failure message="boom" type="AssertionError">stack here</failure>
    </testcase>
  </testsuite>
</testsuites>
"""


@pytest.fixture()
def synthetic_runs(workspace):
    tmp_path, manager = workspace
    old_run = tmp_path / "opt" / "output" / "run_old"
    new_run = tmp_path / "opt" / "output" / "run_new"
    for run_dir in (old_run, new_run):
        run_dir.mkdir(parents=True)
        (run_dir / "demo_result.xml").write_text(JUNIT_XML, encoding="utf-8")
    (new_run / "Hercules_Test_Report.md").write_text("# report\n", encoding="utf-8")
    (new_run / "demo_result.html").write_text("<html></html>", encoding="utf-8")
    bundle_dir = new_run / "bundles" / "failing_scenario"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "bundle.md").write_text("# bundle\n", encoding="utf-8")
    os.utime(old_run, (1_000_000_000, 1_000_000_000))
    os.utime(new_run, (2_000_000_000, 2_000_000_000))
    return tmp_path, manager, old_run, new_run


def test_get_run_results_latest(synthetic_runs) -> None:
    _tmp_path, _manager, _old, new = synthetic_runs
    result = run(mcp_server.hercules_get_run_results())
    assert "error" not in result
    assert result["run_id"] == "run_new"
    assert result["total"] == 2
    assert result["passed"] == 1
    assert result["failed"] == 1
    assert result["errors"] == 0
    # paths are reported relative to the Hercules root, never absolute
    assert result["report_md"] == os.path.join("opt", "output", "run_new", "Hercules_Test_Report.md")
    assert result["html"] == os.path.join("opt", "output", "run_new", "demo_result.html")
    assert result["bundles"] == [os.path.join("opt", "output", "run_new", "bundles", "failing_scenario")]
    assert result["junit_xml"] and not os.path.isabs(result["junit_xml"])
    statuses = {case["name"]: case["status"] for case in result["test_cases"]}
    assert statuses == {"passing scenario": "passed", "failing scenario": "failed"}
    failure = result["test_cases"][1]
    assert failure["failure_message"] == "boom"


def test_get_run_results_by_run_id(synthetic_runs) -> None:
    _tmp_path, _manager, old, _new = synthetic_runs
    result = run(mcp_server.hercules_get_run_results(run_id="run_old"))
    assert result["run_id"] == "run_old"
    result = run(mcp_server.hercules_get_run_results(run_id="missing"))
    assert "error" in result


def test_get_run_results_with_no_runs(workspace) -> None:
    result = run(mcp_server.hercules_get_run_results())
    assert "error" in result


# ── error-message leakage policy ──────────────────────────────────────────────


def test_get_run_results_run_not_found_error_has_no_absolute_path(synthetic_runs) -> None:
    tmp_path, _manager, _old, _new = synthetic_runs
    result = run(mcp_server.hercules_get_run_results(run_id="missing"))
    assert "error" in result
    assert str(tmp_path) not in result["error"], "run-not-found errors must not leak the absolute output dir"
    assert "not found in opt/output" in result["error"], "the project-relative location stays useful"


def test_get_run_results_junit_parse_error_has_no_absolute_path(workspace) -> None:
    tmp_path, _manager = workspace
    run_dir = tmp_path / "opt" / "output" / "run_broken"
    run_dir.mkdir(parents=True)
    (run_dir / "broken_result.xml").write_text("<not-xml", encoding="utf-8")

    result = run(mcp_server.hercules_get_run_results())

    assert "error" in result and "parse" in result["error"]
    assert str(tmp_path) not in result["error"], "junit parse errors must not leak the absolute path"
    assert "broken_result.xml" in result["error"]


# ── hercules_open_dashboard ───────────────────────────────────────────────────


def test_open_dashboard_returns_report_url(synthetic_runs) -> None:
    _tmp_path, _manager, _old, new = synthetic_runs
    result = run(mcp_server.hercules_open_dashboard())
    assert result["dashboard"] == "not_running"
    # report_url keeps its file:// URI contract; report_path is the portable root-relative form.
    assert result["report_path"] == f"opt/output/{new.name}/Hercules_Test_Report.md"
    assert result["report_url"] == (new / "Hercules_Test_Report.md").absolute().as_uri()
    assert result["report_url"].startswith("file:///")
    assert "hercules-dashboard" in result["hint"]


def test_open_dashboard_without_runs(workspace) -> None:
    result = run(mcp_server.hercules_open_dashboard())
    assert result["dashboard"] == "not_running"
    assert result["report_url"] is None and result["report_path"] is None


# ── hercules_check_config ─────────────────────────────────────────────────────


def test_check_config_offline(workspace, monkeypatch) -> None:
    _tmp_path, _manager = workspace

    def fake_model(agent_name: str = "planner_agent"):
        return object()

    monkeypatch.setattr(mcp_server, "get_litellm_chat_model", fake_model)

    result = run(mcp_server.hercules_check_config(ping_model=False))
    assert isinstance(result["ok"], bool)
    checks = result["checks"]
    assert isinstance(checks["llm_config"]["exists"], bool)
    assert checks["llm_config"]["path"].endswith("agents_llm_config.json")
    assert checks["litellm_model"]["ok"] is True
    assert isinstance(checks["playwright"]["ok"], bool)
    assert "model_ping" not in checks


def test_check_config_reports_model_failure(workspace, monkeypatch) -> None:
    _tmp_path, _manager = workspace

    def broken_model(agent_name: str = "planner_agent"):
        raise ValueError("no config file")

    monkeypatch.setattr(mcp_server, "get_litellm_chat_model", broken_model)
    result = run(mcp_server.hercules_check_config(ping_model=False))
    assert result["checks"]["litellm_model"]["ok"] is False
    assert "no config file" in result["checks"]["litellm_model"]["error"]


# ── legacy aliases ────────────────────────────────────────────────────────────


def test_legacy_generate_gherkin_alias(workspace, monkeypatch) -> None:
    _tmp_path, _manager = workspace

    async def fake_generator(description: str) -> str:
        return f"Feature: {description}\n"

    monkeypatch.setattr(mcp_server, "generate_gherkin_from_description", fake_generator)
    assert run(mcp_server.generate_gherkin("legacy")).startswith("Feature: legacy")


def test_legacy_run_test_alias(workspace, monkeypatch) -> None:
    _tmp_path, _manager = workspace

    async def fake_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        return _fake_outcome()

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)
    text = run(mcp_server.run_test(gherkin="Feature: G\n\n  Scenario: s\n    Given a\n"))
    assert "success=True" in text
    text = run(mcp_server.run_test())
    assert text.startswith("Error:")


def test_legacy_get_test_results_alias(synthetic_runs, monkeypatch) -> None:
    _tmp_path, _manager, _old, _new = synthetic_runs
    payload = json.loads(run(mcp_server.get_test_results()))
    assert payload["run_id"] == "run_new"


def test_legacy_get_test_results_error_prefix(synthetic_runs) -> None:
    _tmp_path, _manager, _old, _new = synthetic_runs
    text = run(mcp_server.get_test_results(run_id="missing"))
    assert text.startswith("Error:"), "get_test_results must match run_test's 'Error: ' contract, not dump an error dict"


def test_legacy_run_test_auto_creates_synthetic_session_on_empty_registry(workspace, monkeypatch) -> None:
    """Auto-create happens only on an empty registry: the run lands on a
    synthetic session and no OTHER session is created or repointed."""
    _tmp_path, manager = workspace

    async def fake_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        return _fake_outcome()

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)

    text = run(mcp_server.run_test(gherkin="Feature: G\n\n  Scenario: s\n    Given a\n"))

    assert "success=True" in text
    # Exactly one synthetic (empty app_url) session was created for the run.
    sessions = manager.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].app_url == ""


def test_legacy_run_test_uses_existing_session_without_side_effect(workspace, monkeypatch) -> None:
    """With a configured session present, run_test must not spawn any extra session."""
    _tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")

    async def fake_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        return _fake_outcome()

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)

    text = run(mcp_server.run_test(gherkin="Feature: G\n\n  Scenario: s\n    Given a\n"))

    assert "success=True" in text
    assert manager.list_sessions() == [state]  # no synthetic session side effect
    assert manager.resolve_session() is state


def test_legacy_run_test_without_inputs_creates_no_session(workspace) -> None:
    _tmp_path, manager = workspace
    text = run(mcp_server.run_test())
    assert text.startswith("Error:")
    assert manager.list_sessions() == []  # auto-create only happens when there is something to run


def test_legacy_run_test_forwards_additional_instructions(workspace, monkeypatch) -> None:
    _tmp_path, _manager = workspace
    captured: dict = {}

    async def fake_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        with open(feature_path, encoding="utf-8") as handle:
            captured["content"] = handle.read()
        return _fake_outcome()

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)

    text = run(mcp_server.run_test(gherkin="Feature: G\n\n  Scenario: s\n    Given a\n", additional_instructions="Use the admin user"))
    assert "success=True" in text
    assert captured["content"].startswith("# Use the admin user")


# ── notification hub forwarding ───────────────────────────────────────────────


def test_notification_manager_forwards_to_hub() -> None:
    hub = get_notification_hub()
    received: list[dict] = []

    def listener(event: dict) -> None:
        received.append(event)

    hub.register_listener(listener)
    try:
        separate = NotificationManager()
        separate.notify("step done", "step_completed")
        assert {"message": "step done", "type": "step_completed"} in received

        # hub's own notifications are dispatched once (no self-forwarding/recursion)
        hub.notify("direct", "other")
        assert {"message": "direct", "type": "other"} in received
        assert len(received) == 2
    finally:
        hub.unregister_listener(listener)


def test_hub_listener_sees_executor_step_event() -> None:
    """Regression guard for the simple_hercules._executor_node notify hook."""
    import testzeus_hercules.core.simple_hercules as simple_hercules_module

    source = open(simple_hercules_module.__file__, encoding="utf-8").read()
    assert 'message_type="step_completed"' in source
    assert "get_notification_hub" in source
