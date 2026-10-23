"""Regression tests for the confirmed #158 platform bugs (TES-1939).

Offline: LLM calls, network and Hercules runs are stubbed; the signal tests use
real (trivial) subprocesses.
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import pathlib
import signal
import subprocess
import sys
import textwrap
import time

import httpx
import pytest
import testzeus_hercules.mcp_server as mcp_server
import testzeus_hercules.orchestrator.scheduler as scheduler_module
from testzeus_hercules.dashboard.app import create_app
from testzeus_hercules.orchestrator.session import SessionManager
from testzeus_hercules.utils import url_guard
from testzeus_hercules.utils.path_guard import ForbiddenFileError, PathOutsideRootsError, check_prd_document, is_within, resolve_within
from testzeus_hercules.utils.redact import redact_webhook_text, redact_webhook_url

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

JUNIT_ONE = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites><testsuite name="{name}" tests="1" failures="0" errors="0" time="0.5">
<testcase name="{case}" classname="{name}" time="0.5"/></testsuite></testsuites>
"""


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("TESTZEUS_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    manager = SessionManager()
    monkeypatch.setattr(mcp_server, "get_session_manager", lambda: manager)
    return tmp_path, manager


# ── 1. hercules_get_run_results: html must belong to the chosen xml ───────────


def _shared_run_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    run_dir = tmp_path / "opt" / "output" / "run_20260101_000000"
    run_dir.mkdir(parents=True)
    # two runs share the folder; "aaa" (older) sorts first alphabetically, "zzz" is newest
    (run_dir / "aaa_result.xml").write_text(JUNIT_ONE.format(name="aaa.feature", case="old case"))
    (run_dir / "aaa_result.html").write_text("<html>old</html>")
    (run_dir / "zzz_result.xml").write_text(JUNIT_ONE.format(name="zzz.feature", case="new case"))
    (run_dir / "zzz_result.html").write_text("<html>new</html>")
    os.utime(run_dir / "aaa_result.xml", (1_000, 1_000))
    os.utime(run_dir / "zzz_result.xml", (2_000, 2_000))
    return run_dir


def test_run_results_html_pairs_with_newest_xml(workspace) -> None:
    tmp_path, _ = workspace
    run_dir = _shared_run_dir(tmp_path)
    result = run(mcp_server.hercules_get_run_results())
    assert result["junit_xml"].endswith("zzz_result.xml")
    assert result["html"].endswith("zzz_result.html"), "html must be the report of the same run as the xml"
    assert result["test_cases"][0]["name"] == "new case"
    assert result["result_id"] == "zzz_result"
    assert result["other_results"] == ["aaa_result"]
    assert run_dir.name == result["run_id"]


def test_run_results_select_older_result_in_shared_folder(workspace) -> None:
    tmp_path, _ = workspace
    _shared_run_dir(tmp_path)
    result = run(mcp_server.hercules_get_run_results(run_id="run_20260101_000000", result_id="aaa_result"))
    assert result["junit_xml"].endswith("aaa_result.xml")
    assert result["html"].endswith("aaa_result.html")
    assert result["test_cases"][0]["name"] == "old case"
    missing = run(mcp_server.hercules_get_run_results(run_id="run_20260101_000000", result_id="nope"))
    assert "not found" in missing["error"]
    assert "invalid" in run(mcp_server.hercules_get_run_results(run_id="run_20260101_000000", result_id="../x"))["error"].lower()


def test_open_dashboard_html_follows_newest_xml(workspace) -> None:
    tmp_path, _ = workspace
    _shared_run_dir(tmp_path)
    result = run(mcp_server.hercules_open_dashboard())
    assert result["report_url"].endswith("zzz_result.html")


# ── 2. orphan-proof signal handling ───────────────────────────────────────────

FEATURE = "Feature: demo\n\n  Scenario: s1\n    Given a step\n"

CHILD_CODE = textwrap.dedent("""
    import os, subprocess, sys, time
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    with open("child.pids", "w") as handle:
        handle.write(f"{os.getpid()} {grandchild.pid}")
    time.sleep(300)
    """)


def _runner_script(entry: str) -> str:
    return textwrap.dedent(f"""
        import sys
        import testzeus_hercules.orchestrator.run_manager as rm
        CHILD = {CHILD_CODE!r}
        rm._subprocess_command = lambda workspace: [sys.executable, "-c", CHILD]
        """) + textwrap.dedent(entry)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie that nobody reaped still answers kill(0); count it as dead
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _wait_for_pids(tmp_path: pathlib.Path, timeout: float = 30.0) -> tuple[int, int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for path in glob.glob(str(tmp_path / ".hercules" / "**" / "child.pids"), recursive=True):
            parts = pathlib.Path(path).read_text().split()
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
        time.sleep(0.1)
    raise AssertionError("scenario subprocess did not start")


def _run_and_signal(tmp_path: pathlib.Path, entry: str, sig: int) -> tuple[int, str]:
    script = tmp_path / "runner.py"
    script.write_text(_runner_script(entry))
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHONWARNINGS="ignore")
    proc = subprocess.Popen([sys.executable, str(script)], cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    child_pid = grandchild_pid = 0
    try:
        child_pid, grandchild_pid = _wait_for_pids(tmp_path)
        proc.send_signal(sig)
        _out, err = proc.communicate(timeout=30)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and (_alive(child_pid) or _alive(grandchild_pid)):
            time.sleep(0.1)
        assert not _alive(child_pid), "scenario subprocess survived the runner"
        assert not _alive(grandchild_pid), "scenario's child (browser stand-in) survived the runner"
        return proc.returncode, err
    finally:
        for pid in (child_pid, grandchild_pid):
            if pid and _alive(pid):
                os.kill(pid, signal.SIGKILL)
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("sig,expected", [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_run_manager_signal_kills_scenario_process_tree(tmp_path, sig, expected) -> None:
    (tmp_path / "demo.feature").write_text(FEATURE)
    entry = 'raise SystemExit(rm.main(["demo.feature", "--timeout", "120"]))\n'
    code, err = _run_and_signal(tmp_path, entry, sig)
    assert code == expected
    assert "Traceback" not in err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("sig,expected", [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_suite_cli_signal_kills_scenario_process_tree(tmp_path, sig, expected) -> None:
    (tmp_path / "demo.feature").write_text(FEATURE)
    (tmp_path / "suites.yaml").write_text("s:\n  tests: [demo.feature]\n")
    entry = 'import testzeus_hercules.orchestrator.suites as su\nraise SystemExit(su.main(["s", "--config", "suites.yaml"]))\n'
    code, err = _run_and_signal(tmp_path, entry, sig)
    assert code == expected
    assert "Traceback" not in err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_monitor_signal_exits_cleanly_and_kills_scenario_tree(tmp_path, sig) -> None:
    (tmp_path / "demo.feature").write_text(FEATURE)
    (tmp_path / "suites.yaml").write_text("s:\n  tests: [demo.feature]\n")
    (tmp_path / "monitor.yaml").write_text(f"suites: [s]\nconfig: {tmp_path}/suites.yaml\nstate_file: {tmp_path}/state.json\n")
    entry = 'import testzeus_hercules.orchestrator.scheduler as sc\nraise SystemExit(sc.main(["--config", "monitor.yaml"]))\n'
    code, err = _run_and_signal(tmp_path, entry, sig)
    assert code == 0
    assert "Traceback" not in err


def test_run_manager_missing_feature_file_is_clean_exit_2(tmp_path, capsys) -> None:
    from testzeus_hercules.orchestrator import run_manager

    assert run_manager.main([str(tmp_path / "ghost.feature")]) == 2
    err = capsys.readouterr().err
    assert "not found" in err.lower()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_terminate_live_children_kills_registered_group() -> None:
    from testzeus_hercules.orchestrator import run_manager

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"], start_new_session=True)
    run_manager._LIVE_GROUPS.add(proc.pid)
    try:
        assert run_manager.terminate_live_children(grace_s=5.0) == 1
        assert proc.wait(timeout=10) is not None
        assert not run_manager._LIVE_GROUPS
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_terminate_process_tree_kills_term_ignoring_grandchild_after_leader_exits(tmp_path) -> None:
    """The leader exits on SIGTERM (and is reaped) but a descendant ignores it: the group SIGKILL must still fire."""
    from testzeus_hercules.orchestrator import run_manager

    pid_file = tmp_path / "gc.pid"
    script = f'(trap "" TERM; exec sleep 300) & echo $! > {pid_file}; trap "exit 0" TERM; while true; do sleep 0.2; done'

    async def scenario() -> int:
        proc = await asyncio.create_subprocess_exec("sh", "-c", script, start_new_session=True)
        for _ in range(100):
            if pid_file.exists() and pid_file.read_text().strip():
                break
            await asyncio.sleep(0.05)
        grandchild = int(pid_file.read_text())
        await run_manager._terminate_process_tree(proc, grace_s=0.5)
        return grandchild

    grandchild = asyncio.run(scenario())
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(grandchild):
            time.sleep(0.1)
        assert not _alive(grandchild), "TERM-ignoring grandchild survived the group kill"
    finally:
        if _alive(grandchild):
            os.kill(grandchild, signal.SIGKILL)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_second_cancel_during_grace_still_kills_the_group_and_keeps_it_registered(tmp_path) -> None:
    """A second signal cancels the graceful shutdown mid-grace: the SIGKILL must not be skipped."""
    from testzeus_hercules.orchestrator import run_manager

    pid_file = tmp_path / "child.pid"
    script = f'trap "" TERM; echo $$ > {pid_file}; while true; do sleep 0.2; done'

    async def scenario() -> tuple[int, bool]:
        proc = await asyncio.create_subprocess_exec("sh", "-c", script, start_new_session=True)
        run_manager._LIVE_GROUPS.add(proc.pid)
        for _ in range(100):
            if pid_file.exists() and pid_file.read_text().strip():
                break
            await asyncio.sleep(0.05)
        task = asyncio.ensure_future(run_manager._terminate_process_tree(proc, grace_s=30.0))
        await asyncio.sleep(0.3)  # inside the grace period, SIGTERM already ignored
        task.cancel()  # the second signal
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.3)
        return proc.pid, proc.pid in run_manager._LIVE_GROUPS

    try:
        pid, registered = asyncio.run(scenario())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.1)
        assert not _alive(pid), "TERM-ignoring child survived the second signal"
        assert registered, "pid must stay in _LIVE_GROUPS until the run_cli safety net confirms the group is gone"
    finally:
        run_manager._LIVE_GROUPS.clear()
        if pid_file.exists():
            try:
                os.killpg(int(pid_file.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass


# ── 3. webhook errors never log the secret URL ────────────────────────────────

SLACK = "https://hooks.slack.com/services/T0001/B0002/SECRETSECRET?token=querysecret"


def test_redact_webhook_url_keeps_only_origin_and_masked_path() -> None:
    assert redact_webhook_url(SLACK) == "https://hooks.slack.com/[REDACTED]"
    assert redact_webhook_url("https://user:pw@example.com:8443/") == "https://example.com:8443"
    assert redact_webhook_url("not a url") == "[unparsable-url]"
    text = redact_webhook_text(f"Client error '404 Not Found' for url '{SLACK}'", SLACK)
    assert "SECRET" not in text and "querysecret" not in text and "hooks.slack.com" in text


def test_webhook_failure_and_success_logs_do_not_leak_secrets(tmp_path, monkeypatch, caplog) -> None:
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text("s:\n  tests: [x.feature]\n")
    cfg_path = tmp_path / "monitor.yaml"
    cfg_path.write_text(f"suites: [s]\nconfig: {suites_yaml}\nnotify_on: always\nwebhook_url: {SLACK}\nstate_file: {tmp_path}/state.json\n")
    cfg = scheduler_module.load_monitor_config(str(cfg_path))

    async def fake_suites(*args, **kwargs):
        return {"suite": "s", "total": 1, "passed": 1, "failed": 0, "errors": 0, "retries_used": 0, "per_scenario": [], "merged_xml": None, "workroot": "ws"}

    async def failing_post(url, payload):
        request = httpx.Request("POST", url)
        raise httpx.HTTPStatusError(f"Client error '404 Not Found' for url '{url}'", request=request, response=httpx.Response(404, request=request))

    async def ok_post(url, payload):
        return None

    monkeypatch.setattr(scheduler_module.suites, "run_suite", fake_suites)
    with caplog.at_level(logging.DEBUG):
        monkeypatch.setattr(scheduler_module, "_post_webhook", failing_post)
        run(scheduler_module.run_monitor_once(cfg, scheduler_module.load_state(cfg["state_file"])))
        monkeypatch.setattr(scheduler_module, "_post_webhook", ok_post)
        run(scheduler_module.run_monitor_once(cfg, scheduler_module.load_state(cfg["state_file"])))
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Webhook POST to" in logged and "Webhook notification sent to" in logged
    for secret in ("SECRETSECRET", "querysecret", "T0001", "B0002"):
        assert secret not in logged


# ── 4. prd_path URL credentials / tokens ──────────────────────────────────────


def test_configure_session_rejects_prd_url_with_userinfo(workspace) -> None:
    _tmp, _manager = workspace
    result = run(mcp_server.hercules_configure_session("https://app.example.com", prd_path="https://alice:hunter2@docs.example.com/prd.md"))
    assert "error" in result and "credentials" in result["error"]
    assert "hunter2" not in json.dumps(result)


def test_prd_url_token_not_persisted_or_echoed_but_kept_in_memory(workspace, monkeypatch) -> None:
    tmp_path, manager = workspace

    async def fake_probe(url, timeout=5.0):
        return True

    monkeypatch.setattr(mcp_server, "_check_app_url", fake_probe)
    result = run(mcp_server.hercules_configure_session("https://app.example.com", prd_path="HTTPS://docs.example.com/prd.md?token=topsecret&page=1"))
    assert "topsecret" not in json.dumps(result)
    assert result["prd_path"].startswith("https://docs.example.com/prd.md?token=[REDACTED]")
    persisted = (tmp_path / ".hercules" / "sessions.json").read_text()
    assert "topsecret" not in persisted
    state = manager.resolve_session(result["session_id"])
    assert state.prd_path == "https://docs.example.com/prd.md?token=topsecret&page=1"  # fetch still works in-memory (scheme lowercased)
    assert "topsecret" not in repr(state)


def test_build_standard_prd_refuses_userinfo_url(workspace) -> None:
    _tmp, manager = workspace
    state = manager.create_session("https://app.example.com")
    result = run(mcp_server.hercules_generate_standardized_prd(session_id=state.session_id, prd_path="  https://bob:pw@docs.example.com/prd.md "))
    assert "credentials" in result["error"]
    assert "pw@" not in json.dumps(result)


def test_prd_url_detection_is_case_insensitive_and_stripped(workspace, monkeypatch) -> None:
    _tmp, manager = workspace
    state = manager.create_session("https://app.example.com")
    seen: list[str] = []

    async def fake_build(source, *args, **kwargs):
        seen.append(source)
        raise ValueError("stop here")

    monkeypatch.setattr(mcp_server, "build_normalized_prd", fake_build)
    run(mcp_server.hercules_generate_standardized_prd(session_id=state.session_id, prd_path="  HTTP://docs.example.com/prd.md  "))
    assert seen == ["http://docs.example.com/prd.md"]  # treated as a URL, not confined as a local path


# ── 5. dashboard planner thoughts confined to the project root ────────────────


def test_dashboard_planner_text_ignores_paths_outside_root(tmp_path) -> None:
    from testzeus_hercules.dashboard import app as dashboard

    root = tmp_path / "proj"
    (root / "proofs").mkdir(parents=True)
    outside = tmp_path / "secret_planner_thoughts.txt"
    outside.write_text("TOP SECRET OUTSIDE")
    inside = root / "proofs" / "planner_thoughts.txt"
    inside.write_text("inside thoughts")
    link = root / "proofs" / "link_planner.txt"
    link.symlink_to(outside)

    def case_for(path) -> dict:
        return {"artifacts": [{"kind": "log", "path": str(path)}]}

    assert dashboard._planner_text(case_for(inside), str(root)) == "inside thoughts"
    assert dashboard._planner_text(case_for(outside), str(root)) is None
    assert dashboard._planner_text(case_for(link), str(root)) is None
    assert dashboard._planner_text(case_for(root / ".." / "secret_planner_thoughts.txt"), str(root)) is None


def test_dashboard_case_page_does_not_render_files_outside_root(tmp_path) -> None:
    from starlette.testclient import TestClient

    outside = tmp_path / "secret_planner_thoughts.txt"
    outside.write_text("TOP SECRET OUTSIDE")
    root = tmp_path / "proj"
    run_dir = root / "output" / "run_20260101_000000"
    run_dir.mkdir(parents=True)
    xml = f"""<?xml version="1.0"?>
<testsuites><testsuite name="Feature: demo" tests="1" failures="1" skipped="0" time="0.1">
<testcase name="Scenario: demo" classname="Feature: demo" time="0.1">
<properties><property name="Planner Thoughts" value="{outside}"/></properties>
<failure message="boom" type="AssertionError"></failure>
</testcase></testsuite></testsuites>"""
    (run_dir / "final_result.xml").write_text(xml)
    app = create_app(db_url=f"sqlite:///{tmp_path / 'h.db'}", project_root=str(root))
    with TestClient(app) as client:
        client.post("/import", headers={"Sec-Fetch-Site": "same-origin"})
        runs = client.get("/api/runs").json()
        assert runs
        cases = client.get(f"/run/{runs[0]['run_id']}")
        assert "TOP SECRET OUTSIDE" not in cases.text
        page = client.get(f"/case/{runs[0]['run_id']}/Scenario: demo")
        assert page.status_code == 200, page.text
        assert "Planner thoughts" in page.text and "TOP SECRET OUTSIDE" not in page.text


# ── 6. NUL bytes and over-long names ──────────────────────────────────────────


def test_path_guard_nul_byte_is_a_clean_refusal(tmp_path) -> None:
    with pytest.raises(PathOutsideRootsError):
        resolve_within(str(tmp_path / "a\x00b"), [str(tmp_path)])
    with pytest.raises(ForbiddenFileError):
        check_prd_document("prd\x00.md")
    assert is_within("x\x00y", str(tmp_path)) is False


def test_artifact_endpoint_nul_byte_is_4xx_not_500(tmp_path) -> None:
    from starlette.testclient import TestClient

    app = create_app(db_url=f"sqlite:///{tmp_path / 'h.db'}", project_root=str(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:
        for raw in ("%00", "x%00y", f"{tmp_path}/a%00.png"):
            response = client.get(f"/artifact?path={raw}")
            assert response.status_code in (400, 403, 404), (raw, response.status_code)
        assert client.get("/artifact", params={"path": str(tmp_path / ("a" * 400))}).status_code in (400, 403, 404)


def test_run_id_too_long_is_clean_error_without_host_path(workspace) -> None:
    tmp_path, _ = workspace
    (tmp_path / "opt" / "output").mkdir(parents=True)
    result = run(mcp_server.hercules_get_run_results(run_id="r" * 300))
    assert "Invalid run_id" in result["error"]
    assert str(tmp_path) not in result["error"] and len(result["error"]) < 400


# ── 7. no absolute host paths in tool results ─────────────────────────────────


def test_tool_results_do_not_leak_absolute_paths(workspace, monkeypatch) -> None:
    tmp_path, _ = workspace
    (tmp_path / "proj").mkdir()
    (tmp_path / "prd.md").write_text("# PRD")

    async def fake_probe(url, timeout=5.0):
        return True

    monkeypatch.setattr(mcp_server, "_check_app_url", fake_probe)
    monkeypatch.setattr(mcp_server, "get_litellm_chat_model", lambda name="planner_agent": object())
    configured = run(mcp_server.hercules_configure_session("https://app.example.com", project_path=str(tmp_path / "proj"), prd_path=str(tmp_path / "prd.md")))
    checked = run(mcp_server.hercules_check_config())
    _shared_run_dir(tmp_path)
    opened = run(mcp_server.hercules_open_dashboard())
    for result in (configured, checked, opened):
        assert str(tmp_path) not in json.dumps(result), result
    assert configured["project_path"] == "proj" and configured["prd_path"] == "prd.md"


def test_error_messages_scrub_the_root(workspace) -> None:
    tmp_path, _ = workspace
    scrubbed = mcp_server._error(f"boom at {tmp_path}/opt/output/x and {tmp_path}")["error"]
    assert str(tmp_path) not in scrubbed
    assert "opt/output/x" in scrubbed


def test_run_failure_message_scrubs_root(workspace) -> None:
    tmp_path, _ = workspace
    run_dir = tmp_path / "opt" / "output" / "run_1"
    run_dir.mkdir(parents=True)
    xml = f"""<?xml version="1.0"?><testsuites><testsuite name="d" tests="1" failures="1" errors="0" time="1">
<testcase name="c" classname="d" time="1"><failure message="File {tmp_path}/test_data/secret.csv missing"/></testcase></testsuite></testsuites>"""
    (run_dir / "d_result.xml").write_text(xml)
    result = run(mcp_server.hercules_get_run_results(run_id="run_1"))
    assert str(tmp_path) not in json.dumps(result)
    assert "test_data/secret.csv" in result["test_cases"][0]["failure_message"]


def test_runner_bridge_error_names_only_the_basename(workspace, monkeypatch) -> None:
    tmp_path, _ = workspace
    from testzeus_hercules.orchestrator import runner_bridge

    async def exits(*args, **kwargs):
        raise SystemExit(1)

    import testzeus_hercules.__main__ as hercules_main

    monkeypatch.setattr(hercules_main, "sequential_process", exits)
    outcome = run(runner_bridge.execute_feature_file(str(tmp_path / "opt" / "input" / "x.feature")))
    assert "x.feature" in outcome.error and str(tmp_path) not in outcome.error


# ── review follow-ups: PRD fetch errors, redact_url coverage, dashboard filter, path scrub ──


def test_prd_fetch_failure_message_has_no_url_token(monkeypatch) -> None:
    from testzeus_hercules.intelligence import prd_parser

    original = url_guard.fetch_text

    def failing(url, **kwargs):
        return original(url, transport=httpx.MockTransport(lambda request: httpx.Response(401, text="no")), resolver=lambda host, port: ["93.184.216.34"], **kwargs)

    monkeypatch.setattr(prd_parser, "fetch_text", failing)
    with pytest.raises(ValueError) as caught:
        prd_parser._fetch_url_text("https://example.com/prd.md?token=SEKRET123&x=1", {})
    message = str(caught.value)
    assert "SEKRET123" not in message and "93.184.216.34" not in message
    assert "401" in message


def test_prd_fetch_transport_error_message_has_no_url_token(monkeypatch) -> None:
    from testzeus_hercules.intelligence import prd_parser

    def failing(url, **kwargs):
        raise httpx.ConnectError(f"connect failed for {url}?token=SEKRET123")

    monkeypatch.setattr(prd_parser, "fetch_text", failing)
    with pytest.raises(ValueError) as caught:
        prd_parser._fetch_url_text("https://example.com/prd.md?token=SEKRET123", {})
    assert "SEKRET123" not in str(caught.value)
    assert "ConnectError" in str(caught.value)


@pytest.mark.parametrize(
    "url,secret",
    [
        ("https://h/f?X-Amz-Signature=SECRETVAL&ok=1", "SECRETVAL"),
        ("https://h/f?X-Amz-Credential=SECRETVAL", "SECRETVAL"),
        ("https://h/f?X-Amz-Security-Token=SECRETVAL", "SECRETVAL"),
        ("https://h/f?jwt=SECRETVAL", "SECRETVAL"),
        ("https://h/f?sig=SECRETVAL", "SECRETVAL"),
        ("https://h/f?access_key=SECRETVAL", "SECRETVAL"),
        ("https://h/f?pwd=SECRETVAL", "SECRETVAL"),
        ("https://h/f?sid=SECRETVAL&a=1", "SECRETVAL"),
        ("https://h/f?session=SECRETVAL", "SECRETVAL"),
        ("https://h/cb#access_token=SECRETVAL&state=1", "SECRETVAL"),
        ("https://h/f;token=SECRETVAL/x?a=1", "SECRETVAL"),
        ("https://h/f?a=1;token=SECRETVAL", "SECRETVAL"),
        ("https://u:p@[::1/f?token=SECRETVAL#access_token=SECRETVAL", "SECRETVAL"),
    ],
)
def test_redact_url_covers_signed_url_session_fragment_and_matrix_secrets(url, secret) -> None:
    from testzeus_hercules.utils.redact import redact_url

    assert secret not in redact_url(url)


def test_redact_url_keeps_non_secret_parts() -> None:
    from testzeus_hercules.utils.redact import redact_url

    assert redact_url("https://h/f?monitor=1&keyword=k#section-2") == "https://h/f?monitor=1&keyword=k#section-2"


def test_reloaded_redacted_prd_url_asks_for_the_url_again(workspace) -> None:
    _tmp, manager = workspace
    state = manager.create_session("https://app.example.com")
    state.prd_path = "https://docs.example.com/prd.md?token=[REDACTED]&page=1"  # what sessions.json holds after a restart
    result = run(mcp_server.hercules_generate_standardized_prd(session_id=state.session_id))
    assert "pass prd_path" in result["error"].lower()
    assert "redacted" in result["error"].lower()


def test_dashboard_artifact_resolver_applies_the_sensitive_file_filter(tmp_path) -> None:
    from testzeus_hercules.dashboard import app as dashboard

    root = tmp_path / "proj"
    (root / ".hercules").mkdir(parents=True)
    (root / "proofs").mkdir()
    (root / ".env").write_text("API_KEY=sekret")
    (root / ".hercules" / "sessions.json").write_text("{}")
    (root / "proofs" / "planner_thoughts.txt").write_text("fine")
    (root / "proofs" / ".planner_thoughts.txt").write_text("dotfile")
    for sensitive in (root / ".env", root / ".hercules" / "sessions.json", root / "proofs" / ".planner_thoughts.txt"):
        assert dashboard._resolve_artifact_file(str(sensitive), (), str(root)) is None
        assert dashboard._planner_text({"artifacts": [{"kind": "log", "path": str(sensitive)}]}, str(root)) is None
    assert dashboard._planner_text({"artifacts": [{"kind": "log", "path": str(root / "proofs" / "planner_thoughts.txt")}]}, str(root)) == "fine"
    assert dashboard._resolve_artifact_file(str(root / "proofs" / "planner_thoughts.txt"), (), str(root))


def test_scrub_paths_only_replaces_whole_path_prefixes(workspace, monkeypatch) -> None:
    monkeypatch.setenv("TESTZEUS_ROOT", "/app")
    scrubbed = mcp_server._scrub_paths("see /app/opt/x, /application/x, /home/u/app/x and /app.")
    assert "opt/x" in scrubbed and "/application/x" in scrubbed and "/home/u/app/x" in scrubbed
    assert "/app/opt" not in scrubbed
    assert scrubbed.endswith(" and ..")
    assert mcp_server._scrub_paths("root is /app") == "root is ."


# ── 8. url_guard: total deadline, NAT64 ───────────────────────────────────────


@pytest.mark.parametrize("address", ["64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe", "64:ff9b::a00:1", "2002:7f00:1::1", "::ffff:127.0.0.1"])
def test_nat64_and_6to4_wrapped_private_addresses_are_blocked(address) -> None:
    assert url_guard.is_public_address(address) is False


def test_nat64_wrapped_public_address_is_allowed() -> None:
    assert url_guard.is_public_address("64:ff9b::808:808") is True  # 8.8.8.8


def test_fetch_text_has_a_total_deadline_not_a_per_read_timeout() -> None:
    class Drip(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(50):
                time.sleep(0.1)
                yield b"x"

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Drip()))
    started = time.monotonic()
    with pytest.raises(httpx.HTTPError):
        url_guard.fetch_text("http://93.184.216.34/prd.md", timeout=0.5, resolver=lambda host, port: ["93.184.216.34"], transport=transport)
    assert time.monotonic() - started < 3.0
