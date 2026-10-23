"""Offline regression tests for MCP session artifact isolation and error contracts.

Covers the hardening fixes: per-session artifact directories (standard_prd.json /
test_plan.json / code_summary.json live under .hercules/<session_id>/ and the
global-file fallback is gone), {"error": ...} contracts when collaborators raise
(including SystemExit), and Hercules-root anchoring of the LLM config check.

Everything is offline: the LLM and file-writing collaborators are stubbed.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import testzeus_hercules.mcp_server as mcp_server
from testzeus_hercules.intelligence.normalized_prd import (
    NormalizedPRD,
    PRDRequirement,
    prd_from_json,
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

NO_PRD_ERROR = "No standardized PRD found for this session; call hercules_generate_standardized_prd first."


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Isolate cwd + session manager so state/artifact dirs land in tmp."""
    monkeypatch.delenv("TESTZEUS_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    manager = SessionManager()
    monkeypatch.setattr(mcp_server, "get_session_manager", lambda: manager)
    return tmp_path, manager


def make_prd(project_name: str) -> NormalizedPRD:
    return NormalizedPRD(
        project_name=project_name,
        summary="demo",
        requirements=[PRDRequirement(id="REQ-001", title="Login works", description="Users can log in", acceptance_criteria=["home shown"], source="prd")],
    )


def make_plan(project: str = "Demo") -> TestPlan:
    return TestPlan(
        meta={"project": project},
        requirements=[{"id": "REQ-001", "description": "Login works", "source": "prd", "test_ids": ["TC001"]}],
        test_cases=[TestCase(id="TC001", title="Login succeeds", steps=[PlanStep(type="action", description="open login")])],
    )


def run(coro):
    return asyncio.run(coro)


# ── finding: artifacts are per-session, never global ──────────────────────────


def test_two_sessions_get_distinct_standard_prds(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    prd_a = tmp_path / "prd_a.md"
    prd_b = tmp_path / "prd_b.md"
    prd_a.write_text("# A\n")
    prd_b.write_text("# B\n")
    state_a = manager.create_session("https://a.example.com")
    state_b = manager.create_session("https://b.example.com")
    seen: list[str] = []

    async def fake_build(source: str, llm=None) -> NormalizedPRD:
        seen.append(source)
        return make_prd("PRD A" if source == str(prd_a) else "PRD B")

    monkeypatch.setattr(mcp_server, "build_normalized_prd", fake_build)

    result_a = run(mcp_server.hercules_generate_standardized_prd(session_id=state_a.session_id, prd_path=str(prd_a)))
    result_b = run(mcp_server.hercules_generate_standardized_prd(session_id=state_b.session_id, prd_path=str(prd_b)))

    assert "error" not in result_a and "error" not in result_b
    # Each session writes (and records) its own file.
    assert result_a["standard_prd_path"] == os.path.join(".hercules", state_a.session_id, "standard_prd.json")
    assert result_b["standard_prd_path"] == os.path.join(".hercules", state_b.session_id, "standard_prd.json")
    assert result_a["standard_prd_path"] != result_b["standard_prd_path"]
    assert os.path.isfile(result_a["standard_prd_path"]) and os.path.isfile(result_b["standard_prd_path"])
    assert prd_from_json(result_a["standard_prd_path"]).project_name == "PRD A"
    assert prd_from_json(result_b["standard_prd_path"]).project_name == "PRD B"
    # No global artifact file is written anymore.
    assert not (tmp_path / ".hercules" / "standard_prd.json").exists()

    # _ensure for session B resolves B's own PRD, never session A's.
    ensured_b = run(mcp_server._ensure_standard_prd(state_b, ""))
    assert ensured_b[1] == os.path.abspath(result_b["standard_prd_path"])
    assert ensured_b[0].project_name == "PRD B"


def test_fresh_session_gets_error_not_another_sessions_prd(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    # Simulate a poisoned global file from the old behavior: even when it exists,
    # a fresh session must not consume it.
    hercules_dir = tmp_path / ".hercules"
    hercules_dir.mkdir(parents=True, exist_ok=True)
    prd_to_json(make_prd("Somebody Else"), str(hercules_dir / "standard_prd.json"))

    fresh = SessionManager()  # empty registry, brand-new session
    monkeypatch.setattr(mcp_server, "get_session_manager", lambda: fresh)
    state = fresh.create_session("https://fresh.example.com")
    assert state.standard_prd_path == ""

    async def fail_build(source: str, llm=None) -> NormalizedPRD:
        raise AssertionError("a session without a PRD must error, not consume the global file")

    monkeypatch.setattr(mcp_server, "build_normalized_prd", fail_build)

    result = run(mcp_server.hercules_generate_test_plan(session_id=state.session_id))
    assert result == {"error": NO_PRD_ERROR}

    # Direct helper access is equally refused.
    with pytest.raises(ValueError, match="No standardized PRD found"):
        run(mcp_server._ensure_standard_prd(state, ""))


def test_loaded_session_with_recorded_paths_keeps_working(workspace, monkeypatch) -> None:
    """Backward compatibility: absolute artifact paths recorded on a persisted session still resolve."""
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    kept_dir = tmp_path / "kept"
    kept_dir.mkdir()
    prd_file = kept_dir / "standard_prd.json"
    prd_to_json(make_prd("Persisted"), str(prd_file))
    state.standard_prd_path = str(prd_file)
    manager.save_session_state(state)

    reloaded = SessionManager()  # loads sessions.json, recorded paths intact
    assert reloaded.get_session(state.session_id).standard_prd_path == str(prd_file)
    monkeypatch.setattr(mcp_server, "get_session_manager", lambda: reloaded)

    async def fail_build(source: str, llm=None) -> NormalizedPRD:
        raise AssertionError("the recorded PRD path should be reused, not rebuilt")

    monkeypatch.setattr(mcp_server, "build_normalized_prd", fail_build)

    async def fake_generate(prd, llm=None, cases_per_requirement=3, include_categories=None):
        return make_plan()

    monkeypatch.setattr(mcp_server, "generate_test_plan", fake_generate)

    result = run(mcp_server.hercules_generate_test_plan(session_id=state.session_id))
    assert "error" not in result
    assert result["test_plan_path"] == os.path.join(".hercules", state.session_id, "test_plan.json")
    assert reloaded.get_session(state.session_id).test_plan_path == os.path.abspath(result["test_plan_path"])


def test_code_summary_is_per_session(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    project = tmp_path / "proj"
    project.mkdir()
    state_a = manager.create_session("https://a.example.com", project_path=str(project))
    state_b = manager.create_session("https://b.example.com", project_path=str(project))

    def fake_generate(path: str) -> dict:
        return {"frameworks": [], "languages": {}, "entry_points": [], "routes": [], "total_files": 0}

    def fake_save(summary: dict, out_path: str) -> None:
        Path(out_path).write_text("{}", encoding="utf-8")

    monkeypatch.setattr(mcp_server, "generate_code_summary", fake_generate)
    monkeypatch.setattr(mcp_server, "save_summary", fake_save)

    result_a = run(mcp_server.hercules_generate_code_summary(session_id=state_a.session_id))
    result_b = run(mcp_server.hercules_generate_code_summary(session_id=state_b.session_id))

    assert "error" not in result_a and "error" not in result_b
    assert result_a["code_summary_path"] != result_b["code_summary_path"]
    assert result_a["code_summary_path"] == os.path.join(".hercules", state_a.session_id, "code_summary.json")
    assert result_b["code_summary_path"] == os.path.join(".hercules", state_b.session_id, "code_summary.json")
    assert not (tmp_path / ".hercules" / "code_summary.json").exists()


def test_test_plan_is_per_session(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    state_a = manager.create_session("https://a.example.com")
    state_b = manager.create_session("https://b.example.com")
    for state, name in ((state_a, "prd_a.md"), (state_b, "prd_b.md")):
        prd_file = tmp_path / name
        prd_file.write_text(f"# {name}\n")
        prd_to_json(make_prd(name), str(prd_file))
        state.standard_prd_path = str(prd_file)

    async def fake_generate(prd, llm=None, cases_per_requirement=3, include_categories=None):
        return make_plan(project=prd.project_name)

    monkeypatch.setattr(mcp_server, "generate_test_plan", fake_generate)

    result_a = run(mcp_server.hercules_generate_test_plan(session_id=state_a.session_id))
    result_b = run(mcp_server.hercules_generate_test_plan(session_id=state_b.session_id))

    assert "error" not in result_a and "error" not in result_b
    assert result_a["test_plan_path"] == os.path.join(".hercules", state_a.session_id, "test_plan.json")
    assert result_b["test_plan_path"] == os.path.join(".hercules", state_b.session_id, "test_plan.json")
    assert not (tmp_path / ".hercules" / "test_plan.json").exists()


# ── finding: collaborators raising must yield {"error": ...}, never exceptions ─


def test_generate_and_execute_description_llm_failure_returns_error(workspace, monkeypatch) -> None:
    _tmp_path, manager = workspace
    manager.create_session("https://app.example.com")

    async def broken_generator(description: str) -> str:
        raise RuntimeError("llm exploded")

    monkeypatch.setattr(mcp_server, "generate_gherkin_from_description", broken_generator)
    result = run(mcp_server.hercules_generate_and_execute(description="buy things"))
    assert "error" in result
    assert "llm exploded" in result["error"]


def test_generate_and_execute_system_exit_returns_error(workspace, monkeypatch) -> None:
    _tmp_path, manager = workspace
    manager.create_session("https://app.example.com")

    async def exiting_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        raise SystemExit(2)  # BaseException: must not escape the tool

    monkeypatch.setattr(mcp_server, "execute_feature_file", exiting_execute)
    result = run(mcp_server.hercules_generate_and_execute(gherkin="Feature: G\n\n  Scenario: s\n    Given a\n"))
    assert result["error"] == "SystemExit: 2"


def test_generate_test_plan_llm_failure_returns_error(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    prd_file = tmp_path / "prd.md"
    prd_to_json(make_prd("Demo"), str(prd_file))
    state.standard_prd_path = str(prd_file)

    async def broken_generate(prd, llm=None, cases_per_requirement=3, include_categories=None):
        raise RuntimeError("plan llm down")

    monkeypatch.setattr(mcp_server, "generate_test_plan", broken_generate)
    result = run(mcp_server.hercules_generate_test_plan(session_id=state.session_id))
    assert "error" in result
    assert "plan llm down" in result["error"]
    assert state.test_plan_path == ""  # nothing recorded on failure


def test_generate_test_plan_write_failure_returns_error(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    prd_file = tmp_path / "prd.md"
    prd_to_json(make_prd("Demo"), str(prd_file))
    state.standard_prd_path = str(prd_file)

    async def fake_generate(prd, llm=None, cases_per_requirement=3, include_categories=None):
        return make_plan()

    def broken_write(plan, out_path: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(mcp_server, "generate_test_plan", fake_generate)
    monkeypatch.setattr(mcp_server, "plan_to_json", broken_write)
    result = run(mcp_server.hercules_generate_test_plan(session_id=state.session_id))
    assert "error" in result
    assert "disk full" in result["error"]


def test_generate_code_summary_failure_returns_error(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    project = tmp_path / "proj"
    project.mkdir()
    state = manager.create_session("https://app.example.com", project_path=str(project))

    def broken_scan(path: str) -> dict:
        raise RuntimeError("scan failed")

    monkeypatch.setattr(mcp_server, "generate_code_summary", broken_scan)
    result = run(mcp_server.hercules_generate_code_summary(session_id=state.session_id))
    assert "error" in result
    assert "scan failed" in result["error"]


def test_rerun_runner_failures_return_error_dicts(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    last_feature = tmp_path / "opt" / "input" / "last.feature"
    last_feature.parent.mkdir(parents=True)
    last_feature.write_text("Feature: last\n\n  Scenario: s\n    Given a\n")
    state.last_run_summaries.append({"feature_file": str(last_feature)})

    async def exiting_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        raise SystemExit(3)

    monkeypatch.setattr(mcp_server, "execute_feature_file", exiting_execute)
    result = run(mcp_server.hercules_rerun(session_id=state.session_id))
    assert result["error"] == "SystemExit: 3"

    async def raising_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        raise RuntimeError("browser exploded")

    monkeypatch.setattr(mcp_server, "execute_feature_file", raising_execute)
    result = run(mcp_server.hercules_rerun(session_id=state.session_id))
    assert "error" in result
    assert "browser exploded" in result["error"]


# ── finding: LLM config check anchors at the Hercules root ────────────────────


def test_check_config_anchors_llm_config_at_hercules_root(workspace, monkeypatch) -> None:
    tmp_path, _manager = workspace
    elsewhere = tmp_path / "unrelated_cwd"
    elsewhere.mkdir()
    monkeypatch.setenv("TESTZEUS_ROOT", str(tmp_path))
    monkeypatch.chdir(elsewhere)  # process cwd differs from the Hercules root

    result = run(mcp_server.hercules_check_config(ping_model=False))

    assert result["checks"]["llm_config"]["path"] == "agents_llm_config.json"
    assert result["checks"]["llm_config"]["exists"] is False


def test_check_config_honors_llm_config_env_override(workspace, monkeypatch) -> None:
    tmp_path, _manager = workspace
    custom = tmp_path / "custom_llm_config.json"
    custom.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AGENTS_LLM_CONFIG_FILE", str(custom))

    result = run(mcp_server.hercules_check_config(ping_model=False))

    assert result["checks"]["llm_config"]["path"] == "custom_llm_config.json"
    assert result["checks"]["llm_config"]["exists"] is True
