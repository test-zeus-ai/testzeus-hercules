"""The OpenRouter key: environment first, else a private key file in the Hercules home; never printed."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from testzeus_hercules.cli.keys import find_key, key_file, with_key, write_key_file

SECRET = "sk-or-test-not-a-real-key-123"


def test_the_environment_wins_over_the_file(tmp_path: Path) -> None:
    write_key_file(tmp_path, "from-file")

    value, status = find_key({"OPENROUTER_API_KEY": SECRET}, tmp_path)

    assert value == SECRET and status.source == "env"


def test_a_private_key_file_is_read_when_the_variable_is_unset(tmp_path: Path) -> None:
    path = write_key_file(tmp_path, SECRET)

    env, status = with_key({"OTHER": "x"}, tmp_path)

    assert path == key_file(tmp_path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert env == {"OTHER": "x", "OPENROUTER_API_KEY": SECRET}
    assert status.source == "file" and status.found
    assert SECRET not in status.describe() and SECRET not in repr(status)


@pytest.mark.parametrize("line", [f"OPENROUTER_API_KEY={SECRET}", f"export OPENROUTER_API_KEY='{SECRET}'", f'# comment\nOPENROUTER_API_KEY="{SECRET}"'])
def test_key_file_lines_may_be_exported_or_quoted(tmp_path: Path, line: str) -> None:
    path = key_file(tmp_path)
    path.write_text(line + "\n")
    path.chmod(0o600)

    value, _ = find_key({}, tmp_path)

    assert value == SECRET


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")
def test_a_key_file_others_can_read_is_ignored_with_a_fix(tmp_path: Path) -> None:
    path = key_file(tmp_path)
    path.write_text(f"OPENROUTER_API_KEY={SECRET}\n")
    path.chmod(0o644)

    env, status = with_key({}, tmp_path)

    assert "OPENROUTER_API_KEY" not in env
    assert status.source == "none"
    assert status.problem is not None and "chmod 600" in status.problem and SECRET not in status.problem


def test_another_variable_name_is_honoured(tmp_path: Path) -> None:
    write_key_file(tmp_path, SECRET, variable="MY_JEV_KEY")

    value, status = find_key({}, tmp_path, "MY_JEV_KEY")

    assert value == SECRET and status.variable == "MY_JEV_KEY"


def test_a_missing_file_is_not_a_problem(tmp_path: Path) -> None:
    value, status = find_key({}, tmp_path)

    assert value is None and status.problem is None
    assert "not set" in status.describe()


@pytest.mark.parametrize("bad", ["", "two\nlines"])
def test_write_refuses_an_empty_or_multi_line_key(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        write_key_file(tmp_path, bad)
