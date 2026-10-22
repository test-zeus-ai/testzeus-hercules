"""Async-safety regression tests for the Hercules MCP server.

Covers the hardening fixes: serialized in-process runs (``_EXEC_LOCK``) with
unique per-run feature files, execution/ping deadlines
(``HERCULES_MCP_EXEC_TIMEOUT``), run_id traversal guard, loopback default bind
host, verified TLS probes, and tolerant output-dir enumeration.

Everything is offline: ``execute_feature_file`` and the LLM are stubbed, no
real runs happen.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import threading
from pathlib import Path

import httpx
import pytest
import testzeus_hercules.mcp_server as mcp_server
from testzeus_hercules.orchestrator.runner_bridge import ExecutionOutcome
from testzeus_hercules.orchestrator.session import SessionManager

GHERKIN = "Feature: G\n\n  Scenario: s\n    Given a\n"

JUNIT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="demo.feature" tests="1" failures="0" errors="0" time="0.5">
    <testcase name="passing scenario" classname="demo.feature" time="0.5"/>
  </testsuite>
</testsuites>
"""


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Isolate cwd + session manager so _state_dir/_result_dir land in tmp."""
    monkeypatch.delenv("TESTZEUS_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    manager = SessionManager()
    monkeypatch.setattr(mcp_server, "get_session_manager", lambda: manager)
    return tmp_path, manager


def run(coro):
    return asyncio.run(coro)


def _fake_outcome() -> ExecutionOutcome:
    return ExecutionOutcome(
        summaries=[{"total": 1, "passed": 1, "failed": 0, "errors": 0}],
        success=True,
        duration_s=0.1,
        progress_events=[],
    )


class _ConcurrencyTracker:
    """Stub ``execute_feature_file`` recording overlap and the feature paths it saw."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.paths: list[str] = []

    async def execute(self, feature_path: str, progress_sink=None) -> ExecutionOutcome:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.paths.append(feature_path)
        await asyncio.sleep(0.05)
        with self._lock:
            self.active -= 1
        return _fake_outcome()


def _make_run(tmp_path: Path, name: str) -> Path:
    run_dir = tmp_path / "opt" / "output" / name
    run_dir.mkdir(parents=True)
    (run_dir / "demo_result.xml").write_text(JUNIT_XML, encoding="utf-8")
    return run_dir


# ── finding 1: concurrent runs are serialized with distinct feature files ─────


def test_concurrent_generate_and_execute_serialized_with_distinct_files(workspace, monkeypatch) -> None:
    _tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    tracker = _ConcurrencyTracker()
    monkeypatch.setattr(mcp_server, "execute_feature_file", tracker.execute)

    async def gather_two():
        return await asyncio.gather(
            mcp_server.hercules_generate_and_execute(session_id=state.session_id, gherkin=GHERKIN),
            mcp_server.hercules_generate_and_execute(session_id=state.session_id, gherkin=GHERKIN),
        )

    results = run(gather_two())

    assert tracker.max_active == 1, "concurrent runs must be serialized by _EXEC_LOCK"
    assert len(tracker.paths) == 2
    assert len(set(tracker.paths)) == 2, "each run must write its own feature file"
    input_dir = Path(mcp_server._hercules_root()) / "opt" / "input"
    for path in tracker.paths:
        name = os.path.basename(path)
        assert name.startswith("hercules_mcp_run_") and name.endswith(".feature")
        assert os.path.dirname(path) == str(input_dir)
        assert os.path.isfile(path)
    for result in results:
        assert "error" not in result
        assert result["success"] is True


def test_concurrent_reruns_serialized(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    last_feature = tmp_path / "opt" / "input" / "last.feature"
    last_feature.parent.mkdir(parents=True, exist_ok=True)
    last_feature.write_text(GHERKIN, encoding="utf-8")
    state.last_run_summaries.append({"feature_file": str(last_feature)})
    tracker = _ConcurrencyTracker()
    monkeypatch.setattr(mcp_server, "execute_feature_file", tracker.execute)

    async def gather_two():
        return await asyncio.gather(
            mcp_server.hercules_rerun(session_id=state.session_id),
            mcp_server.hercules_rerun(session_id=state.session_id),
        )

    results = run(gather_two())

    assert tracker.max_active == 1, "concurrent reruns must be serialized by _EXEC_LOCK"
    for result in results:
        assert "error" not in result
        assert result["source"] == "last_run"


# ── finding 4: deadlines ──────────────────────────────────────────────────────


def test_exec_timeout_env_parsing(monkeypatch) -> None:
    monkeypatch.delenv("HERCULES_MCP_EXEC_TIMEOUT", raising=False)
    assert mcp_server._exec_timeout() == mcp_server._DEFAULT_EXEC_TIMEOUT_S == 1800.0
    monkeypatch.setenv("HERCULES_MCP_EXEC_TIMEOUT", "12.5")
    assert mcp_server._exec_timeout() == 12.5
    monkeypatch.setenv("HERCULES_MCP_EXEC_TIMEOUT", "not-a-number")
    assert mcp_server._exec_timeout() == 1800.0


def test_generate_and_execute_timeout_returns_error_dict(workspace, monkeypatch) -> None:
    _tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    monkeypatch.setenv("HERCULES_MCP_EXEC_TIMEOUT", "0.05")

    async def slow_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        await asyncio.sleep(5)
        return _fake_outcome()  # pragma: no cover - cancelled before this

    monkeypatch.setattr(mcp_server, "execute_feature_file", slow_execute)
    result = run(mcp_server.hercules_generate_and_execute(session_id=state.session_id, gherkin=GHERKIN))
    assert "error" in result
    assert "timed out" in result["error"]
    assert result["timeout_s"] == 0.05


def test_rerun_timeout_returns_error_dict(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    state = manager.create_session("https://app.example.com")
    last_feature = tmp_path / "opt" / "input" / "last.feature"
    last_feature.parent.mkdir(parents=True, exist_ok=True)
    last_feature.write_text(GHERKIN, encoding="utf-8")
    state.last_run_summaries.append({"feature_file": str(last_feature)})
    monkeypatch.setenv("HERCULES_MCP_EXEC_TIMEOUT", "0.05")

    async def slow_execute(feature_path: str, progress_sink=None) -> ExecutionOutcome:
        await asyncio.sleep(5)
        return _fake_outcome()  # pragma: no cover - cancelled before this

    monkeypatch.setattr(mcp_server, "execute_feature_file", slow_execute)
    result = run(mcp_server.hercules_rerun(session_id=state.session_id))
    assert "error" in result
    assert "timed out" in result["error"]
    assert result["feature_file"] == os.path.join("opt", "input", "last.feature")  # display path


def test_check_config_ping_timeout_returns_check_dict(workspace, monkeypatch) -> None:
    _tmp_path, _manager = workspace
    monkeypatch.setattr(mcp_server, "_MODEL_PING_TIMEOUT_S", 0.05)

    class _SlowModel:
        async def ainvoke(self, prompt: str) -> str:
            await asyncio.sleep(5)
            return "OK"  # pragma: no cover - cancelled before this

    def fake_model(agent_name: str = "planner_agent"):
        return _SlowModel()

    monkeypatch.setattr(mcp_server, "get_litellm_chat_model", fake_model)
    result = run(mcp_server.hercules_check_config(ping_model=True))
    ping = result["checks"]["model_ping"]
    assert ping["ok"] is False
    assert "timed out" in ping["error"]


# ── finding 2: code summary runs off the event loop ───────────────────────────


def test_generate_code_summary_offloads_to_thread(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace
    project = tmp_path / "proj"
    project.mkdir()
    state = manager.create_session("https://app.example.com", project_path=str(project))

    main_thread = threading.get_ident()
    seen_threads: list[int] = []

    def fake_generate(path: str) -> dict:
        seen_threads.append(threading.get_ident())
        return {"frameworks": [], "languages": {"py": 1}, "entry_points": [], "routes": [], "total_files": 1}

    def fake_save(summary: dict, out_path: str) -> None:
        seen_threads.append(threading.get_ident())
        Path(out_path).write_text("{}", encoding="utf-8")

    monkeypatch.setattr(mcp_server, "generate_code_summary", fake_generate)
    monkeypatch.setattr(mcp_server, "save_summary", fake_save)

    result = run(mcp_server.hercules_generate_code_summary(session_id=state.session_id))
    assert "error" not in result
    assert seen_threads
    assert all(tid != main_thread for tid in seen_threads), "scan and save must run off the event loop"


# ── finding 6: loopback bind host by default ──────────────────────────────────


def test_mcp_host_defaults_to_loopback(monkeypatch) -> None:
    monkeypatch.delenv("MCP_HOST", raising=False)
    assert mcp_server._mcp_host() == "127.0.0.1"
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    assert mcp_server._mcp_host() == "0.0.0.0"


# ── finding 7: run_id traversal guard ─────────────────────────────────────────


def test_get_run_results_rejects_unsafe_run_ids(workspace) -> None:
    tmp_path, _manager = workspace
    _make_run(tmp_path, "run_ok")
    for bad in ("..", ".", "../escape", "a/b", "with space"):
        result = run(mcp_server.hercules_get_run_results(run_id=bad))
        assert "error" in result, bad
        assert "Invalid run_id" in result["error"], bad


def test_get_run_results_accepts_safe_run_ids(workspace) -> None:
    tmp_path, _manager = workspace
    _make_run(tmp_path, "run_20260609_132411")
    result = run(mcp_server.hercules_get_run_results(run_id="run_20260609_132411"))
    assert "error" not in result
    assert result["run_id"] == "run_20260609_132411"
    assert result["passed"] == 1


# ── finding 8: TLS verification stays on ──────────────────────────────────────


def test_check_app_url_keeps_tls_verification_enabled(monkeypatch) -> None:
    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def head(self, url):
            raise httpx.ConnectError("offline")

        async def get(self, url):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(mcp_server.httpx, "AsyncClient", _FakeClient)
    assert run(mcp_server._check_app_url("https://app.example.com")) is False
    assert captured.get("verify") is not False


# ── finding 9: tolerant output-dir enumeration ────────────────────────────────


def test_list_subdirs_skips_vanished_entries(tmp_path, monkeypatch) -> None:
    good = tmp_path / "run_ok"
    good.mkdir()
    ghost = tmp_path / "run_ghost"
    ghost.mkdir()

    real_is_dir = pathlib.Path.is_dir

    def flaky_is_dir(self):
        if self.name == "run_ghost":
            raise FileNotFoundError(f"vanished: {self}")
        return real_is_dir(self)

    monkeypatch.setattr(pathlib.Path, "is_dir", flaky_is_dir)
    assert mcp_server._list_subdirs(tmp_path) == [good]


def test_list_subdirs_missing_directory_is_empty(tmp_path) -> None:
    assert mcp_server._list_subdirs(tmp_path / "missing") == []


def test_sorted_newest_first_tolerates_vanished_stat(tmp_path, monkeypatch) -> None:
    old = tmp_path / "run_old"
    new = tmp_path / "run_new"
    old.mkdir()
    new.mkdir()
    os.utime(old, (1_000_000_000, 1_000_000_000))
    os.utime(new, (2_000_000_000, 2_000_000_000))

    real_stat = pathlib.Path.stat

    def flaky_stat(self, **kwargs):
        if self.name == "run_new":
            raise FileNotFoundError(f"vanished: {self}")
        return real_stat(self, **kwargs)

    monkeypatch.setattr(pathlib.Path, "stat", flaky_stat)
    sorted_paths = mcp_server._sorted_newest_first([old, new])
    assert sorted_paths == [old, new]  # vanished run sorts last instead of raising


def test_get_run_results_survives_entries_vanishing_mid_enumeration(workspace, monkeypatch) -> None:
    tmp_path, _manager = workspace
    _make_run(tmp_path, "run_ok")
    ghost = tmp_path / "opt" / "output" / "run_ghost"
    ghost.mkdir()

    real_is_dir = pathlib.Path.is_dir

    def flaky_is_dir(self):
        if self.name == "run_ghost":
            raise FileNotFoundError(f"vanished: {self}")
        return real_is_dir(self)

    monkeypatch.setattr(pathlib.Path, "is_dir", flaky_is_dir)
    result = run(mcp_server.hercules_get_run_results())
    assert "error" not in result
    assert result["run_id"] == "run_ok"
