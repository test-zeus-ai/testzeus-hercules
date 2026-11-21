"""scripts/release.py: which versions can be released, and where the version is written."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("release_script", ROOT / "scripts" / "release.py")
assert _spec is not None and _spec.loader is not None
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


@pytest.mark.parametrize("wanted", ["2.0.0", "2.0.0rc1", "2.0.0b2", "2.1.0"])
def test_final_and_pre_releases_newer_than_the_current_dev_version_are_accepted(wanted: str) -> None:
    assert str(release.check_version(wanted, "2.0.0.dev0")) == wanted


@pytest.mark.parametrize(
    ("wanted", "reason"),
    [("2.0.0.dev1", "development"), ("2.0.0+local", "local"), ("1.0.2", "not newer"), ("two", "not a PEP 440")],
)
def test_dev_local_older_and_invalid_versions_are_refused(wanted: str, reason: str) -> None:
    with pytest.raises(SystemExit, match=reason):
        release.check_version(wanted, "2.0.0.dev0")


def test_the_version_is_written_to_pyproject_and_the_dockerfile(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "2.0.0.dev0"\n[tool.x]\nversion = "keep"\n')
    (tmp_path / "Dockerfile").write_text("FROM x\nARG VERSION=2.0.0.dev0\nARG REVISION=unknown\n")

    release.set_version(tmp_path, release.check_version("2.0.0", "2.0.0.dev0"))

    assert release.current_version(tmp_path) == "2.0.0"
    assert 'version = "keep"' in (tmp_path / "pyproject.toml").read_text()
    assert "ARG VERSION=2.0.0\n" in (tmp_path / "Dockerfile").read_text()


def test_the_repository_files_have_the_version_lines_it_rewrites() -> None:
    assert release.current_version(ROOT)
    assert release._DOCKER_VERSION.search((ROOT / "Dockerfile").read_text())
