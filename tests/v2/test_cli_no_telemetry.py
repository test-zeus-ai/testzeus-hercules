"""The global --no-telemetry flag."""

import io
import os
from pathlib import Path

import pytest

from testzeus_hercules.cli import main as cli_main
from testzeus_hercules.cli.first_run import notice_marker


def _env(tmp_path: Path) -> dict[str, str]:
    return {"HERCULES_HOME": str(tmp_path / "home"), "HOME": str(tmp_path)}


@pytest.mark.parametrize("argv", [["--no-telemetry", "version"], ["version", "--no-telemetry"]])
def test_flag_is_accepted_before_or_after_the_command(argv: list[str]) -> None:
    args = cli_main.build_parser().parse_args(argv)

    assert args.no_telemetry is True


def test_flag_defaults_to_off() -> None:
    assert cli_main.build_parser().parse_args(["version"]).no_telemetry is False


def test_every_command_accepts_the_flag() -> None:
    parser = cli_main.build_parser()
    commands = next(action for action in parser._actions if action.dest == "command")
    for name, subparser in commands.choices.items():
        assert any("--no-telemetry" in action.option_strings for action in subparser._actions), name


def test_flag_suppresses_the_first_run_notice(tmp_path: Path) -> None:
    err = io.StringIO()

    cli_main.main(["--no-telemetry", "version"], env=_env(tmp_path), stdout=io.StringIO(), stderr=err)

    assert "telemetry" not in err.getvalue().lower()
    assert not notice_marker(tmp_path / "home").exists()


def test_without_flag_the_notice_is_shown(tmp_path: Path) -> None:
    err = io.StringIO()

    cli_main.main(["version"], env=_env(tmp_path), stdout=io.StringIO(), stderr=err)

    assert "PostHog" in err.getvalue()


def test_flag_given_a_mapping_leaves_it_unchanged() -> None:
    env = {"A": "1"}

    result = cli_main._without_telemetry(env)

    assert result == {"A": "1", "HERCULES_TELEMETRY": "0"}
    assert env == {"A": "1"}


def test_flag_with_the_process_environment_is_inherited_by_child_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERCULES_TELEMETRY", raising=False)

    assert cli_main._without_telemetry(None) is None
    assert os.environ["HERCULES_TELEMETRY"] == "0"
