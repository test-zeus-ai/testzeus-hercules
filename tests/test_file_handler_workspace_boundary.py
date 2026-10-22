"""Safety regression tests for the findings file tools (persist/recall/augment).

The file tools must be confined to the workspace root (PROJECT_SOURCE_ROOT):

- writes/reads outside the workspace (absolute paths and ../ traversal) are
  rejected with an error string naming the allowed root
- symlinks that escape the workspace are refused on write (POSIX)
- a symlinked final path component is refused on write via O_NOFOLLOW (POSIX)
- inside-workspace write/read/augment keeps working
"""

import json
import os

import pytest
from testzeus_hercules.config import SingletonConfigManager, set_global_conf
from testzeus_hercules.core.extra_tools.file_handler_tool import (
    augment_findings,
    persist_findings,
    recall_findings,
)


@pytest.fixture()
def workspace(tmp_path):
    """Point the global config singleton at an isolated workspace root."""
    root = tmp_path / "workspace"
    root.mkdir()
    os.environ["IS_TEST_ENV"] = "true"

    SingletonConfigManager.reset_instance()
    try:
        set_global_conf(
            {"PROJECT_SOURCE_ROOT": str(root), "LLM_MODEL_NAME": "test-model", "LLM_MODEL_API_KEY": "test-key"},
            ignore_env=True,
        )
        yield root
    finally:
        SingletonConfigManager.reset_instance()


# ── inside-workspace behavior still works ─────────────────────────────────────


def test_relative_path_write_read_roundtrip(workspace) -> None:
    result = persist_findings("findings/results.json", '{"answer": 42}')
    assert result == "Successfully wrote data to findings/results.json"
    assert json.loads((workspace / "findings" / "results.json").read_text(encoding="utf-8")) == {"answer": 42}
    assert recall_findings("findings/results.json") == {"answer": 42}


def test_absolute_path_inside_workspace_write_read_roundtrip(workspace) -> None:
    target = workspace / "abs.txt"
    assert persist_findings(str(target), "hello") == f"Successfully wrote data to {target}"
    assert recall_findings(str(target)) == "hello"


def test_augment_inside_workspace_appends_and_merges(workspace) -> None:
    # first augment on a missing file delegates to persist (create)
    assert augment_findings("notes.log", "one") == "Successfully wrote data to notes.log"
    assert augment_findings("notes.log", "two") == "Successfully appended data to notes.log"
    assert (workspace / "notes.log").read_text(encoding="utf-8") == "onetwo"

    assert augment_findings("data.json", '{"a": 1}') == "Successfully wrote data to data.json"
    assert augment_findings("data.json", '{"b": 2}') == "Successfully appended data to data.json"
    assert json.loads((workspace / "data.json").read_text(encoding="utf-8")) == {"a": 1, "b": 2}


# ── outside-workspace rejection ───────────────────────────────────────────────


def test_persist_rejects_absolute_path_outside_workspace(tmp_path, workspace) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    result = persist_findings(str(outside / "victim.json"), '{"a": 1}')

    assert result.startswith("Error writing file:")
    assert "outside the workspace root" in result
    assert str(workspace) in result  # the error lists the allowed root
    assert not (outside / "victim.json").exists()


def test_recall_rejects_absolute_path_outside_workspace(tmp_path, workspace) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("TOPSECRET", encoding="utf-8")

    result = recall_findings(str(secret))

    assert result.startswith("Error reading file:")
    assert "outside the workspace root" in result
    assert str(workspace) in result
    assert "TOPSECRET" not in result


def test_persist_rejects_traversal_escape(workspace) -> None:
    result = persist_findings("../escaped.txt", "nope")

    assert result.startswith("Error writing file:")
    assert "outside the workspace root" in result
    assert not (workspace.parent / "escaped.txt").exists()

    nested_result = persist_findings("sub/../../escaped2.txt", "nope")
    assert nested_result.startswith("Error writing file:")
    assert not (workspace.parent / "escaped2.txt").exists()


def test_recall_rejects_traversal_escape(workspace) -> None:
    secret = workspace.parent / "secret.log"
    secret.write_text("TOPSECRET", encoding="utf-8")

    result = recall_findings("../secret.log")

    assert result.startswith("Error reading file:")
    assert "TOPSECRET" not in result


def test_augment_rejects_escape(workspace) -> None:
    result = augment_findings("../escaped.log", "nope")

    assert result.startswith("Error appending file:")
    assert "outside the workspace root" in result
    assert not (workspace.parent / "escaped.log").exists()


# ── symlink handling ──────────────────────────────────────────────────────────


@pytest.mark.skipif(os.name != "posix", reason="symlink semantics are POSIX-only")
def test_persist_refuses_symlink_pointing_outside(workspace, tmp_path) -> None:
    outside_file = tmp_path / "outside" / "target.json"
    outside_file.parent.mkdir(exist_ok=True)
    outside_file.write_text("ORIGINAL", encoding="utf-8")
    link = workspace / "link.json"
    os.symlink(outside_file, link)

    result = persist_findings("link.json", '{"pwned": true}')

    assert result.startswith("Error writing file:")
    assert outside_file.read_text(encoding="utf-8") == "ORIGINAL"


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW is POSIX-only")
def test_persist_refuses_symlinked_target_via_o_nofollow(workspace) -> None:
    real = workspace / "real.json"
    real.write_text("{}", encoding="utf-8")
    alias = workspace / "alias.json"
    os.symlink(real, alias)

    result = persist_findings("alias.json", '{"a": 2}')

    assert result.startswith("Error writing file:")
    assert real.read_text(encoding="utf-8") == "{}"


# ── error paths keep returning strings ────────────────────────────────────────


def test_unsupported_extension_returns_error_string(workspace) -> None:
    assert persist_findings("file.exe", "x").startswith("Error writing file:")
    assert recall_findings("file.exe").startswith("Error reading file:")


# ── configured test-data directory outside the workspace stays usable ─────────


@pytest.fixture()
def workspace_with_external_test_data(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    test_data = tmp_path / "shared_test_data"
    test_data.mkdir()
    os.environ["IS_TEST_ENV"] = "true"
    SingletonConfigManager.reset_instance()
    try:
        set_global_conf(
            {"PROJECT_SOURCE_ROOT": str(root), "TEST_DATA_PATH": str(test_data), "LLM_MODEL_NAME": "test-model", "LLM_MODEL_API_KEY": "test-key"},
            ignore_env=True,
        )
        yield root, test_data
    finally:
        SingletonConfigManager.reset_instance()


def test_absolute_path_in_external_test_data_dir_allowed(workspace_with_external_test_data) -> None:
    _root, test_data = workspace_with_external_test_data
    target = test_data / "users.json"
    assert persist_findings(str(target), '{"u": 1}') == f"Successfully wrote data to {target}"
    assert recall_findings(str(target)) == {"u": 1}


def test_relative_read_falls_back_to_test_data_dir(workspace_with_external_test_data) -> None:
    _root, test_data = workspace_with_external_test_data
    (test_data / "users.json").write_text('{"u": 2}', encoding="utf-8")
    assert recall_findings("users.json") == {"u": 2}


def test_external_test_data_does_not_open_other_directories(workspace_with_external_test_data, tmp_path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    result = persist_findings(str(other / "x.txt"), "no")
    assert result.startswith("Error writing file:") and "outside the workspace root" in result
    assert not (other / "x.txt").exists()
