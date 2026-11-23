"""The default install stays lean: no ML runtimes, no hosted-gateway SDKs in core.

Heavy or optional packages either live in an extra or are not needed at all, and the
modules that used them import lazily so the core imports without them.
"""

import ast
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

# Packages that must never be core dependencies of the local-first install.
FORBIDDEN_CORE = {"torch", "transformers", "numba", "unstructured", "portkey-ai", "playwright-recaptcha", "inflection", "python-json-logger", "langchain-openai"}


def _name(requirement: str) -> str:
    for separator in ("[", ">", "<", "=", "!", "~", ";", " "):
        requirement = requirement.split(separator, 1)[0]
    return requirement.strip().lower()


def test_core_dependencies_exclude_heavy_and_hosted_only_packages() -> None:
    core = {_name(requirement) for requirement in PYPROJECT["project"]["dependencies"]}

    assert core.isdisjoint(FORBIDDEN_CORE), sorted(core & FORBIDDEN_CORE)


def test_the_security_scanner_is_an_external_tool_not_a_dependency() -> None:
    every = {_name(requirement) for requirement in PYPROJECT["project"]["dependencies"]}
    every |= {_name(requirement) for extra in PYPROJECT["project"].get("optional-dependencies", {}).values() for requirement in extra}

    assert not any("nuclei" in name for name in every)


def test_unstructured_is_never_imported_anywhere() -> None:
    offenders = [str(path.relative_to(ROOT)) for path in (ROOT / "testzeus_hercules").rglob("*.py") if "unstructured" in path.read_text(encoding="utf-8")]

    assert offenders == []
