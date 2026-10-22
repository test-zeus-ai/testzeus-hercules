import os
import subprocess
import sys
from pathlib import Path


def test_config_import_does_not_require_playwright(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["AUTO_MODE"] = "1"
    env["ENABLE_TELEMETRY"] = "1"
    env["IS_TEST_ENV"] = "true"
    env["PYTHONPATH"] = str(repo_root)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.abc
import sys


class BlockPlaywright(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "playwright" or fullname.startswith("playwright."):
            raise ImportError("blocked playwright")
        return None


sys.meta_path.insert(0, BlockPlaywright())
from testzeus_hercules.config import SingletonConfigManager, get_global_conf, set_global_conf

print(SingletonConfigManager.__name__, callable(set_global_conf), callable(get_global_conf))
print("testzeus_hercules.core" in sys.modules)
""",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert "SingletonConfigManager True True" in result.stdout
    assert result.stdout.rstrip().endswith("False")


def test_llm_helper_import_does_not_eagerly_import_agents(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["AUTO_MODE"] = "1"
    env["ENABLE_TELEMETRY"] = "1"
    env["IS_TEST_ENV"] = "true"
    env["PYTHONPATH"] = str(repo_root)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys

from testzeus_hercules.utils.llm_helper import create_chat_model

print(callable(create_chat_model))
print("testzeus_hercules.core.agents" in sys.modules)
""",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-2:] == ["True", "False"]
