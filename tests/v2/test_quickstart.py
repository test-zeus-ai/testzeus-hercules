"""``testzeus-hercules`` (``uvx testzeus-hercules``): install the command, download Chromium and run setup, asking first;
and the coding-agent setup prompt in the docs, which must only name flags that exist."""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from pathlib import Path

import pytest

from testzeus_hercules.cli.quickstart import QuickstartDeps, Ran, chromium_ready, install_spec, is_temporary_env, main

ROOT = Path(__file__).resolve().parents[2]
DRY_RUN = "browser: chromium version 131\n  Install location:    {chromium}\n\nbrowser: ffmpeg\n  Install location:    {ffmpeg}\n\nbrowser: chromium-headless-shell version 131\n  Install location:    {shell}\n"


class Machine:
    """A scripted machine: which commands exist, whether Chromium is downloaded, and every command run."""

    def __init__(self, tmp_path: Path, *, tools: Sequence[str] = ("uv",), hercules: str | None = None, chromium: bool = False, tool_bin: str = "/home/u/.local/bin") -> None:
        self.tmp_path, self.tools, self.hercules, self.tool_bin = tmp_path, tools, hercules, tool_bin
        self.ran: list[list[str]] = []
        self.chromium = tmp_path / "ms-playwright" / "chromium-1148"
        self.shell = tmp_path / "ms-playwright" / "chromium_headless_shell-1148"
        if chromium:
            self.chromium.mkdir(parents=True, exist_ok=True)
            self.shell.mkdir(parents=True, exist_ok=True)

    def which(self, command: str) -> str | None:
        if command == "hercules":
            return self.hercules
        return f"/usr/bin/{command}" if command in self.tools else None

    def run(self, command: Sequence[str], capture: bool) -> Ran:
        self.ran.append(list(command))
        if command[-2:] == ["--dry-run", "chromium"]:
            return Ran(0, DRY_RUN.format(chromium=self.chromium, ffmpeg=self.tmp_path / "ffmpeg", shell=self.shell))
        if command[1:] == ["tool", "dir", "--bin"]:
            return Ran(0, self.tool_bin + "\n")
        return Ran(0)

    def deps(self, answers: Sequence[str] = ()) -> QuickstartDeps:
        queue = list(answers)
        return QuickstartDeps(
            ask=lambda prompt: queue.pop(0) if queue else "",
            which=self.which,
            run=self.run,
            prefix=self.tmp_path / ".cache" / "uv" / "archive-v0" / "x",
            python="/py",
            version=lambda: "2.0.0",
            exists=lambda path: False,
        )


def _start(machine: Machine, *argv: str, answers: Sequence[str] = (), env: dict[str, str] | None = None) -> tuple[int, str, list[list[str]]]:
    setup_calls: list[list[str]] = []
    out = io.StringIO()
    code = main(list(argv), env=env or {"PATH": "/usr/bin"}, deps=machine.deps(answers), setup=lambda flags: setup_calls.append(flags) or 0, stdout=out)
    return code, out.getvalue(), setup_calls


def test_a_fresh_machine_gets_the_command_chromium_and_setup(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    code, out, setup_calls = _start(machine)
    assert code == 0, out
    assert ["/usr/bin/uv", "tool", "install", "--python", "3.13", "testzeus-hercules==2.0.0"] in machine.ran
    assert ["/usr/bin/uv", "tool", "update-shell"] in machine.ran, "the tool folder was not on PATH"
    assert ["/py", "-m", "playwright", "install", "chromium"] in machine.ran
    assert setup_calls == [[]]
    assert "Hercules is ready." in out and "Restart your coding agent" in out


def test_nothing_is_installed_after_a_no(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    code, out, setup_calls = _start(machine, answers=["n", "none"])
    assert code == 0
    assert not any("install" in command and "tool" in command for command in machine.ran)
    assert ["/py", "-m", "playwright", "install", "chromium"] not in machine.ran
    assert "coding agents start `hercules mcp`" in out and setup_calls == [[]]


def test_what_is_already_there_is_left_alone(tmp_path: Path) -> None:
    machine = Machine(tmp_path, hercules="/home/u/.local/bin/hercules", chromium=True)
    code, out, _ = _start(machine, env={"PATH": "/home/u/.local/bin"})
    assert code == 0
    assert not [command for command in machine.ran if "install" in command and "--dry-run" not in command], "nothing new is installed"
    assert "found /home/u/.local/bin/hercules" in out and "chromium is ready" in out and "not on your PATH" not in out


def test_an_installed_command_off_the_path_gets_the_path_offer(tmp_path: Path) -> None:
    machine = Machine(tmp_path, hercules="/home/u/.local/share/uv/tools/testzeus-hercules/bin/hercules", chromium=True)
    code, out, _ = _start(machine, "--yes")
    assert code == 0 and "is not on your PATH yet" in out and ["/usr/bin/uv", "tool", "update-shell"] in machine.ran


def test_the_hercules_inside_uvx_does_not_count_as_installed(tmp_path: Path) -> None:
    machine = Machine(tmp_path, hercules=str(tmp_path / ".cache" / "uv" / "archive-v0" / "abc" / "bin" / "hercules"), chromium=True)
    code, _, _ = _start(machine, "--yes")
    assert code == 0 and any(command[1:3] == ["tool", "install"] for command in machine.ran)


def test_yes_and_setup_flags_pass_through(tmp_path: Path) -> None:
    machine = Machine(tmp_path, chromium=True)
    code, out, setup_calls = _start(machine, "--yes", "--agents", "claude", "--egress", "local", "--no-browser")
    assert code == 0
    assert setup_calls == [["--yes", "--agents", "claude", "--egress", "local"]]
    assert "[Y/n] y (--yes)" in out and "Browsers to set up: none (--browsers)" in out


def test_pipx_is_used_without_uv_and_advice_without_either(tmp_path: Path) -> None:
    machine = Machine(tmp_path, tools=("pipx",), chromium=True)
    _start(machine, "--yes")
    assert ["/usr/bin/pipx", "install", "testzeus-hercules==2.0.0"] in machine.ran
    machine = Machine(tmp_path, tools=(), chromium=True)
    code, out, _ = _start(machine, "--yes")
    assert code == 0 and "neither uv nor pipx" in out and "pip install 'testzeus-hercules==2.0.0'" in out


def test_a_setup_failure_is_the_exit_code(tmp_path: Path) -> None:
    out = io.StringIO()
    code = main(["--no-install", "--no-browser"], env={}, deps=Machine(tmp_path).deps(), setup=lambda flags: 1, stdout=out)
    assert code == 1 and "Hercules is ready." not in out.getvalue()


def test_a_command_is_passed_to_hercules(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"], env={}) == 0
    assert re.search(r"\d", capsys.readouterr().out)


def test_help_names_the_own_flags_and_the_setup_flags() -> None:
    out = io.StringIO()
    assert main(["--help"], env={}, stdout=out) == 0
    assert "--no-install" in out.getvalue() and "testzeus-hercules setup --help" in out.getvalue()


@pytest.mark.parametrize(
    ("version", "env", "override", "expected"),
    [
        ("2.0.0", {}, None, "testzeus-hercules==2.0.0"),
        ("2.0.0rc1", {}, None, "testzeus-hercules==2.0.0rc1"),
        ("2.0.0+local", {}, None, "testzeus-hercules"),
        ("2.0.0.dev0", {}, None, "git+https://github.com/test-zeus-ai/testzeus-hercules.git"),
        ("unknown", {}, None, "testzeus-hercules"),
        ("2.0.0", {"HERCULES_INSTALL_FROM": "git+https://example.com/h.git"}, None, "git+https://example.com/h.git"),
        ("2.0.0", {"HERCULES_INSTALL_FROM": "x"}, "./dist/h.whl", "./dist/h.whl"),
    ],
)
def test_the_install_pins_this_version_unless_told_otherwise(version: str, env: dict[str, str], override: str | None, expected: str) -> None:
    assert install_spec(version, env, override) == expected


def test_temporary_environments_are_recognised() -> None:
    assert is_temporary_env(Path("/Users/u/.cache/uv/archive-v0/abc/bin"))
    assert is_temporary_env(Path("/Users/u/Library/Caches/pipx/abc"))
    assert not is_temporary_env(Path("/Users/u/.local/share/uv/tools/testzeus-hercules/bin"))


def test_chromium_named_by_the_environment_counts(tmp_path: Path) -> None:
    browser = tmp_path / "chrome"
    browser.write_text("")
    assert chromium_ready("/py", {"HERCULES_CHROMIUM": str(browser)}, lambda command, capture: Ran(1))
    assert not chromium_ready("/py", {}, lambda command, capture: Ran(1))


# --- the coding-agent setup prompt -------------------------------------------


def _prompt_block() -> str:
    text = (ROOT / "docs" / "guide" / "agent-setup.md").read_text()
    match = re.search(r"<!-- prompt:start -->\n```text\n(.*?)```\n<!-- prompt:end -->", text, re.S)
    assert match, "docs/guide/agent-setup.md must hold the prompt between the prompt markers"
    return match.group(1)


def test_the_readme_and_the_docs_carry_the_same_prompt() -> None:
    prompt = _prompt_block()
    assert prompt in (ROOT / "README.md").read_text()
    assert "uvx testzeus-hercules" in prompt


def test_the_prompt_only_names_flags_that_exist() -> None:
    import argparse

    from testzeus_hercules.cli.main import build_parser
    from testzeus_hercules.cli.quickstart import _own_parser

    known = {flag for action in _own_parser()._actions for flag in action.option_strings}
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                known |= {flag for child_action in child._actions for flag in child_action.option_strings}
    named = set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", _prompt_block()))
    assert named and named <= known, named - known


def test_the_prompt_keeps_the_key_out_of_the_chat() -> None:
    prompt = _prompt_block()
    assert "Never ask me" in prompt and "API key" in prompt


def test_the_one_line_start_is_at_the_top_of_the_readme() -> None:
    readme = (ROOT / "README.md").read_text()
    assert readme.index("uvx testzeus-hercules") < readme.index("## Three ways to use it")


# --- which browsers --------------------------------------------------------------------------------------------------


def _installs(machine: Machine) -> list[str]:
    return [command[-1] for command in machine.ran if command[:4] == ["/py", "-m", "playwright", "install"] and "--dry-run" not in command]


def test_the_browser_question_offers_every_engine_and_installs_the_answer(tmp_path: Path) -> None:
    machine = Machine(tmp_path, chromium=True)
    code, out, _ = _start(machine, "--no-install", answers=["firefox, webkit"])
    assert code == 0
    for name in ("chromium", "firefox", "webkit", "chrome", "msedge"):
        assert f"   {name} " in out
    assert _installs(machine) == ["firefox", "webkit"]


def test_a_mistyped_answer_is_asked_again(tmp_path: Path) -> None:
    machine = Machine(tmp_path, chromium=True)
    code, out, _ = _start(machine, "--no-install", answers=["firefx", "firefox"])
    assert code == 0 and "unknown browser 'firefx'" in out and _installs(machine) == ["firefox"]


def test_yes_sets_up_chromium_and_the_browsers_the_projects_environments_use(tmp_path: Path) -> None:
    envs = tmp_path / "project" / ".hercules" / "envs"
    envs.mkdir(parents=True)
    (envs / "de.json").write_text('{"name": "de", "base_url": "http://x.test", "browser": "webkit"}')
    (envs / "broken.json").write_text("{not json")
    machine = Machine(tmp_path)
    code, out, _ = _start(machine, "--yes", "--no-install", "--project", str(tmp_path / "project"))
    assert code == 0 and "(your environments use it)" in out
    assert _installs(machine) == ["chromium", "webkit"]


def test_browsers_flag_names_them_and_edge_means_msedge(tmp_path: Path) -> None:
    machine = Machine(tmp_path, chromium=True)
    code, out, _ = _start(machine, "--no-install", "--browsers", "edge,chromium")
    assert code == 0 and "installer may ask for your password" in out
    assert _installs(machine) == ["msedge"], "Chromium is already there"
    out_buffer = io.StringIO()
    assert main(["--browsers", "safari"], env={}, deps=machine.deps(), setup=lambda flags: 0, stdout=out_buffer) == 2
    assert "unknown browser 'safari'" in out_buffer.getvalue()


def test_an_installed_chrome_is_found_by_its_command(tmp_path: Path) -> None:
    machine = Machine(tmp_path, tools=("uv", "google-chrome"), chromium=True)
    code, out, _ = _start(machine, "--no-install", "--browsers", "chrome")
    assert code == 0 and "chrome is ready" in out and _installs(machine) == []


def test_the_browser_list_lines_up_ready_and_not_set_up(tmp_path: Path) -> None:
    machine = Machine(tmp_path, chromium=True)
    code, out, _ = _start(machine, "--no-install", answers=["none"])
    lines = {line.split()[0]: line for line in out.splitlines() if line.strip().split(" ")[0] in ("chromium", "firefox")}
    assert code == 0 and lines["chromium"].index("Playwright's") == lines["firefox"].index("Playwright's")
