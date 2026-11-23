"""``hercules run`` prints the evidence folder relative to the current folder when it is inside it."""

from __future__ import annotations

from pathlib import Path

import pytest

from testzeus_hercules.cli.run import _shown


def test_a_run_folder_inside_the_current_folder_is_shown_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert _shown(tmp_path / ".hercules" / "runs" / "r_1") == str(Path(".hercules", "runs", "r_1"))


def test_a_run_folder_elsewhere_is_shown_in_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "here").mkdir()
    monkeypatch.chdir(tmp_path / "here")
    elsewhere = tmp_path / "project" / ".hercules" / "runs" / "r_1"
    assert _shown(elsewhere) == str(elsewhere)
