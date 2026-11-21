"""Run an exported pytest test once, to see that it passes on its own (``hercules export --check``).

A pytest-playwright export (``playwright-py``) runs in a browser; an Appium export (``appium-py``) runs on the
device the run used, through its Appium server (see :func:`run_appium_check`).

The export is written to a folder of its own and run with pytest in a child process, with the
variables it needs (secrets by name from the environment, credentials the run took out of the
case's URLs from the case itself) and a Playwright trace. Video is not recorded: it needs
Playwright's ffmpeg helper, without which the browser page never opens and pytest hangs while
closing it. The trace's frames become a recording (see :mod:`testzeus_hercules.export.recording`).
A run that takes longer than its time limit is stopped, the browser with it.

The child does not inherit the caller's whole environment (which may hold unrelated keys and tokens):
it gets only what Python, pytest and Playwright need to run (PATH, HOME, locale, temporary folders,
proxies and CA bundles, the display, ``PLAYWRIGHT_*``, the browser in ``HERCULES_CHROMIUM``) and the
variables the export declares.

The trace holds what the browser sent, secrets included: it stays in the run's folder. The
output shown, and every file name, are free of secret values.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from testzeus_hercules.act.redact import mask_url_credentials
from testzeus_hercules.export.common import BROWSER_ENV, REDACTED_MASK, ExportResult
from testzeus_hercules.kernel.child_env import child_environment
from testzeus_hercules.export.recording import write_recording

DEFAULT_TIMEOUT_S = 600.0
_STOP_GRACE_S = 5.0
_OUTPUT_LINES = 40
RESULTS_DIR = "results"
RECORDING_FILE = "recording.gif"


# What the child process may inherit besides the export's own variables: enough for Python, pytest and Playwright to run.
_KEEP = ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONIOENCODING", "PYTHONUTF8", BROWSER_ENV)


class CheckError(Exception):
    """The export cannot be checked (wrong format, pytest-playwright or the Appium client missing, no Appium server, a variable it needs is not set)."""


@dataclass(frozen=True)
class CheckResult:
    """How the exported test's one run went."""

    passed: bool
    exit_code: int | None
    duration_s: float
    folder: Path
    test_file: Path
    output: str
    timed_out: bool = False
    trace: Path | None = None
    recording: Path | None = None
    # What probably went wrong, when the output says so (the device was not available, ...).
    hint: str | None = None


def check_environment(exported: ExportResult, env: Mapping[str, str], extra: Mapping[str, str], keep: Iterable[str] = ()) -> dict[str, str]:
    """The child's environment: from ``env`` only what Python and Playwright need, the variables the export declares
    and the names in ``keep``; with the export's variables from ``extra`` (credentials taken from the case's URLs).
    Refused when a required one has no value (named, never shown)."""
    child = child_environment(env, keep=(*_KEEP, *(item.name for item in exported.env), *keep), prefixes=("PLAYWRIGHT_",))
    child.update(extra)
    missing = [item.name for item in exported.env if item.required and not child.get(item.name)]
    if missing:
        raise CheckError(f"the exported test needs {', '.join(missing)}; set {'it' if len(missing) == 1 else 'them'} in the environment")
    return child


def has_module(python: str, module: str, env: Mapping[str, str] | None = None) -> bool:
    try:
        return subprocess.run([python, "-c", f"import {module}"], capture_output=True, timeout=60, env=None if env is None else dict(env)).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def has_pytest_playwright(python: str, env: Mapping[str, str] | None = None) -> bool:
    return has_module(python, "pytest_playwright", env)


def redacted(text: str, secrets: Sequence[str]) -> str:
    """``text`` without the secret values (and without credentials in any URL it shows)."""
    for value in sorted({value for value in secrets if value}, key=len, reverse=True):
        text = text.replace(value, REDACTED_MASK)
    return mask_url_credentials(text)


def run_check(
    exported: ExportResult,
    folder: Path,
    *,
    env: Mapping[str, str],
    extra: Mapping[str, str] | None = None,
    python: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    keep: Iterable[str] = (),
) -> CheckResult:
    """Write ``exported`` (pytest-playwright) to ``folder`` and run it once with a trace; see the module docstring."""
    if exported.format != "playwright-py":
        raise CheckError("only the pytest-playwright export (playwright-py) can be checked")
    python = python or sys.executable
    extra = dict(extra or {})
    child = check_environment(exported, env, extra, keep)
    if not has_pytest_playwright(python, child):
        raise CheckError(f"pytest-playwright is not installed for {python}; install it (pip install pytest-playwright) to check exports")
    folder.mkdir(parents=True, exist_ok=True)
    test_file = folder / Path(exported.filename).name
    test_file.write_text(exported.code, encoding="utf-8")
    from testzeus_hercules.cases.browser_options import engine_of

    browser = engine_of(str(exported.spec.get("browser_name") or "chromium"))  # chrome / msedge: the channel is in the test's launch options
    command = [python, "-m", "pytest", *_own_config(folder), "-q", "-p", "no:cacheprovider", test_file.name, "--browser", browser, "--tracing", "on", "--output", RESULTS_DIR]
    secrets = [*extra.values(), *(child.get(item.name, "") for item in exported.env if item.required)]
    passed, code, duration, tail, timed_out = _run_pytest(command, folder, child, secrets, timeout_s)
    trace = next(iter(sorted((folder / RESULTS_DIR).rglob("trace.zip"))), None)
    recording = None
    if trace is not None:
        try:
            recording = write_recording(trace, folder / RECORDING_FILE)
        except Exception:  # a recording is a convenience; the check's verdict stands without it
            recording = None
    return CheckResult(passed=passed, exit_code=code, duration_s=duration, folder=folder, test_file=test_file, output=tail, timed_out=timed_out, trace=trace, recording=recording)


# What an Appium export's failure says when the device (not the test) was the problem.
_DEVICE_TROUBLE = ("SessionNotCreatedException", "Could not find a connected Android device", "Could not find a device", "Unable to find an active device", "No device connected")


def run_appium_check(
    exported: ExportResult,
    folder: Path,
    *,
    env: Mapping[str, str],
    appium_url: str,
    local: bool,
    extra: Mapping[str, str] | None = None,
    python: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    keep: Iterable[str] = (),
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> CheckResult:
    """Write ``exported`` (appium-py) to ``folder`` and run it once on the device, through the Appium server at
    ``appium_url`` (``APPIUM_URL`` when set). A ``local`` server must answer before the test starts."""
    if exported.format != "appium-py":
        raise CheckError("run_appium_check runs the Appium export (appium-py)")
    python = python or sys.executable
    extra = dict(extra or {})
    child = check_environment(exported, env, extra, keep)
    if not has_module(python, "appium", child) or not has_module(python, "pytest", child):
        raise CheckError(f"the Appium Python client or pytest is not installed for {python}; install them (pip install pytest Appium-Python-Client) to check Appium exports")
    url = (child.get("APPIUM_URL") or appium_url).rstrip("/")
    if local:
        problem = appium_status(url, client_factory)
        if problem is not None:
            raise CheckError(f"no Appium server answers at {mask_url_credentials(url)} ({problem}). Start it (`appium --address 127.0.0.1 --port 4723`) with the device running, or set APPIUM_URL")
    folder.mkdir(parents=True, exist_ok=True)
    test_file = folder / Path(exported.filename).name
    test_file.write_text(exported.code, encoding="utf-8")
    command = [python, "-m", "pytest", *_own_config(folder), "-q", "-p", "no:cacheprovider", test_file.name]
    secrets = [*extra.values(), *(child.get(item.name, "") for item in exported.env if item.required)]
    passed, code, duration, tail, timed_out = _run_pytest(command, folder, child, secrets, timeout_s)
    hint = None
    if not passed and any(marker in tail for marker in _DEVICE_TROUBLE):
        hint = "the Appium server could not start a session on the device: check that the device (or emulator) is running and connected, and that its Appium driver is installed (`appium driver list --installed`)"
    return CheckResult(passed=passed, exit_code=code, duration_s=duration, folder=folder, test_file=test_file, output=tail, timed_out=timed_out, hint=hint)


def appium_status(url: str, client_factory: Callable[..., httpx.Client] = httpx.Client) -> str | None:
    """None when the Appium server at ``url`` says it is ready; else why not (without credentials)."""
    try:
        with client_factory(timeout=10.0, trust_env=False) as client:
            response = client.get(f"{url}/status")
    except httpx.HTTPError as exc:
        return f"{type(exc).__name__}"
    if response.status_code >= 400:
        return f"HTTP {response.status_code}"
    try:
        ready = (response.json().get("value") or {}).get("ready", True)
    except (ValueError, AttributeError):
        return "not a WebDriver status answer"
    return None if ready is not False else "it is not ready"


def _run_pytest(command: list[str], folder: Path, child: dict[str, str], secrets: Sequence[str], timeout_s: float) -> tuple[bool, int | None, float, str, bool]:
    """Run pytest in ``folder``: (passed, exit code, seconds, the output's last lines without secrets, timed out)."""
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=folder, env=child, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    timed_out = False
    try:
        output, _ = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        output = _stop(process)
    duration = time.monotonic() - started
    tail = "\n".join(redacted(output or "", secrets).strip().splitlines()[-_OUTPUT_LINES:])
    code = None if timed_out else process.returncode
    return code == 0, code, duration, tail, timed_out


def browser_note(env: Mapping[str, str], browser: str | None = None) -> str:
    """Which browser the check runs (for the summary): the run's own (``browser``), else Chromium."""
    names = {"chrome": "the installed Google Chrome", "msedge": "the installed Microsoft Edge", "firefox": "Playwright's Firefox", "webkit": "Playwright's WebKit"}
    if browser in names:
        return names[browser]
    executable = env.get(BROWSER_ENV)
    return f"the browser in {BROWSER_ENV}" if executable else "Playwright's own Chromium"


def _stop(process: subprocess.Popen[str]) -> str:
    """Stop the run and everything it started (the browser included); what it printed."""
    for sig in (signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)):
        try:
            if hasattr(os, "killpg"):
                os.killpg(process.pid, sig)
            else:  # no process groups: the child only
                process.kill()
        except (ProcessLookupError, PermissionError):
            break
        try:
            output, _ = process.communicate(timeout=_STOP_GRACE_S)
            return output or ""
        except subprocess.TimeoutExpired:
            continue
    return ""


def _own_config(folder: Path) -> list[str]:
    """Run pytest with a config of its own, so the project's (or any parent folder's) pytest settings, which may turn
    plugins off or add options, do not change the check."""
    (folder / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    return ["-c", "pytest.ini", "--rootdir", "."]
