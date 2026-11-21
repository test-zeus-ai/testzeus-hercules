"""Hardware tiers: pick the local model and per-run budgets from available memory.

Memory means total system RAM. On Apple Silicon that is the unified memory the
GPU uses, so it maps directly onto the hardware table. GPU VRAM is not
detected: on machines with a discrete GPU, set ``llm.tier`` explicitly.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from pydantic import BaseModel, ConfigDict

from testzeus_hercules.kernel.settings import Tier
from testzeus_hercules.llm.aliases import TIER_MODEL_ALIAS, TierName

logger = logging.getLogger(__name__)

GIB = 1024**3
# Operating systems report less than a machine's nominal memory (Linux MemTotal leaves
# out kernel reservations), so auto-selection accepts a total down to 92% of a tier's
# nominal size: a "16 GB" machine showing ~15.3 GiB still lands on the 16 GB tier.
_REPORTED_MEMORY_FACTOR = 0.92
_SYSCTL_TIMEOUT_S = 2.0
_MEMINFO = Path("/proc/meminfo")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
Sysconf = Callable[[str], int]


class TierProfile(BaseModel):
    """Model choice and budgets for one hardware tier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: TierName
    min_memory_gib: int
    model_alias: str
    context_tokens: int
    observation_max_elements: int
    observation_max_tokens: int
    vision_tokens: int
    parallel_runs: int


def _profile(name: TierName, min_gib: int, context: int, *, small_observation: bool = False, vision: int = 280, parallel: int = 1) -> TierProfile:
    return TierProfile(
        name=name,
        min_memory_gib=min_gib,
        model_alias=TIER_MODEL_ALIAS[name],
        context_tokens=context,
        observation_max_elements=80 if small_observation else 120,
        observation_max_tokens=2000 if small_observation else 2500,
        vision_tokens=vision,
        parallel_runs=parallel,
    )


# "unsupported" machines still run, with the floor settings.
TIER_PROFILES: Mapping[str, TierProfile] = MappingProxyType(
    {
        "unsupported": _profile("unsupported", 0, 16384, small_observation=True),
        "floor": _profile("floor", 16, 16384, small_observation=True),
        "standard": _profile("standard", 24, 16384),
        "recommended": _profile("recommended", 32, 32768),
        "comfortable": _profile("comfortable", 48, 65536, vision=560, parallel=2),
    }
)
_BY_MEMORY = ("comfortable", "recommended", "standard", "floor")


@dataclass(frozen=True)
class TierSelection:
    profile: TierProfile
    memory_bytes: int | None
    auto: bool
    warnings: tuple[str, ...] = ()


def select_tier(memory_bytes: int | None, requested: Tier) -> TierSelection:
    """Pick the tier for ``memory_bytes``; an explicit ``requested`` tier always wins."""
    if requested != "auto":
        profile = TIER_PROFILES[requested]
        warnings: tuple[str, ...] = ()
        if memory_bytes is not None and memory_bytes < profile.min_memory_gib * GIB:
            warnings = (f"tier '{requested}' expects at least {profile.min_memory_gib} GiB of memory; this machine has {_gib(memory_bytes)}. Expect swapping or out-of-memory errors.",)
        return TierSelection(profile, memory_bytes, auto=False, warnings=warnings)
    if memory_bytes is None:
        warning = "could not detect system memory; using the 'floor' tier. Set llm.tier (floor, standard, recommended, comfortable) to choose."
        return TierSelection(TIER_PROFILES["floor"], None, auto=True, warnings=(warning,))
    for name in _BY_MEMORY:
        profile = TIER_PROFILES[name]
        if memory_bytes >= profile.min_memory_gib * GIB * _REPORTED_MEMORY_FACTOR:
            return TierSelection(profile, memory_bytes, auto=True)
    warning = f"this machine has {_gib(memory_bytes)} of memory; Hercules needs at least 16 GiB. Running with the 'floor' settings; local models may be slow or fail."
    return TierSelection(TIER_PROFILES["unsupported"], memory_bytes, auto=True, warnings=(warning,))


def _gib(memory_bytes: int) -> str:
    return f"{memory_bytes / GIB:.1f} GiB"


def detect_memory_bytes(
    *,
    system: str | None = None,
    run: Runner = subprocess.run,
    meminfo: Path = _MEMINFO,
    sysconf: Sysconf | None = None,
) -> int | None:
    """Total physical memory in bytes, or None if it cannot be determined."""
    name = system if system is not None else platform.system()
    detected: int | None = None
    if name == "Darwin":
        detected = _sysctl_memsize(run)
    elif name == "Linux":
        detected = _meminfo_total(meminfo)
    return detected if detected is not None else _sysconf_total(sysconf if sysconf is not None else getattr(os, "sysconf", None))


def _sysctl_memsize(run: Runner) -> int | None:
    try:
        result = run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=_SYSCTL_TIMEOUT_S, check=False)
        return _positive(int(result.stdout.strip())) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.debug("sysctl hw.memsize failed: %s", exc.__class__.__name__)
        return None


def _meminfo_total(path: Path) -> int | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        if line.startswith("MemTotal:"):
            fields = line.split()
            try:
                return _positive(int(fields[1]) * 1024)
            except (IndexError, ValueError):
                return None
    return None


def _sysconf_total(sysconf: Sysconf | None) -> int | None:
    if sysconf is None:
        return None
    try:
        return _positive(sysconf("SC_PAGE_SIZE") * sysconf("SC_PHYS_PAGES"))
    except (OSError, ValueError, TypeError):
        return None


def _positive(value: int) -> int | None:
    return value if value > 0 else None
