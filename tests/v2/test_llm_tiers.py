"""Tests for memory detection and model tier selection."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.llm.aliases import GEMMA_4_26B_A4B, GEMMA_4_E4B
from testzeus_hercules.llm.tiers import GIB, TIER_PROFILES, detect_memory_bytes, select_tier


@pytest.mark.parametrize(
    ("gib", "tier"),
    [
        (8, "unsupported"),
        (14.5, "unsupported"),
        # The OS reports less than the nominal size (Linux MemTotal excludes kernel
        # reservations), so a "16 GB" machine showing ~15.3 GiB is still the floor tier.
        (15.3, "floor"),
        (16, "floor"),
        (21.9, "floor"),
        (22.5, "standard"),
        (24, "standard"),
        (29.3, "standard"),
        (30.5, "recommended"),
        (32, "recommended"),
        (44.0, "recommended"),
        (45.0, "comfortable"),
        (48, "comfortable"),
        (128, "comfortable"),
    ],
)
def test_auto_tier_boundaries(gib: float, tier: str) -> None:
    selection = select_tier(int(gib * GIB), "auto")

    assert selection.profile.name == tier
    assert selection.auto is True


def test_unsupported_machine_warns_and_runs_with_floor_settings() -> None:
    selection = select_tier(8 * GIB, "auto")
    floor = TIER_PROFILES["floor"]

    assert selection.warnings and "16 GiB" in selection.warnings[0]
    assert selection.profile.model_alias == floor.model_alias == GEMMA_4_E4B
    assert selection.profile.context_tokens == floor.context_tokens == 16384
    assert selection.profile.observation_max_elements == 80


def test_tier_profiles_match_the_hardware_table() -> None:
    floor, standard, recommended, comfortable = (TIER_PROFILES[name] for name in ("floor", "standard", "recommended", "comfortable"))

    assert (floor.model_alias, floor.context_tokens, floor.observation_max_elements, floor.observation_max_tokens) == (GEMMA_4_E4B, 16384, 80, 2000)
    assert (standard.model_alias, standard.context_tokens, standard.vision_tokens) == (GEMMA_4_26B_A4B, 16384, 280)
    assert (recommended.context_tokens, recommended.observation_max_elements, recommended.observation_max_tokens) == (32768, 120, 2500)
    assert (comfortable.context_tokens, comfortable.vision_tokens, comfortable.parallel_runs) == (65536, 560, 2)
    assert recommended.parallel_runs == standard.parallel_runs == floor.parallel_runs == 1


def test_explicit_tier_overrides_memory_without_warning_when_it_fits() -> None:
    selection = select_tier(64 * GIB, "standard")

    assert selection.profile.name == "standard"
    assert selection.auto is False
    assert selection.warnings == ()


def test_explicit_tier_above_the_machine_warns() -> None:
    selection = select_tier(16 * GIB, "comfortable")

    assert selection.profile.name == "comfortable"
    assert any("comfortable" in warning and "48 GiB" in warning for warning in selection.warnings)


def test_unknown_memory_falls_back_to_floor_with_a_warning() -> None:
    selection = select_tier(None, "auto")

    assert selection.profile.name == "floor"
    assert selection.warnings and "llm.tier" in selection.warnings[0]


def test_unknown_memory_with_explicit_tier_has_no_warning() -> None:
    assert select_tier(None, "recommended").warnings == ()


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_detect_memory_on_macos_uses_sysctl() -> None:
    calls: list[Any] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return _completed("34359738368\n")

    assert detect_memory_bytes(system="Darwin", run=run) == 32 * GIB
    assert calls[0][0] == ["sysctl", "-n", "hw.memsize"]
    assert calls[0][1]["timeout"] > 0


def test_detect_memory_on_linux_reads_meminfo(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16384000 kB\nMemFree:  1 kB\n", encoding="utf-8")

    assert detect_memory_bytes(system="Linux", meminfo=meminfo) == 16384000 * 1024


def test_detect_memory_falls_back_to_sysconf(tmp_path: Path) -> None:
    def sysconf(name: str) -> int:
        return {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 1024}[name]

    assert detect_memory_bytes(system="Linux", meminfo=tmp_path / "missing", sysconf=sysconf) == 4096 * 1024


def test_detect_memory_returns_none_when_everything_fails(tmp_path: Path) -> None:
    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, 1)

    def sysconf(name: str) -> int:
        raise ValueError(name)

    assert detect_memory_bytes(system="Darwin", run=run, sysconf=sysconf) is None
    assert detect_memory_bytes(system="Darwin", run=lambda *a, **k: _completed("garbage"), sysconf=sysconf) is None
    assert detect_memory_bytes(system="Linux", meminfo=tmp_path / "missing", sysconf=sysconf) is None
