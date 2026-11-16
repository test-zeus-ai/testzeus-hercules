"""The telemetry notice, shown once per Hercules home on the first command that runs.

Telemetry is on by default, so the first ``hercules`` command prints
:data:`~testzeus_hercules.kernel.telemetry.TELEMETRY_NOTICE` (what is sent, what never
is, how to opt out) to stderr and records that it was shown in
``<hercules home>/telemetry_notice_shown``. Nothing is printed when telemetry is
already off. Commands that speak a protocol on their streams (``worker``, ``mcp``)
never print it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from testzeus_hercules.kernel.settings import Settings, SettingsError
from testzeus_hercules.kernel.telemetry import TELEMETRY_NOTICE, telemetry_enabled

__all__ = ["NOTICE_MARKER", "notice_marker", "record_notice_shown", "show_telemetry_notice_once"]

NOTICE_MARKER = "telemetry_notice_shown"
_QUIET_COMMANDS = frozenset({"worker", "mcp"})
_PRIVATE_DIR = 0o700


def _home(env: Mapping[str, str]) -> tuple[Path, bool]:
    """The Hercules home and whether telemetry is enabled (on when the settings cannot be read; off under egress ``local``)."""
    try:
        settings = Settings.load(env=env)
    except SettingsError:
        home = env.get("HERCULES_HOME") or os.path.join(env.get("HOME") or str(Path.home()), ".hercules")
        return Path(home).expanduser(), True
    return settings.home_dir, telemetry_enabled(settings)


def notice_marker(home: Path) -> Path:
    return home / NOTICE_MARKER


def record_notice_shown(home: Path) -> None:
    """Remember that the notice was shown (best effort: a read-only home just shows it again)."""
    try:
        home.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR)
        notice_marker(home).write_text(datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n", encoding="utf-8")
    except OSError:
        pass


def show_telemetry_notice_once(command: str | None, env: Mapping[str, str], err: TextIO) -> bool:
    """Print the telemetry notice if it was never shown for this home; True when it was printed now."""
    if command is None or command in _QUIET_COMMANDS:
        return False
    home, enabled = _home(env)
    if not enabled or notice_marker(home).exists():
        return False
    err.write(f"{TELEMETRY_NOTICE}\n(This notice is shown once.)\n\n")
    err.flush()
    record_notice_shown(home)
    return True
