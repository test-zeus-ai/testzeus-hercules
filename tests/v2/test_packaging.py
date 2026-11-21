"""The wheel must carry every v2 subpackage and the published JSON Schemas."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETUPTOOLS = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]


def test_all_subpackages_are_discovered() -> None:
    assert SETUPTOOLS["packages"]["find"]["include"] == ["testzeus_hercules*"]


def test_case_schemas_are_package_data() -> None:
    assert "schemas/*.json" in SETUPTOOLS["package-data"]["testzeus_hercules.cases"]
    assert list((ROOT / "testzeus_hercules" / "cases" / "schemas").glob("*.schema.json"))
