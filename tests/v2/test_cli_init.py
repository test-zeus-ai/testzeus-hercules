"""``hercules init --agent claude|codex|cursor``: generated files, no clobbering, JSON/TOML merging."""

from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path

import pytest

from testzeus_hercules.cli.main import main


def run_init(tmp_path: Path, *args: str, env: dict[str, str] | None = None) -> tuple[int, str]:
    out = io.StringIO()
    environ = {"HOME": str(tmp_path / "userhome"), **(env or {})}
    code = main(["init", *args], env=environ, stdout=out, stderr=out)
    return code, out.getvalue()


def test_claude_writes_mcp_json_skill_and_subagent(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()

    code, out = run_init(tmp_path, "--agent", "claude", "--project", str(project))

    assert code == 0, out
    config = json.loads((project / ".mcp.json").read_text())
    assert config["mcpServers"]["hercules"] == {"type": "stdio", "command": "hercules", "args": ["mcp"]}
    skill = (project / ".claude" / "skills" / "hercules-test" / "SKILL.md").read_text()
    agent = (project / ".claude" / "agents" / "qa-runner.md").read_text()
    assert skill.startswith("---\nname: hercules-test\n")
    assert "hercules_run_status" in skill and "failed_only" in skill
    assert agent.startswith("---\nname: qa-runner\n")
    assert "mcp__hercules__hercules_run" in agent


def test_claude_merges_an_existing_mcp_json(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "other-server"}}, "extra": True}))

    code, _ = run_init(tmp_path, "--agent", "claude", "--project", str(project))

    config = json.loads((project / ".mcp.json").read_text())
    assert code == 0
    assert config["mcpServers"]["other"] == {"command": "other-server"}
    assert config["mcpServers"]["hercules"]["args"] == ["mcp"]
    assert config["extra"] is True


def test_existing_files_are_not_clobbered_without_force(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    skill = project / ".claude" / "skills" / "hercules-test" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("my own skill")
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"hercules": {"command": "custom", "args": []}}}))

    code, out = run_init(tmp_path, "--agent", "claude", "--project", str(project))

    assert code == 0
    assert skill.read_text() == "my own skill"
    assert json.loads((project / ".mcp.json").read_text())["mcpServers"]["hercules"]["command"] == "custom"
    assert "skipped" in out and "--force" in out

    code, out = run_init(tmp_path, "--agent", "claude", "--project", str(project), "--force")
    assert code == 0
    assert skill.read_text() != "my own skill"
    assert json.loads((project / ".mcp.json").read_text())["mcpServers"]["hercules"]["command"] == "hercules"


def test_invalid_existing_json_is_left_alone(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".mcp.json").write_text("{ not json")

    code, out = run_init(tmp_path, "--agent", "claude", "--project", str(project), "--force")

    assert code == 1
    assert (project / ".mcp.json").read_text() == "{ not json"
    assert ".mcp.json" in out


def test_print_writes_nothing(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()

    code, out = run_init(tmp_path, "--agent", "claude", "--project", str(project), "--print")

    assert code == 0
    assert list(project.iterdir()) == []
    assert ".mcp.json" in out and "SKILL.md" in out and '"hercules"' in out


def test_codex_prints_the_global_snippet_by_default(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()

    code, out = run_init(tmp_path, "--agent", "codex", "--project", str(project))

    assert code == 0
    assert "[mcp_servers.hercules]" in out and "tool_timeout_sec = 60" in out
    assert not (tmp_path / "userhome" / ".codex" / "config.toml").exists()
    assert (project / ".agents" / "skills" / "hercules-test" / "SKILL.md").is_file()
    agents = (project / "AGENTS.md").read_text()
    assert "<!-- hercules:start -->" in agents and "hercules_run" in agents


def test_codex_write_global_appends_and_keeps_other_servers(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    codex = tmp_path / "userhome" / ".codex"
    codex.mkdir(parents=True)
    (codex / "config.toml").write_text('model = "o4"\n\n[mcp_servers.other]\ncommand = "other"\n')
    (project / "AGENTS.md").write_text("# Team rules\n\nBe nice.\n")

    code, out = run_init(tmp_path, "--agent", "codex", "--project", str(project), "--write-global")
    again, out_again = run_init(tmp_path, "--agent", "codex", "--project", str(project), "--write-global")

    assert code == 0 and again == 0, out + out_again
    config = tomllib.loads((codex / "config.toml").read_text())
    assert config["model"] == "o4"
    assert config["mcp_servers"]["other"]["command"] == "other"
    assert config["mcp_servers"]["hercules"] == {"command": "hercules", "args": ["mcp"], "startup_timeout_sec": 10, "tool_timeout_sec": 60}
    assert (codex / "config.toml").read_text().count("[mcp_servers.hercules]") == 1
    agents = (project / "AGENTS.md").read_text()
    assert agents.startswith("# Team rules\n\nBe nice.\n")
    assert agents.count("<!-- hercules:start -->") == 1


def test_codex_home_env_is_respected(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    codex_home = tmp_path / "custom-codex"

    code, _ = run_init(tmp_path, "--agent", "codex", "--project", str(project), "--write-global", env={"CODEX_HOME": str(codex_home)})

    assert code == 0
    assert "mcp_servers" in tomllib.loads((codex_home / "config.toml").read_text())


def test_cursor_writes_mcp_json_and_rule(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    (project / ".cursor").mkdir(parents=True)
    (project / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {"db": {"command": "db-mcp"}}}))

    code, out = run_init(tmp_path, "--agent", "cursor", "--project", str(project))

    assert code == 0, out
    config = json.loads((project / ".cursor" / "mcp.json").read_text())
    assert set(config["mcpServers"]) == {"db", "hercules"}
    rule = (project / ".cursor" / "rules" / "hercules.mdc").read_text()
    assert rule.startswith("---\n") and "hercules_run" in rule


def test_unknown_agent_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        run_init(tmp_path, "--agent", "vim")


def test_templates_are_generic() -> None:
    from testzeus_hercules.cli import init_templates

    text = " ".join([value for name, value in vars(init_templates).items() if isinstance(value, str) and not name.startswith("__")] + [init_templates.codex_snippet("hercules")]).lower()
    for word in ("/users/", "/home/", "todo", "tbd", "token"):
        assert word not in text, word


def test_subagent_may_call_every_mcp_tool(tmp_path: Path) -> None:
    from testzeus_hercules.mcp.specs import TOOLS as SPECS

    code, out = run_init(tmp_path, "--agent", "claude", "--project", str(tmp_path))
    assert code == 0, out
    header = (tmp_path / ".claude" / "agents" / "qa-runner.md").read_text().split("---")[1]
    tools_line = next(line for line in header.splitlines() if line.startswith("tools:"))
    for spec in SPECS:
        assert f"mcp__hercules__{spec['name']}" in tools_line, spec["name"]


@pytest.mark.parametrize("agent", ["claude", "codex", "cursor", "copilot", "gemini", "generic"])
def test_every_agent_file_teaches_exploring_and_the_triage_rule(tmp_path: Path, agent: str) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    code, out = run_init(tmp_path, "--agent", agent, "--project", str(project))
    assert code == 0, out
    written = [path for path in project.rglob("*") if path.is_file() and path.suffix in (".md", ".mdc")]
    assert written, f"{agent} wrote no agent instructions"
    for path in written:
        text = path.read_text()
        assert "hercules_explore" in text and "hercules_explore_triage" in text, path
        assert "explicit decision" in text and "on your own judgement" in text, path  # triage only on the user's word
        assert "No findings is not a pass" in text or "no findings is not a pass" in text.lower(), path


def test_skill_and_subagent_trigger_on_bug_hunts() -> None:
    from testzeus_hercules.cli import init_templates

    for text in (init_templates.SKILL, init_templates.QA_RUNNER):
        description = next(line for line in text.splitlines() if line.startswith("description:"))
        for word in ("explore", "bug hunt", "find bugs", "what's broken"):
            assert word in description, (word, description)
    assert "via coding agent" in init_templates.SKILL and "i_own_this_environment" in init_templates.SKILL
