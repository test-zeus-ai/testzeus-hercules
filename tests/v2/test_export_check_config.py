"""The export check runs pytest with a config of its own, so a parent folder's pytest settings cannot change it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from testzeus_hercules.export.check import _own_config


def test_a_parent_folders_pytest_settings_do_not_reach_the_check(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "--no-such-option"\n')
    folder = tmp_path / "run" / "export-check"
    folder.mkdir(parents=True)
    (folder / "test_ok.py").write_text("def test_ok():\n    assert True\n")

    done = subprocess.run([sys.executable, "-m", "pytest", *_own_config(folder), "-q", "-p", "no:cacheprovider", "test_ok.py"], cwd=folder, capture_output=True, text=True)

    assert done.returncode == 0, done.stdout + done.stderr
