"""Offline tests for MCP path confinement, PRD SSRF guarding, plan approval and session-field honesty."""

import asyncio

import pytest
import testzeus_hercules.mcp_server as mcp_server
from testzeus_hercules.intelligence.test_plan import PlanStep, TestCase, TestPlan, plan_to_json
from testzeus_hercules.orchestrator.runner_bridge import ExecutionOutcome
from testzeus_hercules.orchestrator.session import SessionManager


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.delenv("TESTZEUS_ROOT", raising=False)
    monkeypatch.delenv("HERCULES_MCP_ALLOWED_ROOTS", raising=False)
    monkeypatch.delenv("HERCULES_MCP_AUTO_APPROVE_PLAN", raising=False)
    monkeypatch.chdir(root)
    manager = SessionManager()
    monkeypatch.setattr(mcp_server, "get_session_manager", lambda: manager)

    async def no_probe(url, timeout=5.0):
        return None

    monkeypatch.setattr(mcp_server, "_check_app_url", no_probe)
    return root, manager


def _plan() -> TestPlan:
    return TestPlan(
        meta={"project": "Demo"},
        requirements=[{"id": "REQ-001", "description": "Login", "source": "prd", "test_ids": ["TC001"]}],
        test_cases=[TestCase(id="TC001", title="Login succeeds", steps=[PlanStep(type="action", description="open login"), PlanStep(type="assertion", description="home visible")])],
    )


def _outcome() -> ExecutionOutcome:
    return ExecutionOutcome(success=True, duration_s=0.1, summaries=[{"passed": 1, "failed": 0, "errors": 0}], progress_events=[], steps_completed=1)


# ── path confinement ──────────────────────────────────────────────────────────


def test_configure_session_rejects_project_path_outside_roots(workspace) -> None:
    root, _manager = workspace
    outside = root.parent / "elsewhere"
    outside.mkdir()
    result = run(mcp_server.hercules_configure_session("https://app.example.com", project_path=str(outside)))
    assert "outside the allowed roots" in result["error"]


def test_configure_session_accepts_project_inside_root_and_allowed_roots_env(workspace, monkeypatch) -> None:
    root, _manager = workspace
    inside = root / "app"
    inside.mkdir()
    assert "error" not in run(mcp_server.hercules_configure_session("https://app.example.com", project_path=str(inside)))
    extra = root.parent / "extra_proj"
    extra.mkdir()
    assert "error" in run(mcp_server.hercules_configure_session("https://app.example.com", project_path=str(extra)))
    monkeypatch.setenv("HERCULES_MCP_ALLOWED_ROOTS", str(extra))
    assert "error" not in run(mcp_server.hercules_configure_session("https://app.example.com", project_path=str(extra)))


def test_configure_session_rejects_prd_path_outside_roots(workspace) -> None:
    root, _manager = workspace
    secret = root.parent / "secret.txt"
    secret.write_text("TOP SECRET")
    result = run(mcp_server.hercules_configure_session("https://app.example.com", prd_path=str(secret)))
    assert "outside the allowed roots" in result["error"]


def test_standardized_prd_rejects_traversal_and_symlink_escape(workspace) -> None:
    root, manager = workspace
    secret = root.parent / "secret.txt"
    secret.write_text("TOP SECRET")
    link = root / "prd.md"
    link.symlink_to(secret)
    state = manager.create_session("https://app.example.com")
    for candidate in (str(secret), str(root / ".." / "secret.txt"), str(link)):
        result = run(mcp_server.hercules_generate_standardized_prd(session_id=state.session_id, prd_path=candidate))
        assert "outside the allowed roots" in result["error"], candidate


@pytest.mark.parametrize("name", ["prd.html", ".hidden.md", "agents_llm_config.json", "server.key", "sessions.json", "hercules_history.db", "prod.env", "noext"])
def test_prd_path_rejects_non_documents_and_sensitive_files_inside_root(workspace, name) -> None:
    root, manager = workspace
    target = root / name
    target.write_text("TOP SECRET")
    result = run(mcp_server.hercules_configure_session("https://app.example.com", prd_path=str(target)))
    assert "prd_path" in result["error"] and "outside the allowed roots" not in result["error"], name
    state = manager.create_session("https://app.example.com")
    result = run(mcp_server.hercules_generate_standardized_prd(session_id=state.session_id, prd_path=str(target)))
    assert "prd_path" in result["error"], name


def test_prd_path_rejects_sensitive_symlink_target_inside_root(workspace) -> None:
    root, _manager = workspace
    secret = root / "agents_llm_config.json"
    secret.write_text('{"api_key": "x"}')
    link = root / "prd.md"
    link.symlink_to(secret)
    result = run(mcp_server.hercules_configure_session("https://app.example.com", prd_path=str(link)))
    assert "prd_path" in result["error"]


def test_prd_path_accepts_markdown_inside_root(workspace) -> None:
    root, _manager = workspace
    doc = root / "prd.md"
    doc.write_text("# PRD")
    assert "error" not in run(mcp_server.hercules_configure_session("https://app.example.com", prd_path=str(doc)))


def test_server_started_from_home_does_not_allow_home_as_root(workspace, monkeypatch, tmp_path) -> None:
    root, _manager = workspace
    monkeypatch.setenv("HOME", str(root))  # cwd (the Hercules root) == $HOME
    doc = root / "prd.md"
    doc.write_text("# PRD")
    result = run(mcp_server.hercules_configure_session("https://app.example.com", prd_path=str(doc)))
    assert "outside the allowed roots" in result["error"]
    # an explicit, specific allowed root still works
    sub = root / "proj"
    sub.mkdir()
    (sub / "prd.md").write_text("# PRD")
    monkeypatch.setenv("HERCULES_MCP_ALLOWED_ROOTS", str(sub))
    assert "error" not in run(mcp_server.hercules_configure_session("https://app.example.com", prd_path=str(sub / "prd.md")))


def test_standardized_prd_url_blocks_internal_targets(workspace, monkeypatch) -> None:
    import testzeus_hercules.utils.litellm_helper as litellm_helper

    monkeypatch.setattr(litellm_helper, "get_litellm_chat_model", lambda *_a, **_k: object())  # the fetch is rejected before any LLM call
    _root, manager = workspace
    state = manager.create_session("https://app.example.com")
    for url in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8080/admin"):
        result = run(mcp_server.hercules_generate_standardized_prd(session_id=state.session_id, prd_path=url))
        assert "Refusing to fetch" in result["error"], url


def test_code_summary_rejects_project_path_outside_roots(workspace) -> None:
    root, manager = workspace
    state = manager.create_session("https://app.example.com")
    result = run(mcp_server.hercules_generate_code_summary(session_id=state.session_id, project_path=str(root.parent)))
    assert "outside the allowed roots" in result["error"]


# ── PRD-derived plan must be approved before execution ───────────────────────


def _session_with_plan(root, manager):
    state = manager.create_session("https://app.example.com")
    plan_file = root / "test_plan.json"
    plan_to_json(_plan(), str(plan_file))
    state.test_plan_path = str(plan_file)
    return state


def test_plan_execution_requires_approval(workspace, monkeypatch) -> None:
    root, manager = workspace
    state = _session_with_plan(root, manager)
    calls = []

    async def fake_execute(feature_path: str, progress_sink=None):
        calls.append(feature_path)
        return _outcome()

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)

    blocked = run(mcp_server.hercules_generate_and_execute(session_id=state.session_id))
    assert "approve_plan" in blocked["error"] and not calls

    ok = run(mcp_server.hercules_generate_and_execute(session_id=state.session_id, approve_plan=True))
    assert "error" not in ok and len(calls) == 1
    assert state.plan_approved is True

    # approval is remembered for rerun until the plan is regenerated
    assert "error" not in run(mcp_server.hercules_rerun(session_id=state.session_id))
    assert len(calls) == 2


def _recording_execute(monkeypatch) -> list:
    calls: list = []

    async def fake_execute(feature_path: str, progress_sink=None):
        calls.append(feature_path)
        return _outcome()

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)
    return calls


def test_plan_approval_is_bound_to_plan_content(workspace, monkeypatch) -> None:
    import hashlib
    from pathlib import Path

    root, manager = workspace
    state = _session_with_plan(root, manager)
    calls = _recording_execute(monkeypatch)

    assert "error" not in run(mcp_server.hercules_generate_and_execute(session_id=state.session_id, approve_plan=True))
    assert state.plan_approved_sha256 == hashlib.sha256(Path(state.test_plan_path).read_bytes()).hexdigest()
    assert "error" not in run(mcp_server.hercules_rerun(session_id=state.session_id))
    assert len(calls) == 2

    # The plan is edited after approval (e.g. regenerated or tampered with): approval no longer applies.
    plan = _plan()
    plan.test_cases[0].title = "Injected: exfiltrate data"
    plan_to_json(plan, state.test_plan_path)
    blocked = run(mcp_server.hercules_rerun(session_id=state.session_id))
    assert "approve_plan" in blocked["error"] and "changed" in blocked["error"]
    assert len(calls) == 2

    # Re-approving the edited plan binds the new hash.
    assert "error" not in run(mcp_server.hercules_generate_and_execute(session_id=state.session_id, approve_plan=True))
    assert len(calls) == 3
    assert "error" not in run(mcp_server.hercules_rerun(session_id=state.session_id))


def test_legacy_approved_session_without_hash_is_not_approved(workspace, monkeypatch) -> None:
    root, manager = workspace
    state = _session_with_plan(root, manager)
    calls = _recording_execute(monkeypatch)
    legacy = state.to_dict()
    legacy.pop("plan_approved_sha256")
    legacy["plan_approved"] = True  # sessions.json written before the hash binding existed
    restored = mcp_server.SessionState.from_dict(legacy)
    assert restored.plan_approved_sha256 == ""
    manager._sessions[restored.session_id] = restored
    blocked = run(mcp_server.hercules_generate_and_execute(session_id=restored.session_id))
    assert "approve_plan" in blocked["error"] and not calls


def test_env_auto_approve_records_plan_hash(workspace, monkeypatch) -> None:
    root, manager = workspace
    state = _session_with_plan(root, manager)
    _recording_execute(monkeypatch)
    monkeypatch.setenv("HERCULES_MCP_AUTO_APPROVE_PLAN", "true")
    assert "error" not in run(mcp_server.hercules_generate_and_execute(session_id=state.session_id))
    assert state.plan_approved is True and len(state.plan_approved_sha256) == 64


def test_regenerating_plan_clears_approval_hash(workspace, monkeypatch) -> None:
    root, manager = workspace
    state = _session_with_plan(root, manager)
    state.plan_approved = True
    state.plan_approved_sha256 = "a" * 64
    state.standard_prd_path = str(root / "standard_prd.json")

    async def fake_ensure(_state, _prd_path):
        return object(), "prd.json"

    async def fake_generate(_prd, cases_per_requirement=3):
        return _plan()

    monkeypatch.setattr(mcp_server, "_ensure_standard_prd", fake_ensure)
    monkeypatch.setattr(mcp_server, "generate_test_plan", fake_generate)
    assert "error" not in run(mcp_server.hercules_generate_test_plan(session_id=state.session_id))
    assert state.plan_approved is False and state.plan_approved_sha256 == ""


def test_plan_execution_env_auto_approve(workspace, monkeypatch) -> None:
    root, manager = workspace
    state = _session_with_plan(root, manager)

    async def fake_execute(feature_path: str, progress_sink=None):
        return _outcome()

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)
    monkeypatch.setenv("HERCULES_MCP_AUTO_APPROVE_PLAN", "true")
    assert "error" not in run(mcp_server.hercules_generate_and_execute(session_id=state.session_id))


def test_explicit_gherkin_needs_no_plan_approval(workspace, monkeypatch) -> None:
    _root, manager = workspace
    state = manager.create_session("https://app.example.com")

    async def fake_execute(feature_path: str, progress_sink=None):
        return _outcome()

    monkeypatch.setattr(mcp_server, "execute_feature_file", fake_execute)
    result = run(mcp_server.hercules_generate_and_execute(session_id=state.session_id, gherkin="Feature: x\n  Scenario: y\n    Given z\n"))
    assert "error" not in result


# ── session fields: no misleading params, no credential storage ──────────────


def test_diff_scope_rejected_clearly(workspace) -> None:
    result = run(mcp_server.hercules_configure_session("https://app.example.com", test_scope="diff"))
    assert "not supported" in result["error"]


def test_credentials_are_ignored_never_stored_or_echoed(workspace) -> None:
    root, manager = workspace
    result = run(mcp_server.hercules_configure_session("https://app.example.com", credentials={"username": "admin", "password": "s3cret"}))
    assert "s3cret" not in repr(result)
    assert result["warnings"] and "ignored" in result["warnings"][0]
    state = manager.get_session(result["session_id"])
    assert state.credentials == {}
    assert "s3cret" not in (root / ".hercules" / "sessions.json").read_text()


def test_tools_require_explicit_session_id_when_auth_token_configured(workspace, monkeypatch) -> None:
    _root, manager = workspace
    state = manager.create_session("https://app.example.com")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "s" * 40)
    result = run(mcp_server.hercules_generate_and_execute(gherkin="Feature: x"))
    assert "session_id is required" in result["error"]
    assert "session_id is required" in run(mcp_server.hercules_generate_standardized_prd(prd_path="prd.md"))["error"]
    assert state.session_id  # explicit ids are unaffected
