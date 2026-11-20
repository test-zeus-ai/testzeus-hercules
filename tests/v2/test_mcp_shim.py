"""The ``hercules mcp`` stdio shim, driven by the official MCP client against a daemon started in the test."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ImageContent, ResourceLink, TextContent
from PIL import Image

from testzeus_hercules.daemon.discovery import read_daemon_file
from tests.v2.fixtures.daemon_support import case_dict, daemon_thread, isolated_env, make_config

REPO_ROOT = Path(__file__).resolve().parents[2]
HEAVY = ("starlette", "uvicorn", "playwright", "langgraph", "langchain", "langchain_core", "langchain_openai", "testzeus_hercules.engine", "testzeus_hercules.daemon.app")
TOOLS = {
    "hercules_run",
    "hercules_run_status",
    "hercules_run_result",
    "hercules_run_cancel",
    "hercules_approve",
    "hercules_rerun",
    "hercules_list_runs",
    "hercules_list_cases",
    "hercules_get_case",
    "hercules_validate_case",
    "hercules_create_case",
    "hercules_update_case",
    "hercules_delete_case",
    "hercules_list_folders",
    "hercules_get_folder",
    "hercules_create_folder",
    "hercules_update_folder",
    "hercules_delete_folder",
    "hercules_run_folder",
    "hercules_list_environments",
    "hercules_get_environment",
    "hercules_create_environment",
    "hercules_update_environment",
    "hercules_delete_environment",
    "hercules_get_evidence",
    "hercules_check_env",
    "hercules_quality_report",
    "hercules_export_code",
    "hercules_plan",
    "hercules_explore",
    "hercules_explore_status",
    "hercules_explore_stop",
    "hercules_explore_sessions",
    "hercules_explore_report",
    "hercules_explore_findings",
    "hercules_explore_finding",
    "hercules_explore_triage",
    "hercules_explore_issue",
    "hercules_explore_reverify",
}


# --------------------------------------------------------------------------- import isolation


def _import_probe(module: str) -> dict[str, Any]:
    code = (
        "import json, sys, time\n"
        "start, cpu = time.perf_counter(), time.process_time()\n"
        f"import {module}\n"
        "elapsed, cpu = time.perf_counter() - start, time.process_time() - cpu\n"
        f"heavy = sorted({{m for m in sys.modules for h in {HEAVY!r} if m == h or m.startswith(h + '.')}})\n"
        "print(json.dumps({'elapsed': elapsed, 'cpu': cpu, 'heavy': heavy}))\n"
    )
    completed = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60, check=True)
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_shim_import_is_light_and_fast() -> None:
    _import_probe("testzeus_hercules.mcp.shim")  # warm the bytecode cache
    probes = [_import_probe("testzeus_hercules.mcp.shim") for _ in range(3)]
    assert all(probe["heavy"] == [] for probe in probes)
    # CPU time: the budget is the shim's own work; wall time also counts other load on the machine.
    assert min(probe["cpu"] for probe in probes) < 0.5, probes
    assert min(probe["elapsed"] for probe in probes) < 1.5, probes


def test_full_mcp_entry_path_stays_light() -> None:
    probe = _import_probe("testzeus_hercules.cli.main, testzeus_hercules.mcp.shim")
    assert probe["heavy"] == []


def test_lazy_mcp_package_still_exposes_everything() -> None:
    code = "import testzeus_hercules.mcp.shim\nfrom mcp import ClientSession\nfrom mcp.server import FastMCP\nprint(ClientSession.__name__, FastMCP.__name__)"
    completed = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["ClientSession", "FastMCP"]


# --------------------------------------------------------------------------- stdio sessions


def _shim_params(env: dict[str, str], project: Path) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "testzeus_hercules.cli.main", "mcp"], env={**env, "PYTHONPATH": str(REPO_ROOT)}, cwd=str(project))


@asynccontextmanager
async def mcp_session(env: dict[str, str], project: Path) -> AsyncIterator[ClientSession]:
    async with stdio_client(_shim_params(env, project)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call(session: ClientSession, name: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
    return await session.call_tool(name, arguments, **kwargs)


def _text(result: Any) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


@pytest.fixture()
def daemon(tmp_path: Path):  # type: ignore[no-untyped-def]
    config = make_config(tmp_path, cancel_grace_s=0.5)
    with daemon_thread(config) as info:
        yield config, info


def test_initialize_lists_tools_with_schemas_and_instructions(daemon, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon

    async def scenario() -> None:
        started = time.monotonic()
        async with stdio_client(_shim_params(config.env, config.settings.project_dir)) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                elapsed = time.monotonic() - started
                tools = (await session.list_tools()).tools
        assert elapsed < 5, elapsed
        assert "hercules_run" in (init.instructions or "") and "hercules_rerun" in (init.instructions or "")
        assert {tool.name for tool in tools} == TOOLS
        for tool in tools:
            assert tool.inputSchema["type"] == "object", tool.name
            assert tool.outputSchema and tool.outputSchema["type"] == "object", tool.name
            assert tool.description and len(tool.description) < 1200
        run_tool = next(tool for tool in tools if tool.name == "hercules_run")
        assert run_tool.inputSchema["properties"]["wait_s"]["maximum"] == 50

    asyncio.run(scenario())


def test_companion_flow_run_poll_result_rerun(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon
    progress: list[tuple[float, float | None, str | None]] = []

    async def on_progress(value: float, total: float | None, message: str | None) -> None:
        progress.append((value, total, message))

    async def scenario() -> dict[str, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            started = await call(session, "hercules_run", {"test_cases": case_dict("fake-fail", "fake-slow", steps=4), "mode": "pure"})
            assert not started.isError, _text(started)
            run_id = started.structuredContent["runs"][0]["run_id"]
            assert started.structuredContent["runs"][0]["status"] in ("queued", "running")

            status = await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 30}, progress_callback=on_progress)
            assert status.structuredContent["status"] == "failed", status.structuredContent
            assert status.structuredContent["finished"] is True

            result = await call(session, "hercules_run_result", {"run_id": run_id})
            rerun = await call(session, "hercules_rerun", {"run_id": run_id, "scope": "failed_only", "wait_s": 30})
            listed = await call(session, "hercules_list_runs", {"limit": 5})
            return {"run_id": run_id, "result": result, "rerun": rerun, "listed": listed}

    out = asyncio.run(scenario())
    run_id, result = out["run_id"], out["result"]
    assert progress and any("step" in (message or "") for _, _, message in progress)
    assert result.structuredContent["status"] == "failed"
    assert result.structuredContent["failing_step"]["index"] == 2
    images = [item for item in result.content if isinstance(item, ImageContent)]
    assert len(images) == 1 and images[0].mimeType == "image/jpeg"
    assert max(Image.open(io.BytesIO(base64.b64decode(images[0].data))).size) <= 1024
    links = [item for item in result.content if isinstance(item, ResourceLink)]
    assert any(str(link.uri) == f"hercules://runs/{run_id}/steps/2/som.jpg" for link in links)
    assert "FAILED" in _text(result) and len(_text(result)) < 8000
    files = {entry["uri"]: entry["file"] for entry in result.structuredContent["evidence"]}
    assert Path(files[f"hercules://runs/{run_id}/steps/2/plain.jpg"]).is_file()

    rerun = out["rerun"].structuredContent
    assert rerun["all_finished"] is True and rerun["runs"][0]["status"] == "failed"
    assert {run["run_id"] for run in out["listed"].structuredContent["runs"]} >= {run_id, rerun["runs"][0]["run_id"]}


def test_run_with_wait_returns_the_result_and_gherkin_input(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon
    feature = 'Feature: F\n  Scenario: Quick pass\n    Given I open "/"\n    Then I should see "Home"\n'

    async def scenario() -> Any:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            return await call(session, "hercules_run", {"gherkin": feature, "base_url": "http://127.0.0.1:9", "wait_s": 30})

    result = asyncio.run(scenario())
    assert not result.isError, _text(result)
    assert result.structuredContent["all_finished"] is True
    assert result.structuredContent["runs"][0]["status"] == "passed"
    assert "PASSED" in _text(result)


def test_rerun_failed_only_of_a_passed_run_is_not_an_error(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon

    async def scenario() -> dict[str, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            started = await call(session, "hercules_run", {"test_cases": [case_dict()], "wait_s": 30})
            run_id = started.structuredContent["runs"][0]["run_id"]
            nothing = await call(session, "hercules_rerun", {"run_id": run_id})
            whole = await call(session, "hercules_rerun", {"run_id": run_id, "scope": "whole", "wait_s": 30})
            return {"started": started, "nothing": nothing, "whole": whole}

    out = asyncio.run(scenario())
    assert out["started"].structuredContent["runs"][0]["status"] == "passed"
    nothing = out["nothing"]
    assert not nothing.isError, _text(nothing)
    assert nothing.structuredContent == {"runs": [], "all_finished": True}
    assert "nothing to re-run" in _text(nothing) and 'scope "whole"' in _text(nothing)
    whole = out["whole"]
    assert not whole.isError, _text(whole)
    assert whole.structuredContent["runs"][0]["status"] == "passed"


def test_rerun_passes_option_changes_to_the_daemon(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon

    async def scenario() -> dict[str, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            started = await call(session, "hercules_run", {"test_cases": [case_dict("fake-fail")], "mode": "pure", "a11y": True, "wait_s": 30})
            run_id = started.structuredContent["runs"][0]["run_id"]
            changed = await call(session, "hercules_rerun", {"run_id": run_id, "mode": "hybrid", "a11y": False, "visual": True, "fail_on": "serious", "wait_s": 30})
            invalid = await call(session, "hercules_rerun", {"run_id": run_id, "scope": "whole", "fail_on": "serious", "a11y": False})
            return {"run_id": run_id, "changed": changed, "invalid": invalid}

    out = asyncio.run(scenario())
    changed = out["changed"]
    assert not changed.isError, _text(changed)
    new_id = changed.structuredContent["runs"][0]["run_id"]
    assert new_id != out["run_id"]
    args = json.loads((config.settings.project_dir / ".hercules" / "runs" / new_id / "worker_args.json").read_text())
    assert args[args.index("--mode") + 1] == "hybrid" and "--visual" in args and "--a11y" not in args
    assert args[args.index("--quality-fail-on") + 1] == "serious"
    invalid = out["invalid"]
    assert invalid.isError and "fail_on needs a11y or visual" in _text(invalid)


def test_evidence_tool_and_resources(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon

    async def scenario() -> dict[str, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            started = await call(session, "hercules_run", {"test_cases": [case_dict("fake-fail")], "wait_s": 30})
            run_id = started.structuredContent["runs"][0]["run_id"]
            text = await call(session, "hercules_get_evidence", {"uri": f"hercules://runs/{run_id}/steps/2/observation.txt"})
            image = await call(session, "hercules_get_evidence", {"uri": f"hercules://runs/{run_id}/steps/2/som.jpg"})
            outside = await call(session, "hercules_get_evidence", {"uri": f"hercules://runs/{run_id}/../../secret"})
            foreign = await call(session, "hercules_get_evidence", {"uri": "file:///etc/passwd"})
            templates = (await session.list_resource_templates()).resourceTemplates
            resources = (await session.list_resources()).resources
            read = await session.read_resource(f"hercules://runs/{run_id}/steps/2/observation.txt")  # type: ignore[arg-type]
            return {"run_id": run_id, "text": text, "image": image, "outside": outside, "foreign": foreign, "templates": templates, "resources": resources, "read": read}

    out = asyncio.run(scenario())
    assert "md_1" in _text(out["text"])
    assert any(isinstance(item, ImageContent) for item in out["image"].content)
    assert out["outside"].isError and out["foreign"].isError
    assert any("hercules://runs/{run_id}" in template.uriTemplate for template in out["templates"])
    assert any(str(resource.uri) == f"hercules://runs/{out['run_id']}/run.json" for resource in out["resources"])
    assert "md_1" in out["read"].contents[0].text


def test_quality_options_result_summary_and_report(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon
    options = {"a11y": True, "fail_on": "serious", "confirm_destructive": True, "destructive_terms": ["archive"], "ask_on_failure": True, "approval_timeout_s": 45}

    async def scenario() -> dict[str, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            started = await call(session, "hercules_run", {"test_cases": [case_dict()], "wait_s": 30, **options})
            run_id = started.structuredContent["runs"][0]["run_id"]
            result = await call(session, "hercules_run_result", {"run_id": run_id})
            report = await call(session, "hercules_quality_report", {"run_id": run_id})
            serious_only = await call(session, "hercules_quality_report", {"run_id": run_id, "min_impact": "critical", "image": False})
            refused = await call(session, "hercules_run", {"test_cases": [case_dict()], "fail_on": "serious"})
            plain = await call(session, "hercules_run", {"test_cases": [case_dict()], "wait_s": 30})
            none = await call(session, "hercules_quality_report", {"run_id": plain.structuredContent["runs"][0]["run_id"]})
            return {"run_id": run_id, "result": result, "report": report, "serious_only": serious_only, "refused": refused, "none": none}

    out = asyncio.run(scenario())
    run_id = out["run_id"]
    args = json.loads((config.settings.project_dir / ".hercules" / "runs" / run_id / "worker_args.json").read_text())
    assert {"--a11y", "--confirm-destructive", "--ask-on-failure"} <= set(args)
    assert args[args.index("--quality-fail-on") + 1] == "serious" and args[args.index("--destructive-terms") + 1] == "archive"
    assert args[args.index("--approval-timeout-s") + 1] == "45.0"

    result = out["result"]
    assert result.structuredContent["status"] == "failed"
    brief = result.structuredContent["quality"]
    assert brief["counts"]["serious"] == 1 and brief["top"][0]["fix"]["snippet"] == "#subscribe { background-color: #3278c9; }"
    assert result.structuredContent["result"]["quality"] == brief  # the full findings come from hercules_quality_report
    text = _text(result)
    assert "Quality: 1 finding(s) on 2 page(s) (1 serious)" in text and "Fix (verified in the page): #subscribe { background-color: #3278c9; }" in text
    assert text.count("Quality:") == 1 and "fail_on serious" in text  # one headline: the MCP block, not the run summary's too
    assert not [line for line in text.splitlines() if line.startswith("Fix (")]  # the summary's own fix lines are dropped
    assert "src/components/SubscribeButton.tsx" in text
    assert len([item for item in result.content if isinstance(item, ImageContent)]) == 1

    report = out["report"]
    finding = report.structuredContent["findings"][0]
    assert (finding["rule"], finding["impact"], finding["fixes"][0]["verified"], finding["found_on"][0]["step"]) == ("color-contrast", "serious", True, 1)
    assert report.structuredContent["visual"][0]["baseline"] == "home" and report.structuredContent["total"] == 1
    images = [item for item in report.content if isinstance(item, ImageContent)]
    assert len(images) == 1 and images[0].mimeType == "image/jpeg"
    assert "1. [serious] color-contrast WCAG 1.4.3 on #subscribe" in _text(report)
    assert out["serious_only"].structuredContent["findings"] == [] and not [item for item in out["serious_only"].content if isinstance(item, ImageContent)]
    assert out["refused"].isError and "fail_on needs a11y or visual" in _text(out["refused"])
    assert "no quality results" in _text(out["none"])


def test_cancel_long_poll_limit_and_check_env(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon

    async def scenario() -> dict[str, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            started = await call(session, "hercules_run", {"test_cases": case_dict("fake-hang")})
            run_id = started.structuredContent["runs"][0]["run_id"]
            too_long = await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 51})
            clock = time.monotonic()
            short = await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 1})
            short_elapsed = time.monotonic() - clock
            cancelled = await call(session, "hercules_run_cancel", {"run_id": run_id})
            final = await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 20})
            not_found = await call(session, "hercules_run_status", {"run_id": "r_nope"})
            env = await call(session, "hercules_check_env", {})
            return {"too_long": too_long, "short": short, "short_elapsed": short_elapsed, "cancelled": cancelled, "final": final, "not_found": not_found, "env": env}

    out = asyncio.run(scenario())
    assert out["too_long"].isError
    assert out["short"].structuredContent["finished"] is False
    assert out["short_elapsed"] < 4
    assert not out["cancelled"].isError
    assert out["final"].structuredContent["status"] == "cancelled"
    assert out["not_found"].isError and "not found" in _text(out["not_found"]).lower()
    env = out["env"].structuredContent
    assert env["daemon"]["reachable"] is True
    assert env["daemon"]["project_dir"] == str(config.settings.project_dir)
    assert "pure" in env["modes"]


def test_description_draft_then_approve(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon

    async def scenario() -> dict[str, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            started = await call(session, "hercules_run", {"description": "Open the home page\nCheck the title says Home", "base_url": "http://127.0.0.1:9"})
            run_id = started.structuredContent["runs"][0]["run_id"]
            approved = await call(session, "hercules_approve", {"run_id": run_id})
            planned = await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 30})  # a drafted case's plan is reviewed too
            await call(session, "hercules_approve", {"run_id": run_id})
            final = await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 30})
            return {"started": started, "approved": approved, "planned": planned, "final": final}

    out = asyncio.run(scenario())
    assert out["started"].structuredContent["runs"][0]["status"] == "needs_approval"
    assert "hercules_approve" in _text(out["started"])
    assert out["approved"].structuredContent["status"] in ("queued", "running", "passed")
    assert out["planned"].structuredContent["status"] == "needs_plan_approval"
    assert out["final"].structuredContent["status"] == "passed"


def test_bad_tool_input_is_a_tool_error(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon

    async def scenario() -> list[Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            return [
                await call(session, "hercules_run", {}),
                await call(session, "hercules_run", {"test_cases": {"id": "BAD ID"}}),
                await call(session, "hercules_run", {"gherkin": "x", "description": "y"}),
            ]

    for result in asyncio.run(scenario()):
        assert result.isError


def test_shim_follows_a_daemon_restart(tmp_path: Path) -> None:
    first = make_config(tmp_path, token="a" * 43)
    second = make_config(tmp_path, token="b" * 43)

    async def restart_scenario() -> list[Any]:
        results = []
        ctx_first = daemon_thread(first)
        ctx_first.__enter__()
        try:
            async with mcp_session(first.env, first.settings.project_dir) as session:
                results.append(await call(session, "hercules_list_runs", {}))
                ctx_first.__exit__(None, None, None)
                ctx_first = None  # type: ignore[assignment]
                with daemon_thread(second):
                    results.append(await call(session, "hercules_list_runs", {}))
        finally:
            if ctx_first is not None:
                ctx_first.__exit__(None, None, None)
        return results

    results = asyncio.run(restart_scenario())
    assert [result.isError for result in results] == [False, False]


def test_shim_starts_the_daemon_when_none_is_running(tmp_path: Path) -> None:
    env = isolated_env(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    daemon_file = tmp_path / "home" / "daemon.json"

    async def scenario() -> tuple[Any, float]:
        async with mcp_session(env, project) as session:
            clock = time.monotonic()
            result = await call(session, "hercules_check_env", {})
            return result, time.monotonic() - clock

    try:
        result, elapsed = asyncio.run(scenario())
        assert not result.isError, _text(result)
        assert result.structuredContent["daemon"]["reachable"] is True
        assert result.structuredContent["daemon"]["started_now"] is True
        assert elapsed < 10
        info = read_daemon_file(daemon_file)
        assert info is not None and info.pid != os.getpid()
    finally:
        info = read_daemon_file(daemon_file)
        if info is not None:
            os.kill(info.pid, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while daemon_file.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
    assert not daemon_file.exists()


# --------------------------------------------------------------------------- human in the loop


def test_run_waiting_for_approval_shows_the_question_and_screenshot_and_only_a_person_edits_it(daemon) -> None:  # type: ignore[no-untyped-def]
    config, info = daemon

    async def scenario() -> dict[str, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            started = await call(session, "hercules_run", {"test_cases": case_dict("fake-approval", steps=4)})
            run_id = started.structuredContent["runs"][0]["run_id"]
            waiting = await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 20})
            refused = [
                await call(session, "hercules_approve", {"run_id": run_id, "decision": "edit", "step_text": 'Click "Keep"'}),
                await call(session, "hercules_approve", {"run_id": run_id}),
            ]
            # The person answers in the UI (the same API), bound to the question they saw.
            url = f"{info.url}/api/runs/{run_id}/approve"
            auth = {"Authorization": f"Bearer {info.token}"}
            question = waiting.structuredContent["approval"]["id"]
            async with httpx.AsyncClient(trust_env=False) as http:
                stale = await http.post(url, json={"decision": "approve", "approval_id": "not-" + question}, headers=auth)
                edited = await http.post(url, json={"decision": "edit", "step_text": 'Click "Keep"', "approval_id": question}, headers=auth)
            final = await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 30})
            result = await call(session, "hercules_run_result", {"run_id": run_id})
            return {"waiting": waiting, "refused": refused, "stale": stale, "edited": edited, "final": final, "result": result}

    out = asyncio.run(scenario())
    waiting = out["waiting"]
    assert waiting.structuredContent["status"] == "needs_approval"
    approval = waiting.structuredContent["approval"]
    assert approval["kind"] == "destructive" and approval["step"] == 2 and approval["id"]
    text = _text(waiting)
    assert "Delete" in text and "Only a person may approve this" in text
    images = [item for item in waiting.content if isinstance(item, ImageContent)]
    assert len(images) == 1 and images[0].mimeType == "image/jpeg"
    for refused in out["refused"]:
        assert refused.isError and "an agent may only reject it" in _text(refused)
    assert out["stale"].status_code == 409
    assert out["edited"].status_code == 200, out["edited"].text
    assert out["final"].structuredContent["status"] == "passed"
    assert out["result"].structuredContent["result"]["steps"][1]["action"] == 'Click "Keep"'


def test_the_agent_may_reject_a_destructive_question(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon

    async def scenario() -> dict[str, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            started = await call(session, "hercules_run", {"test_cases": case_dict("fake-approval", steps=4)})
            run_id = started.structuredContent["runs"][0]["run_id"]
            await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 20})
            rejected = await call(session, "hercules_approve", {"run_id": run_id, "decision": "reject"})
            final = await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 30})
            return {"rejected": rejected, "final": final}

    out = asyncio.run(scenario())
    assert not out["rejected"].isError, _text(out["rejected"])
    assert out["final"].structuredContent["status"] == "failed"


@pytest.mark.parametrize("run_id", ["..", ".", "r_1..health"])  # each fits the tool schema; ".." must still never reach a URL path
def test_run_ids_are_checked_before_they_reach_a_url(daemon, run_id: str) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon

    async def scenario() -> list[Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            return [
                await call(session, "hercules_run_cancel", {"run_id": run_id}),
                await call(session, "hercules_approve", {"run_id": run_id}),
                await call(session, "hercules_plan", {"run_id": "r_1", "against": run_id}),
            ]

    for result in asyncio.run(scenario()):
        assert result.isError and "not a run id" in _text(result)


def test_elicitation_asks_the_user_when_the_client_supports_it(daemon) -> None:  # type: ignore[no-untyped-def]
    from mcp.shared.context import RequestContext
    from mcp.types import ElicitRequestParams, ElicitResult

    config, _ = daemon
    asked: list[str] = []

    async def on_elicit(context: RequestContext[Any, Any], params: ElicitRequestParams) -> ElicitResult:
        asked.append(params.message)
        return ElicitResult(action="accept", content={"decision": "approve"})

    async def scenario() -> dict[str, Any]:
        async with stdio_client(_shim_params(config.env, config.settings.project_dir)) as (read, write):
            async with ClientSession(read, write, elicitation_callback=on_elicit) as session:
                await session.initialize()
                started = await call(session, "hercules_run", {"test_cases": case_dict("fake-approval", steps=4)})
                run_id = started.structuredContent["runs"][0]["run_id"]
                answered = await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 20})
                final = await call(session, "hercules_run_status", {"run_id": run_id, "wait_s": 30})
                return {"answered": answered, "final": final}

    out = asyncio.run(scenario())
    assert asked and "Delete" in asked[0]
    assert "answered: approve" in _text(out["answered"])
    assert out["final"].structuredContent["status"] == "passed"


def test_a_passed_run_is_exported_as_code_and_a_failed_one_is_refused(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon

    async def scenario() -> dict[str, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            passed = await call(session, "hercules_run", {"test_cases": [case_dict()], "wait_s": 30})
            run_id = passed.structuredContent["runs"][0]["run_id"]
            failed = await call(session, "hercules_run", {"test_cases": [case_dict("fake-fail")], "wait_s": 30})
            failed_id = failed.structuredContent["runs"][0]["run_id"]
            return {
                "ts": await call(session, "hercules_export_code", {"run_id": run_id}),
                "py": await call(session, "hercules_export_code", {"run_id": run_id, "format": "playwright-py", "target_dir": "e2e"}),
                "result": await call(session, "hercules_run_result", {"run_id": run_id}),
                "refused": await call(session, "hercules_export_code", {"run_id": failed_id}),
                "partial": await call(session, "hercules_export_code", {"run_id": failed_id, "allow_failed": True, "format": "spec"}),
            }

    out = asyncio.run(scenario())
    ts = out["ts"].structuredContent
    assert ts["format"] == "playwright-ts" and ts["filename"] == "tests/demo-login.spec.ts"
    assert 'await page.getByRole("button", { name: "step number 1", exact: true }).click();' in ts["code"]
    assert "Adapt it to this repository's test conventions" in ts["guidance"]
    assert "```ts" in _text(out["ts"]) and "Suggested file: tests/demo-login.spec.ts" in _text(out["ts"])
    py = out["py"].structuredContent
    assert py["filename"] == "e2e/test_demo_login.py" and "def test_demo_login(page: Page) -> None:" in py["code"]
    assert "automation" not in out["result"].structuredContent["result"], "the run result stays small: the code comes from hercules_export_code"
    assert out["refused"].isError and "only passed runs are exported" in _text(out["refused"])
    partial = out["partial"].structuredContent
    assert partial["partial"] and partial["steps_exported"] == 1 and partial["notes"][0].startswith("PARTIAL")


class _Stream(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def test_started_by_hand_it_says_on_stderr_that_it_is_running(monkeypatch: pytest.MonkeyPatch) -> None:
    from testzeus_hercules.mcp import shim

    def stop(*_args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(shim.anyio, "run", stop)
    err = _Stream(tty=False)
    assert shim.main(env={}, stdin=_Stream(tty=True), stderr=err) == 0
    said = err.getvalue()
    assert said.startswith("Hercules MCP server ") and f"is running on stdio ({len(shim.TOOLS)} tools" in said
    assert "Press Ctrl+C to stop." in said and said.endswith("Hercules MCP server stopped.\n")


def test_started_by_an_mcp_client_it_prints_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from testzeus_hercules.mcp import shim

    monkeypatch.setattr(shim.anyio, "run", lambda *_args: None)
    err = _Stream(tty=False)
    assert shim.main(env={}, stdin=_Stream(tty=False), stderr=err) == 0
    assert err.getvalue() == ""


def test_project_names_the_project_wherever_the_client_starts_the_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Claude Desktop starts MCP servers in / and ignores a cwd: --project names the project instead."""
    from testzeus_hercules.cli.main import main as cli_main
    from testzeus_hercules.mcp import shim

    seen: list[Path] = []
    monkeypatch.setattr(shim.anyio, "run", lambda _serve, _env, project_dir: seen.append(project_dir))
    monkeypatch.setattr(shim.sys, "stdin", _Stream(tty=False))
    monkeypatch.chdir("/")
    project = tmp_path / "app"
    project.mkdir()
    assert cli_main(["mcp", "--project", str(project)], env={"HERCULES_TELEMETRY": "0", "HERCULES_HOME": str(tmp_path / "home")}) == 0
    assert cli_main(["mcp"], env={"HERCULES_TELEMETRY": "0", "HERCULES_HOME": str(tmp_path / "home")}) == 0
    assert seen == [project, Path("/")]


def test_list_cases_gives_each_case_path_to_run_filtered_by_folder_and_words(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon
    cases = config.paths.cases_dir
    (cases / "Checkout").mkdir()
    (cases / "Checkout" / "pay-by-card.feature").write_text('Feature: Pay\n  Scenario: Pay by card\n    Given I open "/pay"\n    Then I should see "Paid"\n', encoding="utf-8")
    (cases / "sign-in.json").write_text(json.dumps(case_dict("fake-pass")), encoding="utf-8")
    (cases / "broken.json").write_text("{not json", encoding="utf-8")

    async def scenario() -> tuple[Any, Any, Any]:
        async with mcp_session(config.env, config.settings.project_dir) as session:
            return (
                await call(session, "hercules_list_cases", {}),
                await call(session, "hercules_list_cases", {"folder": "checkout"}),
                await call(session, "hercules_list_cases", {"query": "PAY", "limit": 1}),
            )

    every, folder, words = asyncio.run(scenario())
    assert not every.isError, _text(every)
    listed = {case["path"]: case for case in every.structuredContent["cases"]}
    assert set(listed) == {".hercules/cases/Checkout/pay-by-card.feature", ".hercules/cases/sign-in.json", ".hercules/cases/broken.json"}
    assert (
        listed[".hercules/cases/Checkout/pay-by-card.feature"] | {"title": "Pay by card", "folder": "Checkout", "kind": "feature", "valid": True, "steps": 2}
        == listed[".hercules/cases/Checkout/pay-by-card.feature"]
    )
    assert listed[".hercules/cases/broken.json"]["valid"] is False and listed[".hercules/cases/broken.json"]["error"]
    assert every.structuredContent["folders"] == ["Checkout"] and every.structuredContent["total"] == 3
    assert "run one with hercules_run case_path=<path>" in _text(every) and "- [Checkout] Pay by card: .hercules/cases/Checkout/pay-by-card.feature (2 steps)" in _text(every)
    assert [case["path"] for case in folder.structuredContent["cases"]] == [".hercules/cases/Checkout/pay-by-card.feature"]
    assert words.structuredContent["total"] == 1 and len(words.structuredContent["cases"]) == 1


PAY = 'Feature: Pay\n  Scenario: Pay by card\n    Given I open "/pay"\n    Then I should see "Paid"\n'


def test_case_tools_create_read_update_and_delete_after_validating(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon
    (config.paths.envs_dir / "shop.json").write_text(json.dumps({"name": "shop", "base_url": "http://127.0.0.1:9", "variables": {"email": "a@b.c"}}), encoding="utf-8")
    (config.paths.envs_dir / "broken-env.json").write_text(json.dumps({"name": "broken-env", "viewport": "huge"}), encoding="utf-8")
    (config.paths.cases_dir / "Checkout").mkdir()
    feature = config.paths.cases_dir / "Checkout" / "pay-by-card.feature"

    async def scenario() -> dict[str, Any]:
        got: dict[str, Any] = {}
        async with mcp_session(config.env, config.settings.project_dir) as session:
            got["valid"] = await call(session, "hercules_validate_case", {"gherkin": "@env:shop\n" + PAY.replace('"Paid"', '"Paid {{email}}"')})
            got["no_steps"] = await call(session, "hercules_validate_case", {"gherkin": "Feature: Pay\n  Scenario: Nothing\n"})
            got["unknown_env"] = await call(session, "hercules_validate_case", {"gherkin": "@env:nowhere\n" + PAY})
            got["invalid_env"] = await call(session, "hercules_validate_case", {"gherkin": "@env:broken-env\n" + PAY})
            got["unknown_value"] = await call(session, "hercules_validate_case", {"gherkin": PAY.replace('"Paid"', '"{{missing}}"')})
            got["both"] = await call(session, "hercules_validate_case", {"gherkin": PAY, "test_case": case_dict("fake-pass")})
            got["bad_create"] = await call(session, "hercules_create_case", {"gherkin": "@env:nowhere\n" + PAY, "folder": "Checkout"})
            got["create"] = await call(session, "hercules_create_case", {"gherkin": PAY, "folder": "Checkout"})
            got["again"] = await call(session, "hercules_create_case", {"gherkin": PAY})
            got["again_here"] = await call(session, "hercules_create_case", {"gherkin": PAY, "folder": "Checkout"})
            got["get"] = await call(session, "hercules_get_case", {"name": ".hercules/cases/Checkout/pay-by-card.feature"})
            got["get_short"] = await call(session, "hercules_get_case", {"name": "Checkout/pay-by-card.feature"})
            updated = PAY.replace('Then I should see "Paid"', 'Then I should see "Paid"\n    And I should see "Receipt"')
            got["bad_update"] = await call(session, "hercules_update_case", {"name": "Checkout/pay-by-card.feature", "gherkin": PAY.replace('"Paid"', '"{{missing}}"')})
            got["renamed"] = await call(session, "hercules_update_case", {"name": "Checkout/pay-by-card.feature", "gherkin": PAY.replace("Pay by card", "Pay by cash")})
            got["kind"] = await call(session, "hercules_update_case", {"name": "Checkout/pay-by-card.feature", "test_case": case_dict("fake-pass")})
            got["update"] = await call(session, "hercules_update_case", {"name": "Checkout/pay-by-card.feature", "gherkin": updated})
            got["outside"] = await call(session, "hercules_get_case", {"name": "../../secrets.json"})
            got["delete"] = await call(session, "hercules_delete_case", {"name": ".hercules/cases/Checkout/pay-by-card.feature"})
            got["gone"] = await call(session, "hercules_get_case", {"name": "Checkout/pay-by-card.feature"})
        return got

    got = asyncio.run(scenario())
    valid = got["valid"].structuredContent
    assert valid["valid"] and valid["file"] == "pay-by-card.feature" and valid["environment"] == "shop" and valid["steps"] == 2, valid
    assert got["no_steps"].structuredContent["valid"] is False and "no steps" in got["no_steps"].structuredContent["error"]
    assert got["unknown_env"].structuredContent["valid"] is False and "unknown environment 'nowhere'" in got["unknown_env"].structuredContent["error"]
    invalid_env = got["invalid_env"].structuredContent["error"]
    assert "broken-env" in invalid_env and "is not valid" in invalid_env and "unknown" not in invalid_env, "an environment that exists but does not load is not called unknown"
    assert got["unknown_value"].structuredContent["valid"] is False and "missing" in got["unknown_value"].structuredContent["error"]
    assert got["both"].isError and "exactly one of gherkin or test_case" in _text(got["both"])

    assert got["bad_create"].isError and "nothing was saved" in _text(got["bad_create"])
    assert got["create"].structuredContent == {"name": "Checkout/pay-by-card.feature", "path": ".hercules/cases/Checkout/pay-by-card.feature", "id": "pay-by-card"}
    assert got["again"].isError and "hercules_update_case" in _text(got["again"])
    assert got["again_here"].isError and "already exists" in _text(got["again_here"]) and "hercules_update_case" in _text(got["again_here"]), _text(got["again_here"])

    for key in ("get", "get_short"):
        view = got[key].structuredContent
        assert view["path"] == ".hercules/cases/Checkout/pay-by-card.feature" and view["kind"] == "feature" and view["source"] == PAY and view["test_case"]["id"] == "pay-by-card"

    assert got["bad_update"].isError and "left as it was" in _text(got["bad_update"])
    assert got["renamed"].isError and "id would change" in _text(got["renamed"])
    assert got["kind"].isError and "give its new content as gherkin" in _text(got["kind"])
    assert not got["update"].isError, _text(got["update"])
    assert got["outside"].isError and "hercules_list_cases" in _text(got["outside"])

    assert got["delete"].structuredContent == {"name": "Checkout/pay-by-card.feature", "removed": True}
    assert got["gone"].isError and "not found" in _text(got["gone"])
    assert not feature.exists()


def test_folder_tools_create_list_read_rename_move_run_and_delete(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon
    cases = config.paths.cases_dir

    def save(folder: str, case_id: str, *tags: str, status: str = "ready") -> None:
        (cases / folder / f"{case_id}.json").write_text(json.dumps(case_dict(*tags, case_id=case_id, status=status)), encoding="utf-8")

    async def scenario() -> dict[str, Any]:
        got: dict[str, Any] = {}
        async with mcp_session(config.env, config.settings.project_dir) as session:
            got["empty"] = await call(session, "hercules_list_folders", {})
            got["create"] = await call(session, "hercules_create_folder", {"name": "Checkout"})
            got["create_sub"] = await call(session, "hercules_create_folder", {"name": "Cards", "parent": ".hercules/cases/checkout"})
            got["again"] = await call(session, "hercules_create_folder", {"name": "Checkout"})
            got["no_parent"] = await call(session, "hercules_create_folder", {"name": "X", "parent": "Nowhere"})
            save("Checkout", "pay", "fake-pass")
            save("Checkout/Cards", "visa", "fake-fail")
            save("Checkout/Cards", "amex", status="draft")
            (cases / "Checkout" / "broken.json").write_text("{not json", encoding="utf-8")
            got["list"] = await call(session, "hercules_list_folders", {})
            got["get"] = await call(session, "hercules_get_folder", {"folder": "checkout"})
            got["missing"] = await call(session, "hercules_get_folder", {"folder": "Nowhere"})
            got["outside"] = await call(session, "hercules_get_folder", {"folder": "../.."})
            got["run"] = await call(session, "hercules_run_folder", {"folder": "Checkout", "wait_s": 30})
            batch = got["run"].structuredContent["batch"]
            got["runs"] = await call(session, "hercules_list_runs", {"batch": batch})
            got["only_here"] = await call(session, "hercules_run_folder", {"folder": "Checkout/Cards", "subfolders": False, "environment": "nowhere"})
            got["both"] = await call(session, "hercules_update_folder", {"folder": "Checkout", "name": "Pay", "parent": ""})
            got["rename"] = await call(session, "hercules_update_folder", {"folder": "Checkout/Cards", "name": "Wallets"})
            got["move"] = await call(session, "hercules_update_folder", {"folder": "Checkout/Wallets", "parent": ""})
            got["moved_tree"] = sorted(path.relative_to(cases).as_posix() for path in cases.rglob("*.json"))
            got["delete_up"] = await call(session, "hercules_delete_folder", {"folder": "Wallets"})
            (cases / "Checkout" / "notes.txt").write_text("not a case", encoding="utf-8")
            got["delete_all"] = await call(session, "hercules_delete_folder", {"folder": "Checkout", "mode": "delete_cases"})
            got["kept"] = (cases / "Checkout" / "pay.json").is_file()
            (cases / "Checkout" / "notes.txt").unlink()
            got["delete_all_again"] = await call(session, "hercules_delete_folder", {"folder": "Checkout", "mode": "delete_cases"})
        return got

    got = asyncio.run(scenario())
    assert got["empty"].structuredContent == {"folders": [], "top_level_cases": 0, "truncated": False}
    assert got["create"].structuredContent == {"folder": "Checkout"}
    assert got["create_sub"].structuredContent == {"folder": "Checkout/Cards"}, _text(got["create_sub"])
    assert got["again"].isError and "no folder was made" in _text(got["again"]) and "already has a folder" in _text(got["again"])
    assert got["no_parent"].isError and "not found" in _text(got["no_parent"])

    assert got["list"].structuredContent["folders"] == [{"folder": "Checkout", "cases": 4, "ready": 2}, {"folder": "Checkout/Cards", "cases": 2, "ready": 1}]
    folder = got["get"].structuredContent
    assert folder["folder"] == "Checkout" and folder["subfolders"] == ["Checkout/Cards"] and folder["ready"] == 2
    assert {case["path"] for case in folder["cases"]} == {
        ".hercules/cases/Checkout/pay.json",
        ".hercules/cases/Checkout/broken.json",
        ".hercules/cases/Checkout/Cards/visa.json",
        ".hercules/cases/Checkout/Cards/amex.json",
    }
    assert "(draft)" in _text(got["get"]) and "(does not load)" in _text(got["get"])
    assert got["missing"].isError and "not found" in _text(got["missing"])
    assert got["outside"].isError and "hercules_list_folders" in _text(got["outside"])

    run = got["run"].structuredContent
    assert run["folder"] == "Checkout" and run["batch"].startswith("b_") and run["all_finished"] is True, run
    assert sorted(item["status"] for item in run["runs"]) == ["failed", "passed"]
    assert {item["name"] for item in run["skipped"]} == {"Checkout/broken.json", "Checkout/Cards/amex.json"}
    assert f"hercules_list_runs batch={run['batch']}" in _text(got["run"]) and "Skipped Checkout/Cards/amex.json: a draft" in _text(got["run"])
    assert {item["run_id"] for item in got["runs"].structuredContent["runs"]} == {item["run_id"] for item in run["runs"]}
    only_here = got["only_here"].structuredContent
    assert only_here["runs"] == [] and {item["name"] for item in only_here["skipped"]} == {"Checkout/Cards/visa.json", "Checkout/Cards/amex.json"}
    assert "Nothing was run in Checkout/Cards" in _text(got["only_here"]) and "unknown environment 'nowhere'" in _text(got["only_here"])

    assert got["both"].isError and "exactly one of name" in _text(got["both"])
    assert got["rename"].structuredContent == {"folder": "Checkout/Wallets", "was": "Checkout/Cards"}
    assert got["move"].structuredContent == {"folder": "Wallets", "was": "Checkout/Wallets"}
    assert got["moved_tree"] == ["Checkout/broken.json", "Checkout/pay.json", "Wallets/amex.json", "Wallets/visa.json"]
    assert got["delete_up"].structuredContent == {"folder": "Wallets", "mode": "move_up", "moved": 2, "deleted": 0}
    assert {"visa.json", "amex.json"} <= {path.name for path in cases.iterdir()} and not (cases / "Wallets").exists()
    assert got["delete_all"].isError and "not cases" in _text(got["delete_all"]) and got["kept"]
    assert got["delete_all_again"].structuredContent == {"folder": "Checkout", "mode": "delete_cases", "moved": 0, "deleted": 2}
    assert not (cases / "Checkout").exists()


def test_environment_tools_create_list_read_update_and_delete(daemon) -> None:  # type: ignore[no-untyped-def]
    config, _ = daemon
    envs = config.paths.envs_dir
    (config.paths.cases_dir / "pay.feature").write_text("@env:shop\n" + PAY, encoding="utf-8")

    async def scenario() -> dict[str, Any]:
        got: dict[str, Any] = {}
        async with mcp_session(config.env, config.settings.project_dir) as session:
            got["empty"] = await call(session, "hercules_list_environments", {})
            got["no_url"] = await call(session, "hercules_create_environment", {"name": "shop"})
            got["create"] = await call(
                session,
                "hercules_create_environment",
                {"name": "shop", "base_url": "http://127.0.0.1:9/{{tenant}}", "variables": {"tenant": "acme", "password": {"secret": "SHOP_PASSWORD"}}, "secret_names": ["SHOP_TOKEN"]},
            )
            got["again"] = await call(session, "hercules_create_environment", {"name": "shop", "base_url": "http://127.0.0.1:9"})
            got["app"] = await call(session, "hercules_create_environment", {"name": "phone", "mobile": {"platform": "android", "package": "com.android.settings", "activity": ".Settings"}})
            got["bad_name"] = await call(session, "hercules_get_environment", {"name": "../secrets"})
            got["list"] = await call(session, "hercules_list_environments", {})
            got["get"] = await call(session, "hercules_get_environment", {"name": "shop"})
            got["nothing"] = await call(session, "hercules_update_environment", {"name": "shop"})
            got["bad_update"] = await call(session, "hercules_update_environment", {"name": "shop", "variables": {}})
            got["update"] = await call(session, "hercules_update_environment", {"name": "shop", "device": "iPhone 15", "secret_names": None})
            got["missing"] = await call(session, "hercules_update_environment", {"name": "nowhere", "device": "iPhone 15"})
            got["delete"] = await call(session, "hercules_delete_environment", {"name": "shop"})
            got["gone"] = await call(session, "hercules_get_environment", {"name": "shop"})
        return got

    got = asyncio.run(scenario())
    assert got["empty"].structuredContent == {"environments": []}
    assert got["no_url"].isError and "nothing was saved" in _text(got["no_url"]) and "base_url" in _text(got["no_url"])
    created = got["create"].structuredContent
    assert not got["create"].isError, _text(got["create"])
    assert created["environment"]["variables"] == {"tenant": "acme", "password": {"secret": "SHOP_PASSWORD"}} and created["used_by"] == [".hercules/cases/pay.feature"]
    assert got["again"].isError and "already exists" in _text(got["again"]) and "hercules_update_environment" in _text(got["again"])
    assert not got["app"].isError, _text(got["app"])
    assert got["bad_name"].isError and "does not match" in _text(got["bad_name"])

    listed = {item["name"]: item for item in got["list"].structuredContent["environments"]}
    assert listed["shop"] | {"kind": "web", "base_url": "http://127.0.0.1:9/{{tenant}}", "variables": ["password", "tenant"], "secret_names": ["SHOP_TOKEN"]} == listed["shop"]
    assert listed["phone"]["kind"] == "app" and listed["phone"]["platform"] == "android"
    assert got["get"].structuredContent["environment"]["secret_names"] == ["SHOP_TOKEN"] and "Used by 1 case(s): .hercules/cases/pay.feature" in _text(got["get"])

    assert got["nothing"].isError and "at least one field" in _text(got["nothing"])
    assert got["bad_update"].isError and "shop was left as it was" in _text(got["bad_update"]) and "tenant" in _text(got["bad_update"])
    updated = got["update"].structuredContent["environment"]
    assert updated["device"] == "iPhone 15" and updated["secret_names"] == [] and updated["variables"]["tenant"] == "acme", updated
    assert got["missing"].isError and "not found" in _text(got["missing"])

    assert got["delete"].structuredContent == {"name": "shop", "removed": True, "used_by": [".hercules/cases/pay.feature"]}
    assert "will not validate until it exists again" in _text(got["delete"])
    assert got["gone"].isError and "not found" in _text(got["gone"])
    assert not (envs / "shop.json").exists() and (envs / "phone.json").is_file()


def test_update_writes_the_named_file_keeps_its_test_data_and_repairs_a_broken_case(daemon) -> None:  # type: ignore[no-untyped-def]
    """A case whose file name is not its id is updated in place (no second file); a .feature keeps its test data when
    test_data is not given; a case that does not load can be repaired; names with upper-case suffixes work."""
    config, _ = daemon
    cases = config.paths.cases_dir
    (cases / "Checkout").mkdir()
    named = cases / "Checkout" / "pay.feature"  # its id is pay-by-card
    named.write_text(PAY.replace('"Paid"', '"Paid {{amount}}"'), encoding="utf-8")
    (cases / "Checkout" / "pay.data.json").write_text(json.dumps({"test_data": {"amount": "12.50"}}), encoding="utf-8")
    (cases / "broken.json").write_text("{not json", encoding="utf-8")
    (cases / "Login.JSON").write_text(json.dumps(case_dict("fake-pass")), encoding="utf-8")

    async def scenario() -> dict[str, Any]:
        got: dict[str, Any] = {}
        async with mcp_session(config.env, config.settings.project_dir) as session:
            edited = PAY.replace('"Paid"', '"Paid {{amount}} now"')
            got["update"] = await call(session, "hercules_update_case", {"name": "Checkout/pay.feature", "gherkin": edited})
            got["repair"] = await call(session, "hercules_update_case", {"name": "broken.json", "test_case": {**case_dict("fake-pass"), "id": "repaired"}})
            got["upper"] = await call(session, "hercules_get_case", {"name": ".hercules/cases/Login.JSON"})
            got["taken"] = await call(session, "hercules_create_case", {"gherkin": PAY})
        return got

    got = asyncio.run(scenario())
    assert got["update"].structuredContent["path"] == ".hercules/cases/Checkout/pay.feature", _text(got["update"])
    assert sorted(path.name for path in (cases / "Checkout").iterdir()) == ["pay.data.json", "pay.feature"], "updated in place, no pay-by-card.feature beside it"
    assert '"Paid {{amount}} now"' in named.read_text()
    assert json.loads((cases / "Checkout" / "pay.data.json").read_text())["test_data"] == {"amount": "12.50"}, "the test data was kept"
    assert got["repair"].structuredContent["path"] == ".hercules/cases/broken.json", _text(got["repair"])
    assert json.loads((cases / "broken.json").read_text())["id"] == "repaired"
    assert not got["upper"].isError and got["upper"].structuredContent["kind"] == "JSON", _text(got["upper"])
    assert got["taken"].isError and "already in Checkout (pay.feature)" in _text(got["taken"]) and "hercules_update_case" in _text(got["taken"]), _text(got["taken"])


def test_project_is_expanded_and_must_be_a_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from testzeus_hercules.mcp import shim

    seen: list[Path] = []
    monkeypatch.setattr(shim.anyio, "run", lambda _serve, _env, project_dir: seen.append(project_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "repo").mkdir()
    assert shim.main(env={}, project_dir=Path("~/repo"), stdin=_Stream(tty=False), stderr=_Stream(tty=False)) == 0
    assert seen == [tmp_path / "repo"], "~ is expanded (MCP clients pass args without a shell)"
    err = _Stream(tty=False)
    assert shim.main(env={}, project_dir=Path("~/no-such-repo"), stdin=_Stream(tty=False), stderr=err) == 2
    assert "is not a folder" in err.getvalue() and str(tmp_path / "no-such-repo") in err.getvalue() and len(seen) == 1
