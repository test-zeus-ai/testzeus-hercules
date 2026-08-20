"""Regression tests for long term memory reloading across project bases.

Bulk runs iterate several project bases inside one process, repointing
TEST_DATA_PATH at each. StaticLTM used to cache the first folder it saw for
the lifetime of the process, so every suite after the first was handed the
first suite's test data.
"""

import os

from testzeus_hercules.config import set_global_conf
from testzeus_hercules.core.memory.static_ltm import StaticLTM, get_user_ltm


def _make_suite(tmp_path, name: str, body: str) -> str:
    """Create a project base holding a single test_data file."""
    test_data_dir = tmp_path / name / "test_data"
    test_data_dir.mkdir(parents=True)
    (test_data_dir / "test_data.txt").write_text(body, encoding="utf-8")
    return str(test_data_dir)


def _point_at(test_data_dir: str) -> None:
    set_global_conf({"TEST_DATA_PATH": test_data_dir}, override=True)


def test_switching_test_data_path_reloads_memory(tmp_path) -> None:
    """Each project base must see its own test data, not the first one's."""
    # Cleared through the attribute rather than reset() so this test fails on
    # its own assertion against the unfixed cache, not on a missing method.
    StaticLTM._instance = None
    first = _make_suite(tmp_path, "suite_one", "ALPHA_MARKER")
    second = _make_suite(tmp_path, "suite_two", "BETA_MARKER")

    _point_at(first)
    assert "ALPHA_MARKER" in (get_user_ltm() or "")

    _point_at(second)
    memory = get_user_ltm() or ""
    assert "BETA_MARKER" in memory
    assert "ALPHA_MARKER" not in memory, "second suite inherited the first suite's test data"


def test_repeated_reads_of_one_path_reuse_the_cache(tmp_path) -> None:
    """Reading twice from the same path must not rebuild the memory."""
    StaticLTM.reset()
    suite = _make_suite(tmp_path, "suite_stable", "GAMMA_MARKER")

    _point_at(suite)
    first_read = StaticLTM()
    second_read = StaticLTM()

    assert first_read is second_read
    assert "GAMMA_MARKER" in (get_user_ltm() or "")


def test_reset_forces_a_reload_from_disk(tmp_path) -> None:
    """reset() drops the cache even when the path has not changed."""
    StaticLTM.reset()
    suite = _make_suite(tmp_path, "suite_rewritten", "BEFORE_MARKER")
    _point_at(suite)
    assert "BEFORE_MARKER" in (get_user_ltm() or "")

    with open(os.path.join(suite, "test_data.txt"), "w", encoding="utf-8") as handle:
        handle.write("AFTER_MARKER")

    StaticLTM.reset()
    memory = get_user_ltm() or ""
    assert "AFTER_MARKER" in memory
    assert "BEFORE_MARKER" not in memory
