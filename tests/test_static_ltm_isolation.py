"""Offline isolation tests for the StaticLTM long-term-memory cache.

StaticLTM is a process-lifetime singleton; bulk runs repoint TEST_DATA_PATH per
suite inside one process, so the cache must be keyed on the configured
test-data path:
1. switching the path mid-process reloads the memory — the second suite sees
   only its own test data, never the first suite's;
2. repeated reads at the same path still hit the cache (same instance);
3. ``StaticLTM.reset()`` drops the cache so an in-place rewrite on disk is
   picked up by the next read;
4. switching the path also drops the process-global stored/run state so one
   suite's stored secrets never leak into another suite's LTM, while same-path
   reads keep that state (within-suite sharing is intended).

Everything reads real files from tmp_path; no network, no LLM.
"""

from collections import deque

import pytest
from testzeus_hercules.config import SingletonConfigManager, set_global_conf
from testzeus_hercules.core.memory.state_handler import _state_dict, _state_string
from testzeus_hercules.core.memory.static_ltm import StaticLTM, get_user_ltm


@pytest.fixture()
def isolated_conf(tmp_path):
    """Save/restore the global config singleton, the StaticLTM cache and the
    process-global state_handler stores."""
    original_conf = SingletonConfigManager._instance
    original_instance = StaticLTM._instance
    original_loaded_for = StaticLTM._loaded_for
    original_string_state = dict(_state_string)
    original_dict_state = {key: deque(values) for key, values in _state_dict.items()}
    _state_string.clear()
    _state_dict.clear()
    yield tmp_path
    SingletonConfigManager._instance = original_conf
    StaticLTM._instance = original_instance
    StaticLTM._loaded_for = original_loaded_for
    _state_string.clear()
    _state_string.update(original_string_state)
    _state_dict.clear()
    _state_dict.update(original_dict_state)


def _write_suite(tmp_path, suite_name: str, marker: str) -> str:
    data_dir = tmp_path / suite_name / "test_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "test_data.txt").write_text(marker, encoding="utf-8")
    return str(data_dir)


def _point_test_data(tmp_path, test_data_path: str) -> None:
    set_global_conf({"PROJECT_SOURCE_ROOT": str(tmp_path), "TEST_DATA_PATH": test_data_path}, override=True)


def test_switching_test_data_path_reloads_memory(isolated_conf, tmp_path) -> None:
    suite_a = _write_suite(tmp_path, "suite_a", "MARKER_SUITE_A")
    suite_b = _write_suite(tmp_path, "suite_b", "MARKER_SUITE_B")

    _point_test_data(tmp_path, suite_a)
    first = get_user_ltm()
    assert "MARKER_SUITE_A" in first

    # The bulk loop repoints TEST_DATA_PATH for the next suite inside the same
    # process; the memory must be rebuilt from the new folder.
    _point_test_data(tmp_path, suite_b)
    second = get_user_ltm()

    assert "MARKER_SUITE_B" in second, "the second suite must see its own test data"
    assert "MARKER_SUITE_A" not in second, "suite A's test data leaked into suite B"
    assert StaticLTM._loaded_for == suite_b


def test_same_path_double_read_hits_cache(isolated_conf, tmp_path) -> None:
    suite_a = _write_suite(tmp_path, "suite_a", "MARKER_SUITE_A")

    _point_test_data(tmp_path, suite_a)

    first = StaticLTM()
    second = StaticLTM()

    assert first is second, "repeated reads at the same path must reuse the cached instance"
    assert "MARKER_SUITE_A" in first.get_user_ltm()
    assert StaticLTM._loaded_for == suite_a


def test_reset_forces_disk_reload(isolated_conf, tmp_path) -> None:
    data_dir = tmp_path / "suite_a" / "test_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    data_file = data_dir / "test_data.txt"
    data_file.write_text("MARKER_V1", encoding="utf-8")

    _point_test_data(tmp_path, str(data_dir))

    assert "MARKER_V1" in get_user_ltm()

    # An in-place rewrite on disk is invisible to the cached instance...
    data_file.write_text("MARKER_V2", encoding="utf-8")
    assert "MARKER_V2" not in get_user_ltm()

    # ...until the cache is explicitly dropped and the next read reloads.
    StaticLTM.reset()
    assert StaticLTM._instance is None
    reloaded = get_user_ltm()
    assert "MARKER_V2" in reloaded
    assert "MARKER_V1" not in reloaded


# ── runtime stored/run state must not leak across bulk-mode suites ────────────
# The state_handler stores are process-global (keyed by the per-scenario test
# id); when a bulk run repoints TEST_DATA_PATH, the previous suite's stored
# secrets used to be folded into the next suite's LTM.


def test_stored_runtime_state_does_not_leak_across_suites(isolated_conf, tmp_path) -> None:
    from testzeus_hercules.core.memory.state_handler import store_data

    suite_a = _write_suite(tmp_path, "suite_a", "MARKER_SUITE_A")
    suite_b = _write_suite(tmp_path, "suite_b", "MARKER_SUITE_B")

    _point_test_data(tmp_path, suite_a)
    assert "MARKER_SUITE_A" in get_user_ltm()

    # A scenario of suite A stores a secret through the store_data tool.
    assert "error" not in store_data("SECRET_SUITE_A_TOKEN")

    # The bulk loop repoints TEST_DATA_PATH for suite B inside the same process.
    _point_test_data(tmp_path, suite_b)
    suite_b_ltm = get_user_ltm()

    assert "MARKER_SUITE_B" in suite_b_ltm, "suite B must still see its own test data"
    assert "SECRET_SUITE_A_TOKEN" not in suite_b_ltm, "suite A's stored secret leaked into suite B's LTM"
    assert "MARKER_SUITE_A" not in suite_b_ltm


def test_same_path_read_retains_within_suite_stored_data(isolated_conf, tmp_path) -> None:
    from testzeus_hercules.core.memory.state_handler import get_stored_data, store_data

    suite_a = _write_suite(tmp_path, "suite_a", "MARKER_SUITE_A")

    _point_test_data(tmp_path, suite_a)
    first = StaticLTM()
    assert "error" not in store_data("SHARED_SUITE_STATE")

    # A second read at the SAME path must keep the runtime state: cross-scenario
    # sharing within a suite is intended.
    second = StaticLTM()
    assert first is second, "repeated reads at the same path must reuse the cached instance"
    assert "SHARED_SUITE_STATE" in get_stored_data()

    # Even a forced reload at the same path must fold the stored data back in.
    StaticLTM.reset()
    reloaded = StaticLTM()
    assert "SHARED_SUITE_STATE" in reloaded.get_user_ltm()
