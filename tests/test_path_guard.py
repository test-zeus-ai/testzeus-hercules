"""Tests for the shared path confinement helper (utils/path_guard)."""

import os

import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.path_guard import (
    PathConfinementError,
    allowed_roots,
    resolve_in_roots,
)


@pytest.fixture
def roots(monkeypatch, tmp_path):
    test_data = tmp_path / "project" / "test_data"
    test_data.mkdir(parents=True)
    config = get_global_conf().get_config()
    monkeypatch.setitem(config, "PROJECT_SOURCE_ROOT", str(tmp_path / "project"))
    monkeypatch.setitem(config, "TEST_DATA_PATH", str(test_data))
    monkeypatch.setitem(config, "API_ALLOWED_PATHS", "")
    return tmp_path


def test_allowed_roots_are_test_data_project_and_extras(roots, monkeypatch, tmp_path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    monkeypatch.setitem(get_global_conf().get_config(), "API_ALLOWED_PATHS", str(extra))
    resolved = allowed_roots()
    assert os.path.realpath(tmp_path / "project" / "test_data") in resolved
    assert os.path.realpath(tmp_path / "project") in resolved
    assert os.path.realpath(extra) in resolved


def test_absolute_path_inside_a_root_is_accepted(roots) -> None:
    target = roots / "project" / "test_data" / "a.json"
    target.write_text("{}")
    assert resolve_in_roots(str(target)) == os.path.realpath(target)


def test_relative_path_prefers_test_data(roots) -> None:
    (roots / "project" / "test_data" / "a.json").write_text("{}")
    (roots / "project" / "a.json").write_text("{}")
    assert resolve_in_roots("a.json") == os.path.realpath(roots / "project" / "test_data" / "a.json")


def test_missing_file_inside_a_root_is_returned_not_refused(roots) -> None:
    assert resolve_in_roots("nope.json").endswith("nope.json")


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../../etc/passwd", "", "   ", "a\x00b"])
def test_paths_outside_the_roots_or_invalid_are_refused(roots, bad) -> None:
    with pytest.raises(PathConfinementError):
        resolve_in_roots(bad)


def test_symlink_pointing_outside_is_refused(roots, tmp_path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    (roots / "project" / "test_data" / "link.txt").symlink_to(outside)
    with pytest.raises(PathConfinementError):
        resolve_in_roots("link.txt")


def test_sibling_directory_with_a_common_prefix_is_refused(roots, tmp_path) -> None:
    sibling = tmp_path / "project-evil"
    sibling.mkdir()
    (sibling / "x.txt").write_text("x")
    with pytest.raises(PathConfinementError):
        resolve_in_roots(str(sibling / "x.txt"))


@pytest.mark.parametrize("name", [".env", "prod.env", "agents_llm_config.json", "server.pem", "id.key", "hercules_history.db", "sub/../.env"])
def test_sensitive_files_inside_a_root_are_refused(roots, name) -> None:
    test_data = roots / "project" / "test_data"
    (test_data / "sub").mkdir(exist_ok=True)
    (test_data / os.path.basename(name)).write_text("SECRET=1")
    with pytest.raises(PathConfinementError):
        resolve_in_roots(name)
    with pytest.raises(PathConfinementError):
        resolve_in_roots(str(test_data / os.path.basename(name)))


def test_harmless_link_to_a_sensitive_file_is_refused(roots) -> None:
    test_data = roots / "project" / "test_data"
    (test_data / ".env").write_text("SECRET=1")
    (test_data / "upload.txt").symlink_to(test_data / ".env")
    with pytest.raises(PathConfinementError):
        resolve_in_roots("upload.txt")


def test_dot_directory_reference_is_not_treated_as_a_dotfile(roots) -> None:
    assert resolve_in_roots(".") == os.path.realpath(roots / "project" / "test_data")


def test_overbroad_roots_are_ignored(tmp_path) -> None:
    home_file = os.path.expanduser("~")
    with pytest.raises(PathConfinementError):
        resolve_in_roots("/etc/hosts", roots=["/"])
    with pytest.raises(PathConfinementError):
        resolve_in_roots(home_file, roots=[os.path.expanduser("~")])
