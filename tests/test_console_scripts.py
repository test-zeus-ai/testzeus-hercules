"""pyproject console scripts must point at importable callables (and uvicorn must be declared)."""

import importlib
import pathlib
import tomllib

import pytest

PYPROJECT = tomllib.loads((pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))

EXPECTED_SCRIPTS = {"hercules-suite", "hercules-dashboard", "hercules-monitor", "hercules-run-manager", "testzeus-hercules-mcp"}


def test_expected_console_scripts_registered() -> None:
    assert EXPECTED_SCRIPTS <= set(PYPROJECT["project"]["scripts"])


@pytest.mark.parametrize("name", sorted(EXPECTED_SCRIPTS))
def test_console_script_target_is_callable(name) -> None:
    module_name, _, attr = PYPROJECT["project"]["scripts"][name].partition(":")
    assert callable(getattr(importlib.import_module(module_name), attr))


def test_uvicorn_declared_as_dependency() -> None:
    assert any(dep.lower().startswith("uvicorn") for dep in PYPROJECT["project"]["dependencies"])
