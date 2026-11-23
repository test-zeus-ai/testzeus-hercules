"""The run's browser process on disk, so a supervisor can stop it when the worker that started it dies.

Playwright starts Chromium in its own process group (detached from the worker), so signalling the
worker's group leaves the browser running when a worker crashes or is killed. After the browser
starts, the worker asks it for its own process id over CDP (``SystemInfo.getProcessInfo``) and
writes :data:`BROWSER_FILE` in the run directory: the pid, its process group, the browser's
``--user-data-dir`` and the process start time. When a supervisor (the daemon) reaps a dead
worker, :func:`stop_browser` signals that browser's process group, but only after checking that
the process is still the one recorded: its command line still names the recorded user-data
directory and its start time is unchanged (so a recycled pid is never signalled). The worker
removes the file when it closes the browser normally.

POSIX only (``ps`` and process groups); elsewhere recording and stopping do nothing.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["BROWSER_FILE", "BrowserProcess", "forget_browser", "record_browser", "stop_browser"]

BROWSER_FILE = "browser.json"
_PS_TIMEOUT_S = 3.0
_USER_DATA_DIR = re.compile(r"--user-data-dir=(\S+)")
_SUPPORTED = os.name == "posix" and hasattr(os, "killpg")


@dataclass(frozen=True)
class BrowserProcess:
    """What identifies the run's browser process: its pid and group, user-data directory and start time."""

    pid: int
    pgid: int
    user_data_dir: str | None
    started: str | None


def _ps(pid: int, field: str) -> str | None:
    """One ``ps`` column for ``pid`` (``command`` or ``lstart``), or None when the process is gone or ``ps`` fails."""
    try:
        done = subprocess.run(
            ["ps", "-ww", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True, timeout=_PS_TIMEOUT_S, check=False
        )  # -ww: Linux ps cuts long commands to the terminal width
    except (OSError, subprocess.SubprocessError):
        return None
    text = done.stdout.strip()
    return text if done.returncode == 0 and text else None


def describe_process(pid: int) -> BrowserProcess | None:
    """The process as it is now, or None when it does not exist."""
    if not _SUPPORTED:
        return None
    command, started = _ps(pid, "command"), _ps(pid, "lstart")
    if command is None:
        return None
    found = _USER_DATA_DIR.search(command)
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return None
    return BrowserProcess(pid=pid, pgid=pgid, user_data_dir=found.group(1) if found else None, started=started)


async def browser_pid(page: Any) -> int | None:
    """The pid of the browser process behind ``page`` (asked over CDP), or None when it cannot be told."""
    context = getattr(page, "context", None)
    browser = getattr(context, "browser", None)
    new_session = getattr(browser, "new_browser_cdp_session", None)
    if not callable(new_session):
        return None
    try:
        session = await new_session()
        try:
            info = await session.send("SystemInfo.getProcessInfo")
        finally:
            with contextlib.suppress(Exception):
                await session.detach()
    except Exception as exc:  # a browser without the CDP domain: nothing to record
        logger.debug("could not ask the browser for its process id (%s)", type(exc).__name__)
        return None
    for process in info.get("processInfo", ()) if isinstance(info, dict) else ():
        if isinstance(process, dict) and process.get("type") == "browser" and isinstance(process.get("id"), int):
            return int(process["id"])
    return None


async def record_browser(page: Any, run_dir: Path) -> BrowserProcess | None:
    """Write :data:`BROWSER_FILE` for the browser behind ``page``; returns what was recorded (None when nothing was)."""
    if not _SUPPORTED:
        return None
    pid = await browser_pid(page)
    process = describe_process(pid) if pid is not None else None
    if process is None:
        return None
    path = run_dir / BROWSER_FILE
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(process.__dict__), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        logger.warning("could not record the browser process (%s)", type(exc).__name__)
        return None
    return process


def forget_browser(run_dir: Path) -> None:
    """Remove :data:`BROWSER_FILE` (the browser was closed normally)."""
    with contextlib.suppress(OSError):
        (run_dir / BROWSER_FILE).unlink()


def _recorded(run_dir: Path) -> BrowserProcess | None:
    path = run_dir / BROWSER_FILE
    if path.is_symlink():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return BrowserProcess(pid=int(raw["pid"]), pgid=int(raw["pgid"]), user_data_dir=raw.get("user_data_dir"), started=raw.get("started"))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _same_process(recorded: BrowserProcess, now: BrowserProcess) -> bool:
    """True when the live process is the recorded browser: same user-data directory (when recorded) and start time."""
    if recorded.user_data_dir is None and recorded.started is None:
        return False  # nothing to verify it by: never signal on a bare pid
    if recorded.user_data_dir is not None and recorded.user_data_dir != now.user_data_dir:
        return False
    return recorded.started is None or recorded.started == now.started


def stop_browser(run_dir: Path, sig: signal.Signals = signal.SIGKILL) -> bool:
    """Stop the browser a dead worker left behind (its whole process group); True when one was signalled.

    The recorded process is signalled only when it is still the same browser (see the module docstring);
    the record is removed either way.
    """
    recorded = _recorded(run_dir)
    if recorded is None or not _SUPPORTED:
        return False
    try:
        now = describe_process(recorded.pid)
        if now is None or not _same_process(recorded, now):
            return False
        try:
            if now.pgid == recorded.pid == recorded.pgid:
                os.killpg(recorded.pgid, sig)  # the browser leads its own group: its helpers go with it
            else:
                os.kill(recorded.pid, sig)
        except (ProcessLookupError, PermissionError):
            return False
        logger.info("stopped the browser (pid %d) a dead worker left running", recorded.pid)
        return True
    finally:
        forget_browser(run_dir)
