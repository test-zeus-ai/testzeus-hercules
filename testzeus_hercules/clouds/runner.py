"""Run a passed run's exported test on a cloud and keep track of it (the daemon's "Run on cloud").

A cloud run writes its project (see :mod:`testzeus_hercules.clouds.bundle`) to
``<project>/.hercules/cloud-runs/<id>/project`` and starts it:

* web on BrowserStack or TestMu AI: ``python -m pytest`` with the connect conftest (needs ``pytest-playwright``,
  in the ``cloud`` extra);
* web on Sauce Labs: ``saucectl run`` (needs ``saucectl`` on the PATH);
* native app: the app file is uploaded to the cloud first when the run used one, then ``python -m pytest`` runs the
  Appium test (needs ``Appium-Python-Client``, in the ``cloud`` extra).

Its state is ``record.json`` next to the project and its output ``output.log``; the cloud's credentials and their
URL-encoded forms are masked in both. Credentials reach the test only through its environment.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import importlib.util
import json
import os
import re
import shutil
import signal
import sys
import urllib.parse
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from testzeus_hercules.cases.models import RunResult
from testzeus_hercules.clouds.bundle import (
    BROWSER_ENV,
    BUILD_ENV,
    OS_ENV,
    SESSION_MARKER,
    WS_ENDPOINT_ENV,
    Bundle,
    MobileOptions,
    WebOptions,
    mobile_bundle,
    web_bundle,
)
from testzeus_hercules.clouds.credentials import CloudCredentials, find_credentials, sauce_region
from testzeus_hercules.clouds.registry import CloudId, cloud_info
from testzeus_hercules.export.common import ExportError
from testzeus_hercules.kernel.child_env import child_environment
from testzeus_hercules.kernel.settings import Settings

CloudRunStatus = Literal["starting", "running", "passed", "failed", "error", "cancelled"]
FINAL: frozenset[str] = frozenset({"passed", "failed", "error", "cancelled"})
RUN_TIMEOUT_S = 30 * 60
LOG_TAIL_CHARS = 20_000
MAX_LOG_BYTES = 2_000_000
CLOUD_EXTRA_HINT = "pip install 'testzeus-hercules[cloud]'"
_ID = re.compile(r"^[a-f0-9]{12}$")
_SAUCE_JOB = re.compile(r"https://app\.(?:[a-z0-9-]+\.)?saucelabs\.com/tests/[0-9a-f]{16,}")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Lines of output kept in memory for the summary and the session link (the log file keeps up to MAX_LOG_BYTES).
TAIL_LINES = 2000
# A longer line is split rather than failing the read.
LINE_LIMIT = 1 << 20
STOP_GRACE_S = 5.0
# Variables a case must never pass to a test that runs on a third-party cloud, even when it declares them as secrets.
_NEVER_FORWARD = re.compile(
    r"^(HERCULES_.*|.*(OPENAI|ANTHROPIC|OPENROUTER|CEREBRAS|GEMINI|GOOGLE|VERTEX|AZURE|AWS|MISTRAL|GROQ|TOGETHER|FIREWORKS|DEEPSEEK|XAI|COHERE|HF|HUGGINGFACE|PORTKEY|LITELLM|LANGSMITH)_.*(KEY|TOKEN|SECRET).*)$"
)


class CloudRunError(ValueError):
    """A cloud run cannot start; the message is safe to show."""


@dataclass
class CloudRunRecord:
    id: str
    run_id: str
    case_id: str
    cloud: CloudId
    kind: str
    mode: str
    options: dict[str, Any]
    status: CloudRunStatus
    created_at: str
    dashboard_url: str
    started_at: str | None = None
    finished_at: str | None = None
    session_url: str | None = None
    message: str | None = None
    exit_code: int | None = None
    # The process running it, so a daemon starting while a command-line cloud run is in progress leaves it alone.
    owner_pid: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _masks(credentials: CloudCredentials) -> list[str]:
    values = [value for value in (credentials.access_key, credentials.username) if value]
    forms: list[str] = []
    for value in values:
        escaped = json.dumps(value)[1:-1]
        forms += [value, escaped, urllib.parse.quote(value), urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value), urllib.parse.quote(escaped), urllib.parse.quote(escaped, safe="")]
    return sorted({form for form in forms if len(form) >= 4}, key=len, reverse=True)


def redact(text: str, masks: list[str]) -> str:
    for form in masks:
        text = text.replace(form, "******")
    return text


def _alive(pid: int | None) -> bool:
    if not pid or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def forbidden_variables(settings: Settings) -> set[str]:
    """Variables that hold Hercules's own keys: never handed to a test that runs on a cloud."""
    names = {settings.decider.api_key_env or "OPENROUTER_API_KEY", "OPENROUTER_API_KEY"}
    if settings.llm.api_key_env:
        names.add(settings.llm.api_key_env)
    names.update(settings.llm.headers.values())
    return names


def _terminate(process: asyncio.subprocess.Process, *, kill: bool = False) -> None:
    """Stop the test and everything it started (its own process group on POSIX)."""
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL if kill else signal.SIGTERM)
        elif kill:
            process.kill()
        else:
            process.terminate()


def requirements(kind: str, mode: str) -> str | None:
    """What is missing on this machine to run a cloud run of this kind (None: nothing)."""
    if mode == "saucectl":
        return None if shutil.which("saucectl") else "Sauce Labs runs Playwright tests with saucectl, which is not installed: npm i -g saucectl (see https://docs.saucelabs.com/dev/cli/saucectl/)"
    module = "pytest_playwright" if kind == "web" else "appium"
    if importlib.util.find_spec("pytest") is None or importlib.util.find_spec(module) is None:
        return f"running the exported test needs pytest and {'pytest-playwright' if kind == 'web' else 'the Appium Python client'}: {CLOUD_EXTRA_HINT}"
    return None


class CloudRuns:
    """The project's cloud runs: start, follow, cancel, list."""

    def __init__(self, root: Path, settings: Settings, env: Mapping[str, str], project_dir: Path) -> None:
        self.root = root
        self.settings = settings
        self.env = dict(env)
        self.project_dir = project_dir
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    # -- records ---------------------------------------------------------------------------------------------------

    def _dir(self, cloud_run_id: str) -> Path:
        if not _ID.match(cloud_run_id):
            raise CloudRunError("unknown cloud run")
        return self.root / cloud_run_id

    def _write(self, record: CloudRunRecord) -> None:
        folder = self._dir(record.id)
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / "record.json.tmp"
        temporary.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
        os.replace(temporary, folder / "record.json")

    def get(self, cloud_run_id: str) -> CloudRunRecord | None:
        try:
            path = self._dir(cloud_run_id) / "record.json"
            return CloudRunRecord(**json.loads(path.read_text(encoding="utf-8")))
        except (CloudRunError, OSError, ValueError, TypeError):
            return None

    def records(self, run_id: str | None = None, limit: int = 100) -> list[CloudRunRecord]:
        if not self.root.is_dir():
            return []
        records = [record for path in self.root.iterdir() if (record := self.get(path.name)) is not None and (run_id is None or record.run_id == run_id)]
        return sorted(records, key=lambda item: item.created_at, reverse=True)[:limit]

    def log_tail(self, cloud_run_id: str, chars: int = LOG_TAIL_CHARS) -> str:
        try:
            text = (self._dir(cloud_run_id) / "output.log").read_text(encoding="utf-8", errors="replace")
        except (CloudRunError, OSError):
            return ""
        return text[-chars:]

    def is_active(self, cloud_run_id: str) -> bool:
        return cloud_run_id in self._tasks

    def recover(self) -> None:
        """Mark runs left unfinished by a process that is gone as errors."""
        for record in self.records(limit=10_000):
            if record.status not in FINAL and not _alive(record.owner_pid):
                record.status, record.message, record.finished_at = "error", "the daemon stopped while this cloud run was in progress", _now()
                self._write(record)

    # -- starting --------------------------------------------------------------------------------------------------

    def bundle(self, result: RunResult, cloud: CloudId, web: WebOptions, mobile: MobileOptions, *, allow_failed: bool = False) -> Bundle:
        credentials = find_credentials(cloud, self.settings, self.env)
        names = (credentials.username_env, credentials.access_key_env)
        is_app = result.automation is not None and result.automation.app is not None
        try:
            if is_app:
                return mobile_bundle(result, cloud, mobile, allow_failed=allow_failed)
            return web_bundle(result, cloud, web, credentials=names, allow_failed=allow_failed, sauce_region=sauce_region(credentials), project_dir=self.project_dir)
        except (ExportError, ValueError) as exc:
            raise CloudRunError(str(exc)) from None

    def start(self, result: RunResult, cloud: CloudId, web: WebOptions, mobile: MobileOptions) -> CloudRunRecord:
        """Check everything the run needs, write its project and start it in the background (in a running event loop)."""
        prepared = self.prepare(result, cloud, web, mobile)
        self.launch(*prepared, web)
        return prepared[0]

    def launch(self, record: CloudRunRecord, bundle: Bundle, credentials: CloudCredentials, web: WebOptions) -> None:
        """Start a prepared cloud run in the background (in a running event loop)."""
        project = self._dir(record.id) / "project"
        self._tasks[record.id] = asyncio.get_running_loop().create_task(self._run(record, bundle, credentials, web, project))

    def prepare(self, result: RunResult, cloud: CloudId, web: WebOptions, mobile: MobileOptions) -> tuple[CloudRunRecord, Bundle, CloudCredentials]:
        """Check everything the run needs and write its project and record (no event loop needed: may run in a thread)."""
        if self.settings.egress.policy == "local":
            raise CloudRunError("egress policy 'local' forbids sending tests to a cloud; allow it with egress.policy: hybrid-text (or hybrid)")
        info = cloud_info(cloud)
        credentials = find_credentials(cloud, self.settings, self.env)
        if not credentials.found:
            raise CloudRunError(f"{info.name} is not connected: connect it on the Integrations screen, or set {credentials.username_env} and {credentials.access_key_env}")
        bundle = self.bundle(result, cloud, web, mobile)
        missing = requirements(bundle.kind, bundle.mode)
        if missing:
            raise CloudRunError(missing)
        forbidden = forbidden_variables(self.settings)
        refused = sorted({item.name for item in bundle.env if item.name in forbidden or _NEVER_FORWARD.match(item.name)})
        if refused:
            raise CloudRunError(
                f"the case reads {', '.join(refused)}, which look{'s' if len(refused) == 1 else ''} like a model or Hercules key; "
                "it is never sent to a test on a cloud. Give the case's secret another variable name"
            )
        unset = [item.name for item in bundle.env if item.required and not self.env.get(item.name) and item.name not in (credentials.username_env, credentials.access_key_env)]
        unset = [name for name in unset if not self._set_by_runner(bundle, name)]
        if unset:
            raise CloudRunError(f"the exported test needs these environment variables, which the daemon does not have: {', '.join(unset)}; set them and restart the daemon")
        chosen = bundle.web or web
        options = {"browser": chosen.browser, "os": chosen.os} if bundle.kind == "web" else {"device": mobile.device, "platform_version": mobile.platform_version}
        record = CloudRunRecord(
            id=uuid.uuid4().hex[:12],
            run_id=result.run_id,
            case_id=result.case_id,
            cloud=cloud,
            kind=bundle.kind,
            mode=bundle.mode,
            options=options,
            status="starting",
            created_at=_now(),
            dashboard_url=info.dashboard_url,
            owner_pid=os.getpid(),
        )
        project = self._dir(record.id) / "project"
        for relative, text in bundle.files.items():
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self._write(record)
        return record, bundle, credentials

    def _set_by_runner(self, bundle: Bundle, name: str) -> bool:
        if bundle.kind != "mobile":
            return False
        from testzeus_hercules.clouds import mobile

        return name in mobile.RUNNER_SET_ENV

    async def cancel(self, cloud_run_id: str) -> CloudRunRecord | None:
        record = self.get(cloud_run_id)
        if record is None or record.status in FINAL:
            return record
        process = self._processes.get(cloud_run_id)
        if process is not None:
            _terminate(process)
        task = self._tasks.get(cloud_run_id)
        if task is not None and process is None:
            task.cancel()
        record.status, record.message, record.finished_at = "cancelled", "cancelled from Hercules", _now()
        self._write(record)
        return record

    async def close(self) -> None:
        """Stop every cloud run of this process: ask them to stop, then kill what is left after a grace period."""
        for cloud_run_id in list(self._tasks):
            await self.cancel(cloud_run_id)
        tasks = list(self._tasks.values())
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=STOP_GRACE_S)
            for process in list(self._processes.values()):
                _terminate(process, kill=True)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    # -- running ---------------------------------------------------------------------------------------------------

    async def _run(self, record: CloudRunRecord, bundle: Bundle, credentials: CloudCredentials, web: WebOptions, project: Path) -> None:
        masks = _masks(credentials)
        log_path = self._dir(record.id) / "output.log"
        try:
            # Only what Python, Playwright and saucectl need, and the variables the export declares: never the daemon's whole environment.
            keep = ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONIOENCODING", "PYTHONUTF8", WS_ENDPOINT_ENV, BUILD_ENV, *(item.name for item in bundle.env))
            env = child_environment(self.env, keep=keep, prefixes=("PLAYWRIGHT_",))
            env.update({credentials.username_env: credentials.username or "", credentials.access_key_env: credentials.access_key or "", "PYTHONDONTWRITEBYTECODE": "1"})
            if bundle.kind == "web" and bundle.mode == "connect":
                chosen = bundle.web or web
                env.update({BROWSER_ENV: chosen.browser or "chrome", OS_ENV: chosen.os})
            if bundle.kind == "mobile":
                env.update(await self._prepare_app(record, credentials))
            command = ["saucectl", "run", "--config", ".sauce/config.yml"] if bundle.mode == "saucectl" else [sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider", "tests"]
            record.status, record.started_at = "running", _now()
            self._write(record)
            code, output = await self._execute(record.id, command, project, env, log_path, masks)
            record.exit_code = code
            record.session_url = record.session_url or _session_url(output)
            current = self.get(record.id)
            if current is not None and current.status == "cancelled":
                return
            record.status = "passed" if code == 0 else "failed"
            record.message = _summary(output, code)
        except asyncio.CancelledError:
            record.status, record.message = "cancelled", "cancelled from Hercules"
            raise
        except CloudRunError as exc:
            record.status, record.message = "error", redact(str(exc), masks)
        except Exception as exc:  # report, never crash the daemon
            record.status, record.message = "error", redact(f"{type(exc).__name__}: {exc}", masks)
        finally:
            record.finished_at = _now()
            self._processes.pop(record.id, None)
            self._tasks.pop(record.id, None)
            current = self.get(record.id)
            if current is None or current.status != "cancelled":
                self._write(record)

    async def _prepare_app(self, record: CloudRunRecord, credentials: CloudCredentials) -> dict[str, str]:
        from testzeus_hercules.clouds import mobile
        from testzeus_hercules.export import read_run

        result = read_run(self.settings_runs_dir / record.run_id)
        app_file = mobile.app_file_of(result, self.project_dir)
        if app_file is None:
            return {}
        try:
            app_ref = await mobile.upload_app_to_cloud(app_file, record.cloud, credentials, self.settings)
        except Exception as exc:
            raise CloudRunError(f"the app could not be uploaded to {cloud_info(record.cloud).name}: {exc}") from None
        return {mobile.APP_ENV: app_ref}

    @property
    def settings_runs_dir(self) -> Path:
        from testzeus_hercules.kernel.paths import HerculesPaths

        return HerculesPaths.from_settings(self.settings).runs_dir

    async def _execute(self, cloud_run_id: str, command: list[str], cwd: Path, env: Mapping[str, str], log_path: Path, masks: list[str]) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=dict(env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            limit=LINE_LIMIT,
            start_new_session=os.name == "posix",
        )
        self._processes[cloud_run_id] = process
        tail: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
        written = 0
        stdout = process.stdout
        if stdout is None:
            raise CloudRunError("the test's output could not be read")
        try:
            with log_path.open("w", encoding="utf-8") as log:

                async def pump() -> None:
                    nonlocal written
                    while raw := await stdout.readline():
                        line = redact(_ANSI.sub("", raw.decode("utf-8", errors="replace")), masks)
                        tail.append(line)
                        if written < MAX_LOG_BYTES:
                            log.write(line)
                            log.flush()
                            written += len(line.encode("utf-8"))
                            if written >= MAX_LOG_BYTES:
                                log.write("\n[output truncated: the log keeps the first 2 MB]\n")
                        if SESSION_MARKER in line or _SAUCE_JOB.search(line):
                            record = self.get(cloud_run_id)
                            if record is not None and record.session_url is None:
                                record.session_url = _session_url(line)
                                self._write(record)

                try:
                    await asyncio.wait_for(asyncio.gather(pump(), process.wait()), timeout=RUN_TIMEOUT_S)
                except asyncio.TimeoutError:
                    tail.append(f"\nstopped after {RUN_TIMEOUT_S // 60} minutes\n")
        finally:
            # Whatever happened (time limit, cancellation, an unreadable line), the test does not outlive its run.
            if process.returncode is None:
                _terminate(process, kill=True)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), timeout=STOP_GRACE_S)
        return process.returncode if process.returncode is not None else -1, "".join(tail)


def _session_url(output: str) -> str | None:
    for line in output.splitlines():
        if SESSION_MARKER in line:
            return line.split(SESSION_MARKER, 1)[1].strip() or None
    found = _SAUCE_JOB.search(output)
    return found.group(0) if found else None


def _summary(output: str, code: int) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if code == 0:
        return next((line for line in reversed(lines) if "passed" in line), "passed")
    failures = [line for line in lines if line.startswith(("E ", "FAILED", "ERROR")) or "Error:" in line]
    return (failures[-1] if failures else (lines[-1] if lines else f"exit code {code}"))[:500]
