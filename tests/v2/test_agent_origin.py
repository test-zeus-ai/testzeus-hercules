"""Coding agents: ``hercules setup`` offers to connect them (asking first), and runs they start are marked with the
agent, in the run view and in telemetry."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from testzeus_hercules.cli.agents_detect import find_agents, looks_like_project
from testzeus_hercules.cli.main import main
from testzeus_hercules.cli.setup import SetupDeps, parse_agents
from testzeus_hercules.daemon.manager import _origin_env
from testzeus_hercules.kernel.telemetry import (
    RecordingTransport,
    TelemetryClient,
    agent_client_from_name,
    build_payload,
    run_origin_from_env,
)
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.llm.tiers import GIB
from tests.v2.fixtures.daemon_support import client_for, make_config, submit, wait_for, case_dict

# --- detection ---------------------------------------------------------------


def _project(tmp_path: Path, *markers: str) -> Path:
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True, exist_ok=True)
    for marker in markers:
        target = project / marker
        if "." in Path(marker).name and not marker.startswith("."):
            target.write_text("")
        else:
            target.mkdir(parents=True, exist_ok=True)
    return project


def test_agents_are_found_by_command_or_project_files(tmp_path: Path) -> None:
    project = _project(tmp_path, ".cursor", "GEMINI.md")
    found = find_agents(project, which=lambda command: "/usr/bin/claude" if command == "claude" else None)
    assert [(agent.agent, agent.why) for agent in found] == [
        ("claude", "the claude command"),
        ("cursor", "the project's .cursor"),
        ("gemini", "the project's GEMINI.md"),
    ]
    assert not any(agent.set_up for agent in found)


def test_a_home_folder_is_not_a_project(tmp_path: Path) -> None:
    assert looks_like_project(_project(tmp_path))
    assert not looks_like_project(tmp_path / "empty")
    assert not looks_like_project(Path.home())


@pytest.mark.parametrize(("value", "expected"), [(None, None), ("none", ()), ("", ()), ("claude, codex,claude", ("claude", "codex"))])
def test_agents_flag_is_parsed(value: str | None, expected: tuple[str, ...] | None) -> None:
    assert parse_agents(value) == expected


# --- hercules setup asks before connecting ----------------------------------


def _setup(tmp_path: Path, project: Path, *args: str, answers: dict[str, str] | None = None, which: dict[str, str] | None = None) -> tuple[int, str]:
    """``answers`` maps a question's start (such as "Connect Cursor") to the answer; other questions take the default."""
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return next((answer for start, answer in (answers or {}).items() if prompt.strip().startswith(start)), "")

    deps = SetupDeps(
        ask=ask,
        ask_secret=lambda prompt: "",
        make_client=_no_network,
        detect_memory=lambda: 24 * GIB,
        stdin=io.StringIO(""),
        which=lambda command: (which or {}).get(command),
    )
    out = io.StringIO()
    env = {"HERCULES_HOME": str(tmp_path / "home"), "HOME": str(tmp_path / "userhome"), "HERCULES_TELEMETRY": "0"}
    code = main(["setup", *args, "--project", str(project), "--telemetry", "off"], env=env, setup_deps=deps, stdout=out, stderr=io.StringIO())
    return code, out.getvalue()


def _no_network(timeout: float = 5.0):  # type: ignore[no-untyped-def]
    import httpx

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(refuse), timeout=timeout)


def test_setup_asks_for_each_agent_and_connects_only_the_yes(tmp_path: Path) -> None:
    project = _project(tmp_path, ".cursor")
    code, out = _setup(tmp_path, project, answers={"Connect Claude Code": "y", "Connect Cursor": "n"}, which={"claude": "/usr/bin/claude"})
    assert code == 0, out
    assert "(e) Coding agents" in out and "Claude Code: found (the claude command); not connected yet" in out
    assert "Claude Code: connected" in out
    assert (project / ".claude" / "skills" / "hercules-test" / "SKILL.md").is_file()
    assert json.loads((project / ".mcp.json").read_text())["mcpServers"]["hercules"]["args"] == ["mcp"]
    assert not (project / ".cursor" / "rules" / "hercules.mdc").exists(), "the user said no to Cursor"


def test_setup_says_when_an_agent_is_already_connected(tmp_path: Path) -> None:
    project = _project(tmp_path, ".claude/skills/hercules-test")
    (project / ".claude" / "skills" / "hercules-test" / "SKILL.md").write_text("mine")
    code, out = _setup(tmp_path, project)
    assert code == 0, out
    assert "Hercules is already set up for it" in out
    assert (project / ".claude" / "skills" / "hercules-test" / "SKILL.md").read_text() == "mine"


def test_yes_never_writes_agent_files_unless_named(tmp_path: Path) -> None:
    project = _project(tmp_path, ".cursor")
    code, out = _setup(tmp_path, project, "--yes")
    assert code == 0, out
    assert "with --yes, pass --agents cursor to connect them" in out
    assert not (project / ".cursor" / "mcp.json").exists()

    code, out = _setup(tmp_path, project, "--yes", "--agents", "cursor,gemini")
    assert code == 0, out
    assert (project / ".cursor" / "rules" / "hercules.mdc").is_file() and (project / "GEMINI.md").is_file()


def test_agents_none_skips_the_step_and_unknown_agents_are_refused(tmp_path: Path) -> None:
    project = _project(tmp_path, ".cursor")
    code, out = _setup(tmp_path, project, "--yes", "--agents", "none")
    assert code == 0 and "skipped (--agents none)" in out
    code, out = _setup(tmp_path, project, "--yes", "--agents", "vim")
    assert code == 2 and "unknown coding agent 'vim'" in out


# --- runs are marked with the agent that started them -----------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("claude-code", "claude-code"),
        ("Claude Desktop", "claude-code"),
        ("codex-mcp-client", "codex"),
        ("cursor-vscode", "cursor"),
        ("Visual Studio Code", "copilot"),
        ("gemini-cli-mcp-client", "gemini"),
        ("windsurf", "other"),
        ("", None),
        (None, None),
    ],
)
def test_agent_names_map_to_a_fixed_set(name: str | None, expected: str | None) -> None:
    assert agent_client_from_name(name) == expected


def test_the_daemon_marks_runs_started_by_an_agent(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        by_ui = submit(client)
        by_agent = client.post("/api/runs", json={"test_case": case_dict(case_id="agent-case")}, headers={"X-Hercules-Agent": "Claude Code"}).json()["run_id"]
        wait_for(client, by_ui)
        agent_view = wait_for(client, by_agent)
        ui_view = client.get(f"/api/runs/{by_ui}").json()
        rerun = client.post(f"/api/runs/{by_agent}/rerun", json={"scope": "whole"}, headers={"X-Hercules-Agent": "codex"}).json()["run_id"]
        rerun_view = wait_for(client, rerun)
    assert (ui_view["started_by"], ui_view["agent"]) == ("daemon", None)
    assert (agent_view["started_by"], agent_view["agent"]) == ("agent", "claude-code")
    assert (rerun_view["started_by"], rerun_view["agent"]) == ("agent", "codex")
    assert ui_view["explore_session"] is None and agent_view["explore_session"] is None


def test_workers_get_the_origin_and_telemetry_reports_it(tmp_path: Path) -> None:
    assert _origin_env({"origin": "agent", "agent": "cursor"}) == {"HERCULES_RUN_ORIGIN": "agent", "HERCULES_AGENT_CLIENT": "cursor"}
    assert _origin_env({"origin": "agent", "agent": "made-up"}) == {"HERCULES_RUN_ORIGIN": "daemon"}
    assert _origin_env(None) == {"HERCULES_RUN_ORIGIN": "daemon"}
    assert run_origin_from_env({}) == ("cli", None)
    assert run_origin_from_env({"HERCULES_RUN_ORIGIN": "agent", "HERCULES_AGENT_CLIENT": "gemini"}) == ("agent", "gemini")
    assert run_origin_from_env({"HERCULES_RUN_ORIGIN": "nonsense", "HERCULES_AGENT_CLIENT": "gemini"}) == ("cli", None)

    settings = Settings.load(tmp_path, env={"HERCULES_HOME": str(tmp_path / "home")})
    transport = RecordingTransport()
    with TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=transport, run_origin="agent", agent_client="claude-code") as telemetry:
        telemetry.emit("run_started", step_count=3)
    (payload,) = transport.payloads
    assert payload["run_origin"] == "agent" and payload["agent_client"] == "claude-code"


def test_agent_setup_is_a_telemetry_event_with_a_fixed_agent_name() -> None:
    assert build_payload("agent_setup", {"agent_client": "copilot"})["agent_client"] == "copilot"
    with pytest.raises(ValueError):
        build_payload("agent_setup", {"agent_client": "my secret laptop"})


def test_init_records_the_agent_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from testzeus_hercules.kernel import telemetry

    transport = RecordingTransport()
    monkeypatch.setattr(telemetry, "PostHogTransport", lambda *args, **kwargs: transport)
    project = _project(tmp_path)
    code = main(["init", "--agent", "claude", "--project", str(project)], env={"HOME": str(tmp_path / "userhome"), "HERCULES_HOME": str(tmp_path / "home")}, stdout=io.StringIO(), stderr=io.StringIO())
    assert code == 0
    assert [(payload["event"], payload["agent_client"]) for payload in transport.payloads] == [("agent_setup", "claude-code")]


def test_mcp_instructions_route_every_e2e_test_through_hercules() -> None:
    from testzeus_hercules.cli import init_templates
    from testzeus_hercules.mcp.specs import INSTRUCTIONS

    assert "Use Hercules for every end-to-end" in INSTRUCTIONS and "throwaway browser script" in INSTRUCTIONS
    for template in (init_templates.SKILL, init_templates.QA_RUNNER, init_templates.CURSOR_RULE, init_templates.COPILOT_INSTRUCTIONS, init_templates.AGENTS_BLOCK):
        assert "throwaway" in template


def test_the_mcp_shim_names_the_agent_and_sends_it_to_the_daemon(tmp_path: Path) -> None:
    import asyncio
    from types import SimpleNamespace

    import httpx

    from testzeus_hercules.mcp import daemon_client
    from testzeus_hercules.mcp.shim import _agent_of

    session = SimpleNamespace(client_params=SimpleNamespace(clientInfo=SimpleNamespace(name="claude-code", version="2.1")))
    assert _agent_of(session) == "claude-code"
    assert _agent_of(SimpleNamespace()) is None

    seen: list[httpx.Request] = []
    client = daemon_client.DaemonClient(env={"HERCULES_HOME": str(tmp_path)}, project_dir=tmp_path, autostart=False)
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200, json={})))
    client.agent = "claude-code"
    from testzeus_hercules.daemon.discovery import DaemonInfo

    info = DaemonInfo(port=1, pid=1, token="t", started_at="2026-11-13T00:00:00Z", project_dir=str(tmp_path))

    async def go() -> None:
        await client._send(info, "POST", "/api/runs", json={}, params=None, timeout_s=1)
        await client._http.aclose()

    asyncio.run(go())
    assert seen[0].headers["X-Hercules-Agent"] == "claude-code"
