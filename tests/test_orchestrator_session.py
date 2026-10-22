"""Offline tests for the orchestrator session registry and runner bridge.

No network, no LLM, no browsers. Persistence is exercised against tmp
directories (sessions.json lives under the process cwd / TESTZEUS_ROOT).
"""

import asyncio
import json
import os

import pytest
import testzeus_hercules.orchestrator.session as session_module
from testzeus_hercules.core.notification_manager import get_notification_hub
from testzeus_hercules.intelligence.normalized_prd import NormalizedPRD, PRDRequirement
from testzeus_hercules.intelligence.test_plan import PlanStep, TestCase, TestPlan
from testzeus_hercules.orchestrator.runner_bridge import (
    describe_run,
    execute_feature_file,
    write_plan_scenarios_feature,
)
from testzeus_hercules.orchestrator.session import SessionManager, get_session_manager


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Isolate cwd (and TESTZEUS_ROOT) so .hercules persistence lands in tmp."""
    monkeypatch.delenv("TESTZEUS_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_plan() -> TestPlan:
    return TestPlan(
        meta={"project": "Demo"},
        requirements=[{"id": "REQ-001", "description": "Login works", "source": "prd", "test_ids": ["TC001", "TC002"]}],
        test_cases=[
            TestCase(id="TC001", title="Login succeeds", steps=[PlanStep(type="action", description="open login"), PlanStep(type="assertion", description="home visible")]),
            TestCase(id="TC002", title="Login fails", steps=[PlanStep(type="action", description="open login"), PlanStep(type="assertion", description="error shown")]),
        ],
    )


# ── SessionState / SessionManager ─────────────────────────────────────────────


def test_create_and_get_session(workspace) -> None:
    manager = SessionManager()
    state = manager.create_session(
        "https://app.example.com",
        project_path="/tmp/proj",
        test_type="backend",
        credentials={"username": "admin", "password": "s3cret"},
    )
    assert state.session_id.startswith("sess_") and len(state.session_id) == len("sess_") + 8
    assert state.test_type == "backend"
    assert state.credentials["password"] == "s3cret"  # in-memory keeps the real value
    assert manager.get_session(state.session_id) is state
    assert state.created_at  # iso timestamp


def test_create_session_validates_enums(workspace) -> None:
    manager = SessionManager()
    with pytest.raises(ValueError, match="test_type"):
        manager.create_session("https://app.example.com", test_type="database")
    with pytest.raises(ValueError, match="test_scope"):
        manager.create_session("https://app.example.com", test_scope="everything")


def test_get_unknown_session_raises_keyerror(workspace) -> None:
    manager = SessionManager()
    manager.create_session("https://app.example.com")
    manager.create_session("https://other.example.com")
    with pytest.raises(KeyError) as exc_info:
        manager.get_session("sess_deadbeef")
    # Capability leak guard: the error must NOT enumerate the known session ids.
    message = str(exc_info.value)
    assert "Unknown session_id 'sess_deadbeef'" in message
    assert "sess_" not in message.replace("'sess_deadbeef'", "")


def test_create_session_touch_recent_false_keeps_most_recent(workspace) -> None:
    manager = SessionManager()
    first = manager.create_session("https://first.example.com")
    synthetic = manager.create_session("", touch_recent=False)
    assert manager.resolve_session() is first
    assert manager.resolve_session(synthetic.session_id) is synthetic

    # The insertion-order trick survives persistence: a reload keeps `first` as most recent.
    reloaded = SessionManager()
    assert reloaded.resolve_session().session_id == first.session_id
    assert reloaded.get_session(synthetic.session_id).session_id == synthetic.session_id


def test_create_session_touch_recent_false_on_empty_registry(workspace) -> None:
    manager = SessionManager()
    synthetic = manager.create_session("", touch_recent=False)
    # Nothing to keep recent: the synthetic session is the only resolvable one.
    assert manager.resolve_session() is synthetic


def test_resolve_with_no_sessions_raises_valueerror(workspace) -> None:
    manager = SessionManager()
    with pytest.raises(ValueError, match="No session"):
        manager.resolve_session()


def test_resolve_empty_returns_most_recent(workspace) -> None:
    manager = SessionManager()
    first = manager.create_session("https://first.example.com")
    second = manager.create_session("https://second.example.com")
    assert manager.resolve_session() is second
    assert manager.resolve_session(first.session_id) is first
    assert manager.resolve_session(second.session_id) is second


def test_repr_redacts_credentials(workspace) -> None:
    manager = SessionManager()
    state = manager.create_session("https://app.example.com", credentials={"username": "admin", "password": "s3cret"})
    rendered = repr(state)
    assert "s3cret" not in rendered
    assert "***" in rendered


def test_persistence_writes_file_and_redacts_credentials(workspace) -> None:
    manager = SessionManager()
    state = manager.create_session("https://app.example.com", credentials={"username": "admin", "password": "s3cret"})
    manager.get_session(state.session_id)  # force lookup path

    sessions_file = os.path.join(str(workspace), ".hercules", "sessions.json")
    assert os.path.isfile(sessions_file)
    with open(sessions_file, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert state.session_id in payload
    assert payload[state.session_id]["credentials"] == {"username": "***", "password": "***"}


def test_load_sessions_roundtrip(workspace) -> None:
    manager = SessionManager()
    state = manager.create_session("https://app.example.com", project_path="/tmp/proj", prd_path="/tmp/prd.md")
    manager.save_session_state(state)

    reloaded = SessionManager()
    assert state.session_id in {session.session_id for session in reloaded.list_sessions()}
    assert reloaded.get_session(state.session_id).project_path == "/tmp/proj"


def test_get_session_manager_is_singleton(monkeypatch, workspace) -> None:
    monkeypatch.setattr(session_module, "_MANAGER", None)
    assert get_session_manager() is get_session_manager()
    assert isinstance(get_session_manager(), SessionManager)


def test_persisted_app_url_redacts_userinfo(workspace) -> None:
    secret_url = "https://user:tok3n@secret-host.example.com/api?x=1#frag"
    manager = SessionManager()
    state = manager.create_session(secret_url)

    sessions_file = os.path.join(str(workspace), ".hercules", "sessions.json")
    with open(sessions_file, encoding="utf-8") as handle:
        raw = handle.read()
    assert "tok3n" not in raw
    assert "user:tok3n@secret-host.example.com" not in raw
    payload = json.loads(raw)
    assert payload[state.session_id]["app_url"] == "https://secret-host.example.com/api?x=1#frag"
    # In-memory state keeps the real URL so the run can still authenticate.
    assert state.app_url == secret_url
    # Echo paths (repr) must not leak the token either.
    assert "tok3n" not in repr(state)


def test_redact_url_malformed_urls_never_leak_userinfo() -> None:
    """urlsplit rejects some credentialed URLs (bad IPv6 bracket, NFKC-host forms);
    redaction must fall back to a regex userinfo strip, never return the raw URL."""
    malformed = [
        "https://user:pass@[::1",
        "https://user:pass@[::1/path?x=1#frag",
        "https://user:pass@\uff03host",  # fullwidth '#': urlsplit raises under NFKC
        "https://user:pass@examp\uff1ale.com",  # fullwidth ':': same NFKC rejection
    ]
    for url in malformed:
        redacted = session_module._redact_url(url)
        assert "user:pass" not in redacted, url
        assert redacted != url


def test_redact_url_fallback_keeps_rest_of_url() -> None:
    assert session_module._redact_url("https://user:pass@[::1/path?x=1#frag") == "https://[::1/path?x=1#frag"


def test_redact_url_valid_urls_unchanged() -> None:
    assert session_module._redact_url("https://user:tok3n@secret-host.example.com/api?x=1#frag") == "https://secret-host.example.com/api?x=1#frag"
    assert session_module._redact_url("https://user:pass@[::1]:8443/x") == "https://[::1]:8443/x"
    assert session_module._redact_url("https://plain-host.example.com") == "https://plain-host.example.com"
    assert session_module._redact_url("") == ""


def test_persisted_app_url_redacts_userinfo_even_when_malformed(workspace) -> None:
    """to_dict() promises userinfo never persists — even for URLs urlsplit rejects."""
    manager = SessionManager()
    state = manager.create_session("https://user:pass@[::1")

    sessions_file = os.path.join(str(workspace), ".hercules", "sessions.json")
    with open(sessions_file, encoding="utf-8") as handle:
        raw = handle.read()
    assert "user:pass" not in raw
    assert json.loads(raw)[state.session_id]["app_url"] == "https://[::1"
    # In-memory state keeps the real URL so the run can still authenticate.
    assert state.app_url == "https://user:pass@[::1"


def test_corrupt_sessions_json_is_recovered_without_raising(workspace) -> None:
    sessions_file = os.path.join(str(workspace), ".hercules", "sessions.json")
    os.makedirs(os.path.dirname(sessions_file), exist_ok=True)
    with open(sessions_file, "w", encoding="utf-8") as handle:
        handle.write("{not json")

    reloaded = SessionManager()  # must not raise
    assert reloaded.list_sessions() == []

    # The next save recovers the file into valid JSON.
    state = reloaded.create_session("https://app.example.com")
    with open(sessions_file, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert state.session_id in payload


def test_truncated_sessions_json_is_recovered_without_raising(workspace) -> None:
    manager = SessionManager()
    manager.create_session("https://app.example.com")
    sessions_file = os.path.join(str(workspace), ".hercules", "sessions.json")
    with open(sessions_file, encoding="utf-8") as handle:
        good = handle.read()
    with open(sessions_file, "w", encoding="utf-8") as handle:
        handle.write(good[: len(good) // 2])  # simulate a crash mid-write

    reloaded = SessionManager()  # must not raise
    assert reloaded.list_sessions() == []


def test_non_dict_sessions_json_is_skipped_with_warning(workspace, caplog) -> None:
    sessions_file = os.path.join(str(workspace), ".hercules", "sessions.json")
    os.makedirs(os.path.dirname(sessions_file), exist_ok=True)
    with open(sessions_file, "w", encoding="utf-8") as handle:
        handle.write('["not", "a", "session", "map"]')

    with caplog.at_level("WARNING", logger=session_module._LOGGER.name):
        reloaded = SessionManager()  # must not raise

    assert reloaded.list_sessions() == []
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert warnings, "a non-dict sessions.json payload must be logged, not discarded silently"
    assert "sessions.json" in warnings[-1].getMessage()
    assert "list" in warnings[-1].getMessage()


def test_load_sessions_skips_malformed_entries_with_warning(workspace, caplog) -> None:
    """One malformed entry must not abort the load: the good ones load and a
    single warning names the count and the offending keys."""
    sessions_file = os.path.join(str(workspace), ".hercules", "sessions.json")
    os.makedirs(os.path.dirname(sessions_file), exist_ok=True)
    payload = {
        "sess_bad1": ["not", "a", "dict"],
        "sess_good": {"session_id": "sess_good", "app_url": "https://good.example.com", "project_path": "/tmp/proj"},
        "sess_bad2": {"unrecognized_only": True},  # dict, but missing required fields
    }
    with open(sessions_file, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)

    with caplog.at_level("WARNING", logger=session_module._LOGGER.name):
        reloaded = SessionManager()  # must not raise

    # The good entry still loads with its fields intact.
    assert reloaded.get_session("sess_good").project_path == "/tmp/proj"
    assert {s.session_id for s in reloaded.list_sessions()} == {"sess_good"}

    # Exactly one warning covers both malformed entries.
    warnings = [record for record in caplog.records if record.levelname == "WARNING" and "malformed session entries" in record.getMessage()]
    assert len(warnings) == 1, "skipped entries must be summarized once per load"
    message = warnings[0].getMessage()
    assert "Skipped 2 malformed session entries" in message
    assert "sess_bad1" in message and "sess_bad2" in message


def test_to_dict_caps_last_run_summaries(workspace) -> None:
    manager = SessionManager()
    state = manager.create_session("https://app.example.com")
    state.last_run_summaries = [{"run_id": f"r{i}"} for i in range(25)]

    persisted = state.to_dict(redact_credentials=True)

    assert len(persisted["last_run_summaries"]) == session_module.MAX_RUN_SUMMARIES == 10
    # The most recent entries are the ones kept.
    assert persisted["last_run_summaries"][-1] == {"run_id": "r24"}
    assert persisted["last_run_summaries"][0] == {"run_id": "r15"}


# ── runner_bridge ─────────────────────────────────────────────────────────────


def test_write_plan_scenarios_feature_all_and_subset(workspace) -> None:
    all_path = write_plan_scenarios_feature(make_plan(), None, os.path.join(str(workspace), "all.feature"))
    with open(all_path, encoding="utf-8") as handle:
        content = handle.read()
    assert "TC001" in content and "TC002" in content
    assert content.startswith("Feature:")

    subset_path = write_plan_scenarios_feature(make_plan(), ["TC002"], os.path.join(str(workspace), "subset.feature"))
    with open(subset_path, encoding="utf-8") as handle:
        subset = handle.read()
    assert "TC002" in subset and "TC001" not in subset


def test_describe_run_aggregates() -> None:
    summaries = [
        {"total": 2, "passed": 1, "failed": 1, "errors": 0, "junit_xml": "a.xml", "report_md": "a.md", "bundles": ["b1"], "feature_file": "a.feature"},
        {"total": 1, "passed": 1, "failed": 0, "errors": 0, "junit_xml": "b.xml", "report_md": None, "bundles": [], "feature_file": "b.feature"},
    ]
    described = describe_run(summaries)
    assert described["total"] == 3
    assert described["passed"] == 2
    assert described["failed"] == 1
    assert described["errors"] == 0
    assert described["junit_xml"] == "b.xml"  # last summary wins
    assert described["report_md"] == "a.md"
    assert described["bundles"] == ["b1"]
    assert described["feature_files"] == ["a.feature", "b.feature"]


def test_execute_feature_file_success_records_hub_events(workspace, monkeypatch) -> None:
    import testzeus_hercules.__main__ as main_module

    async def fake_sequential() -> dict:
        get_notification_hub().notify("Step 1 completed via browser_nav_agent", "step_completed")
        return {"total": 1, "passed": 1, "failed": 0, "errors": 0, "junit_xml": "x.xml", "report_md": None, "bundles": [], "feature_file": "f.feature"}

    monkeypatch.setattr(main_module, "sequential_process", fake_sequential)

    feature = workspace / "ok.feature"
    feature.write_text("Feature: ok\n\n  Scenario: s\n    Given a\n")
    outcome = asyncio.run(execute_feature_file(str(feature)))

    assert outcome.success is True
    assert outcome.error is None
    assert outcome.summaries[0]["passed"] == 1
    assert outcome.duration_s >= 0
    assert outcome.progress_events == [{"message": "Step 1 completed via browser_nav_agent", "type": "step_completed"}]


def test_execute_feature_file_failed_run(workspace, monkeypatch) -> None:
    import testzeus_hercules.__main__ as main_module

    async def fake_sequential() -> dict:
        return {"total": 1, "passed": 0, "failed": 1, "errors": 0, "junit_xml": "x.xml", "report_md": None, "bundles": [], "feature_file": "f.feature"}

    monkeypatch.setattr(main_module, "sequential_process", fake_sequential)
    feature = workspace / "fail.feature"
    feature.write_text("Feature: fail\n\n  Scenario: s\n    Given a\n")
    outcome = asyncio.run(execute_feature_file(str(feature)))
    assert outcome.success is False
    assert outcome.error is None


def test_execute_feature_file_converts_no_scenario_system_exit(workspace, monkeypatch) -> None:
    import testzeus_hercules.__main__ as main_module

    async def fake_sequential() -> dict:
        raise SystemExit(1)  # the no-scenarios configuration error

    monkeypatch.setattr(main_module, "sequential_process", fake_sequential)
    feature = workspace / "empty.feature"
    feature.write_text("Feature: empty\n")
    outcome = asyncio.run(execute_feature_file(str(feature)))
    assert outcome.success is False
    assert outcome.error is not None and "Scenario" in outcome.error
    assert outcome.summaries == []


def test_execute_feature_file_reraises_other_system_exits(workspace, monkeypatch) -> None:
    import testzeus_hercules.__main__ as main_module

    async def fake_sequential() -> dict:
        raise SystemExit(2)

    monkeypatch.setattr(main_module, "sequential_process", fake_sequential)
    feature = workspace / "x.feature"
    feature.write_text("Feature: x\n")
    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(execute_feature_file(str(feature)))
    assert exc_info.value.code == 2


def test_execute_feature_file_catches_generic_exception(workspace, monkeypatch) -> None:
    import testzeus_hercules.__main__ as main_module

    async def fake_sequential() -> dict:
        raise RuntimeError("browser exploded")

    monkeypatch.setattr(main_module, "sequential_process", fake_sequential)
    feature = workspace / "x.feature"
    feature.write_text("Feature: x\n")
    outcome = asyncio.run(execute_feature_file(str(feature)))
    assert outcome.success is False
    assert "browser exploded" in (outcome.error or "")


def test_execute_feature_file_removes_listener(workspace, monkeypatch) -> None:
    import testzeus_hercules.__main__ as main_module

    async def fake_sequential() -> dict:
        return {"total": 0, "passed": 0, "failed": 0, "errors": 0}

    monkeypatch.setattr(main_module, "sequential_process", fake_sequential)
    feature = workspace / "x.feature"
    feature.write_text("Feature: x\n")

    hub = get_notification_hub()
    before = len(hub.listeners)
    asyncio.run(execute_feature_file(str(feature)))
    assert len(hub.listeners) == before


def test_execute_feature_file_bounded_events_and_sink(workspace, monkeypatch) -> None:
    """The event buffer is capped at 500; progress_sink still sees every event."""
    import testzeus_hercules.__main__ as main_module
    from testzeus_hercules.orchestrator import runner_bridge

    async def fake_sequential() -> dict:
        hub = get_notification_hub()
        for index in range(510):
            hub.notify(f"event {index}", "tick")
        return {"total": 0, "passed": 0, "failed": 0, "errors": 0}

    monkeypatch.setattr(main_module, "sequential_process", fake_sequential)

    sunk: list[dict] = []
    feature = workspace / "x.feature"
    feature.write_text("Feature: x\n")
    outcome = asyncio.run(execute_feature_file(str(feature), progress_sink=sunk.append))

    assert len(outcome.progress_events) == runner_bridge.MAX_PROGRESS_EVENTS == 500
    assert len(sunk) == 510  # sink sees everything; only the buffer is bounded


def test_progress_events_keep_newest_and_count_all_steps(workspace, monkeypatch) -> None:
    """Regression: the buffer keeps the NEWEST 500 events and steps_completed counts the WHOLE run."""
    import testzeus_hercules.__main__ as main_module
    from testzeus_hercules.orchestrator import runner_bridge

    async def fake_sequential() -> dict:
        hub = get_notification_hub()
        for index in range(600):
            hub.notify(f"step {index}", "step_completed")
        return {"total": 0, "passed": 0, "failed": 0, "errors": 0}

    monkeypatch.setattr(main_module, "sequential_process", fake_sequential)

    sunk: list[dict] = []
    feature = workspace / "x.feature"
    feature.write_text("Feature: x\n")
    outcome = asyncio.run(execute_feature_file(str(feature), progress_sink=sunk.append))

    assert len(outcome.progress_events) == runner_bridge.MAX_PROGRESS_EVENTS == 500
    assert outcome.progress_events[-1]["message"] == "step 599"  # newest event present
    assert outcome.progress_events[0]["message"] == "step 100"  # oldest events trimmed
    assert outcome.steps_completed == 600  # counter is NOT capped by the buffer
    assert len(sunk) == 600  # sink still sees everything


def test_prd_and_plan_models_serialize() -> None:
    """Sanity: the PRD/plan models used across MCP tests behave as expected."""
    prd = NormalizedPRD(
        project_name="Demo",
        requirements=[PRDRequirement(id="REQ-001", title="T", description="D", acceptance_criteria=["a"], source="s")],
    )
    assert prd.requirements[0].id == "REQ-001"
    assert make_plan().test_cases[0].id == "TC001"


def test_plan_approval_hash_roundtrips_and_defaults_empty(workspace) -> None:
    from testzeus_hercules.orchestrator.session import SessionState

    state = SessionState(session_id="s1", app_url="https://app.example.com", plan_approved=True, plan_approved_sha256="ab" * 32)
    assert SessionState.from_dict(state.to_dict()).plan_approved_sha256 == "ab" * 32
    legacy = state.to_dict()
    del legacy["plan_approved_sha256"]
    assert SessionState.from_dict(legacy).plan_approved_sha256 == ""
