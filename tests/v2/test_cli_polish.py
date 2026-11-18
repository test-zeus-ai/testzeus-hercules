"""Terminal polish of the ``hercules`` command: colour only on terminals, live run progress, "did you mean",
shell completion, placeholder cloud user names, and the agent setups ``hercules init`` writes."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from testzeus_hercules.cli.completion import completion_script
from testzeus_hercules.cli.main import build_parser, main
from testzeus_hercules.cli.parser import did_you_mean
from testzeus_hercules.cli.progress import StepProgress
from testzeus_hercules.cli.run import styled_summary
from testzeus_hercules.cli.style import Style, color_enabled
from testzeus_hercules.clouds.credentials import CloudCredentials, placeholder_problem

ESC = "\x1b["


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


# --- colour -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("tty", "env", "expected"),
    [
        (True, {}, True),
        (False, {}, False),
        (True, {"NO_COLOR": "1"}, False),
        (True, {"TERM": "dumb"}, False),
        (False, {"FORCE_COLOR": "1"}, True),
        (False, {"FORCE_COLOR": "0"}, False),
        (True, {"NO_COLOR": "1", "FORCE_COLOR": "1"}, False),
    ],
)
def test_colour_follows_the_terminal_and_the_usual_variables(tty: bool, env: dict[str, str], expected: bool) -> None:
    stream = _Tty() if tty else io.StringIO()
    assert color_enabled(stream, env) is expected


def test_without_colour_text_is_unchanged() -> None:
    plain = Style(False)
    assert plain.status("failed", "FAIL") == "FAIL" and plain.bold("x") == "x" and plain.dim("") == ""
    coloured = Style(True)
    assert coloured.ok("ok").startswith(ESC) and coloured.ok("ok").endswith("\x1b[0m")


def test_summary_colours_only_on_a_terminal() -> None:
    text = "Run r_1 FAILED (case login, 3.0s, verdict: decider)\nStep 2 FAILED: Click\nExpected: x"
    assert styled_summary(text, "failed", Style(False)) == text
    coloured = styled_summary(text, "failed", Style(True))
    assert ESC in coloured and "Run r_1 " in coloured and coloured.count("\n") == 2


# --- run progress -----------------------------------------------------------


def _drive(progress: StepProgress) -> None:
    progress.emit("run_started", steps=2)
    progress.emit("step_started", step=1, action="Open the   login page")
    progress.emit("step_finished", step=1, status="passed", duration_ms=1200)
    progress.emit("step_started", step=2, action="Click the Sign in button")
    progress.emit("step_recovering", step=2, attempt=1, reason="reread")
    progress.emit("step_finished", step=2, status="failed", duration_ms=800)
    progress.emit("run_finished", status="failed")


def test_plain_progress_prints_one_line_per_finished_step_and_recovery() -> None:
    out = io.StringIO()
    _drive(StepProgress(out, Style(False)))
    assert out.getvalue().splitlines() == [
        "  passed     step 1/2  Open the login page  1.2s",
        "  recovering step 2/2  attempt 1: reread",
        "  failed     step 2/2  Click the Sign in button  0.8s",
    ]


def test_live_progress_rewrites_the_current_line() -> None:
    out = io.StringIO()
    _drive(StepProgress(out, Style(True), width=60))
    text = out.getvalue()
    assert text.count("\n") == 2, "one finished line per step"
    assert "\r\x1b[2K" in text and "✓" in text and "✗" in text and "recovering, attempt 1" in text


def test_long_actions_are_clipped_on_a_live_line() -> None:
    out = io.StringIO()
    progress = StepProgress(out, Style(False), live=True, width=50)
    progress.emit("step_started", step=1, action="word " * 40)
    progress.close()
    assert "…" in out.getvalue() and len(out.getvalue().strip()) < 60


def test_a_bad_event_never_breaks_the_run() -> None:
    out = io.StringIO()
    progress = StepProgress(out, Style(False))
    progress.emit("step_finished")  # no step number
    progress.emit("step_started", step="not a number")
    assert out.getvalue() == ""


# --- "did you mean" ---------------------------------------------------------


def test_close_choices_are_suggested() -> None:
    assert did_you_mean("argument <command>: invalid choice: 'clod' (choose from doctor, run, cloud)") == "did you mean 'cloud'?"
    assert did_you_mean("argument --cloud: invalid choice: 'browsrstack' (choose from 'browserstack', 'saucelabs', 'testmu')") == "did you mean 'browserstack'?"
    assert did_you_mean("argument <command>: invalid choice: 'zzz' (choose from doctor, run)") is None
    assert did_you_mean("the following arguments are required: CASE") is None


def test_a_mistyped_command_exits_2_with_a_hint(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["clod"], env={})
    assert exit_info.value.code == 2
    error = capsys.readouterr().err
    assert "hint: did you mean 'cloud'?" in error and ESC not in error


# --- shell completion -------------------------------------------------------


def test_completion_covers_commands_subcommands_and_choices() -> None:
    script = completion_script(build_parser(), "bash")
    assert "complete -o default -F _hercules_complete hercules" in script
    assert '":cloud") path="cloud"' in script and '"cloud:run") path="cloud run"' in script
    assert '"cloud run:--cloud") COMPREPLY=($(compgen -W "browserstack saucelabs testmu"' in script
    assert '"run:--mode")' in script and "--decider" not in script and "--frames-fd" not in script
    assert completion_script(build_parser(), "zsh").startswith("autoload -U +X compinit")
    with pytest.raises(ValueError):
        completion_script(build_parser(), "fish")


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_bash_completes_with_the_generated_script(tmp_path: Path) -> None:
    script = tmp_path / "hercules.bash"
    out = io.StringIO()
    assert main(["completion", "bash"], env={}, stdout=out) == 0
    script.write_text(out.getvalue())
    probe = (
        f"source {script}\n"
        't(){ COMP_WORDS=("$@"); COMP_CWORD=$((${#COMP_WORDS[@]}-1)); COMPREPLY=(); _hercules_complete; echo "${COMPREPLY[*]}"; }\n'
        "t hercules clo\nt hercules cloud run r1 --cloud te\nt hercules run --mode hy\n"
    )
    result = subprocess.run(["bash", "-c", probe], capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == ["cloud", "testmu", "hybrid"]


def test_every_visible_option_has_help() -> None:
    import argparse

    missing = []

    def walk(parser: argparse.ArgumentParser, path: str) -> None:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    walk(child, f"{path} {name}")
            elif action.help is None:
                missing.append(f"{path} {action.option_strings or action.dest}")

    walk(build_parser(), "hercules")
    assert missing == []


# --- placeholder cloud user names -------------------------------------------


@pytest.mark.parametrize("name", ["YOUR_BROWSERSTACK_USERNAME", "your-user", "<username>", "${SAUCE_USERNAME}", "changeme", "xxxx", "username"])
def test_placeholder_user_names_are_flagged(name: str) -> None:
    found = CloudCredentials("browserstack", "U", "K", source="file", username=name, access_key="k")
    assert "looks like a placeholder" in (placeholder_problem(found) or "")


@pytest.mark.parametrize("name", ["jane_doe", "acme-qa", "oauth-jane-1a2b3", None])
def test_real_user_names_are_not_flagged(name: str | None) -> None:
    found = CloudCredentials("testmu", "U", "K", source="file" if name else "none", username=name, access_key="k" if name else None)
    assert placeholder_problem(found) is None


def test_cloud_list_warns_about_a_placeholder_name(tmp_path: Path) -> None:
    env = {"HERCULES_HOME": str(tmp_path / "home"), "HERCULES_TELEMETRY": "0", "BROWSERSTACK_USERNAME": "YOUR_BROWSERSTACK_USERNAME", "BROWSERSTACK_ACCESS_KEY": "k3y"}
    out = io.StringIO()
    assert main(["cloud", "list", "--project", str(tmp_path)], env=env, stdout=out, stderr=io.StringIO()) == 0
    text = out.getvalue()
    assert "warning:" in text and "looks like a placeholder" in text and "k3y" not in text and ESC not in text
    rows = io.StringIO()
    main(["cloud", "list", "--json", "--project", str(tmp_path)], env=env, stdout=rows, stderr=io.StringIO())
    assert "placeholder" in json.loads(rows.getvalue())[0]["problem"]


def test_cloud_connect_saves_the_sauce_region(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from testzeus_hercules.cli import cloud_cmd
    from testzeus_hercules.clouds import check as check_module
    from testzeus_hercules.clouds.credentials import SAUCELABS_REGIONS, find_credentials
    from testzeus_hercules.kernel.settings import Settings

    seen: list[str | None] = []

    async def accept(cloud: str, credentials: CloudCredentials, settings: object) -> check_module.CheckResult:
        seen.append(credentials.region)
        return check_module.CheckResult(True, "connected")

    monkeypatch.setattr(check_module, "check_credentials", accept)
    env = {"HERCULES_HOME": str(tmp_path / "home"), "HERCULES_TELEMETRY": "0"}
    monkeypatch.chdir(tmp_path)

    def connect(cloud: str, err: io.StringIO) -> int:
        args = build_parser().parse_args(["cloud", "connect", cloud, "--username", "demo", "--region", "eu-central-1"])
        return cloud_cmd.run_cloud_command(args, env=env, out=io.StringIO(), err=err, stdin=io.StringIO("k3y\n"))

    assert cloud_cmd.SAUCELABS_REGION_CHOICES == SAUCELABS_REGIONS
    assert connect("saucelabs", io.StringIO()) == 0
    err = io.StringIO()
    refused = connect("browserstack", err)

    assert seen == ["eu-central-1"]
    assert find_credentials("saucelabs", Settings.load(tmp_path, env=env), {}).region_source == "file"
    assert refused == 2 and "only Sauce Labs and TestMu AI have a region" in err.getvalue()


# --- hercules init for more agents ------------------------------------------


def _init(tmp_path: Path, agent: str) -> tuple[int, str, Path]:
    project = tmp_path / "repo"
    project.mkdir(exist_ok=True)
    out = io.StringIO()
    code = main(["init", "--agent", agent, "--project", str(project)], env={"HOME": str(tmp_path / "userhome"), "HERCULES_TELEMETRY": "0"}, stdout=out, stderr=out)
    return code, out.getvalue(), project


def test_copilot_gets_vscode_mcp_config_a_skill_and_instructions(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    (project / ".vscode").mkdir(parents=True)
    (project / ".vscode" / "mcp.json").write_text(json.dumps({"servers": {"other": {"command": "x"}}, "inputs": []}))
    code, out, project = _init(tmp_path, "copilot")
    assert code == 0, out
    config = json.loads((project / ".vscode" / "mcp.json").read_text())
    assert config["servers"]["hercules"] == {"type": "stdio", "command": "hercules", "args": ["mcp"]}
    assert config["servers"]["other"] == {"command": "x"} and config["inputs"] == []
    assert (project / ".github" / "skills" / "hercules-test" / "SKILL.md").read_text().startswith("---\nname: hercules-test\n")
    assert (project / ".github" / "instructions" / "hercules.instructions.md").read_text().startswith('---\napplyTo: "**"\n')


def test_gemini_gets_settings_a_skill_and_a_gemini_md_block(tmp_path: Path) -> None:
    code, out, project = _init(tmp_path, "gemini")
    assert code == 0, out
    assert json.loads((project / ".gemini" / "settings.json").read_text())["mcpServers"]["hercules"] == {"command": "hercules", "args": ["mcp"]}
    block = (project / "GEMINI.md").read_text()
    assert "<!-- hercules:start -->" in block and ".agents/skills/hercules-test/SKILL.md" in block
    assert (project / ".agents" / "skills" / "hercules-test" / "SKILL.md").exists()
    code, out, _ = _init(tmp_path, "gemini")
    assert "unchanged GEMINI.md" in out


def test_generic_writes_the_skill_and_prints_the_server_entry(tmp_path: Path) -> None:
    code, out, project = _init(tmp_path, "generic")
    assert code == 0, out
    assert (project / "AGENTS.md").exists() and (project / ".agents" / "skills" / "hercules-test" / "SKILL.md").exists()
    assert '"hercules": {' in out and '"args": [\n        "mcp"\n      ]' in out


def test_the_skill_covers_the_whole_job() -> None:
    from testzeus_hercules.cli import init_templates

    skill = init_templates.SKILL
    for section in ("## When to test", "## Before the first run", "## The loop", "## Writing a test case", "## Reading a failure", "## More than pass or fail", "## Without the MCP tools", "## Rules"):
        assert section in skill, section
    for detail in ("hercules_check_env", "product_bug", "test_fragility", "Scenario:", "environment.mobile", "a11y: true", "hercules_export_code", "hercules cloud run", "exits 0", "Never weaken"):
        assert detail in skill, detail
    for template in (init_templates.QA_RUNNER, init_templates.CURSOR_RULE, init_templates.COPILOT_INSTRUCTIONS):
        assert "## Reading a failure" in template
