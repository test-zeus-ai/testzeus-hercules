"""``hercules worker``: run one queued case for the daemon and stream its events.

Started by ``hercules serve`` in its own process group. It reuses the ``hercules run``
path with the run id the daemon assigned and writes evidence and ``run.json`` as
usual. Events go to the original stdout as JSON lines
(``{"kind": ..., "data": {...}, "at": ...}``); everything else the run prints is
sent to stderr (the daemon keeps it in ``worker.log``). SIGTERM cancels the run;
the engine then finishes it as ``cancelled``.

With ``--control`` the worker reads JSON lines on stdin from the daemon:

* ``{"type": "control", "action": "pause|resume|step|skip|retry"}``;
* ``{"type": "approval", "id": ..., "decision": "approve|reject|edit", "step_text": ...}``;
* ``{"type": "screencast", "on": true|false}`` starts or stops the live view;
* ``{"type": "input", "kind": "click|type|key|scroll", ...}`` drives the page, only while
  the run is paused (answered with an ``input_result`` event).

``--frames-fd N`` names a pipe for screencast frames (see :mod:`testzeus_hercules.engine.live`).
Checkpoints are kept while the run is open, so ``hercules worker --resume <run_id>``
continues a run whose worker died from its last completed step.

Credentials in URLs (a session id or token in a query string, a password in userinfo) are
masked in everything the worker writes: the event stream, ``worker.log`` and the copy of the
case kept in the run directory (see :func:`~testzeus_hercules.act.redact.mask_url_credentials`).

Exit codes are those of ``hercules run``: 0 passed, 1 failed or cancelled, 2 error.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, TextIO

from testzeus_hercules.act.redact import REDACTED, mask_url_credentials
from testzeus_hercules.cases.gherkin import data_file
from testzeus_hercules.kernel.run_context import CancelToken

EXIT_ERROR = 2
_MAX_COMMAND_BYTES = 16 * 1024


class JsonLinesSink:
    """An ``EventSink`` writing one JSON object per line; safe to call from several threads."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def emit(self, kind: str, /, **data: Any) -> None:
        line = json.dumps({"kind": kind, "data": data, "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}, default=str, ensure_ascii=False)
        line = mask_url_credentials(line)  # events reach the daemon's store, its event stream and the UI
        with self._lock:
            try:
                self._stream.write(line + "\n")
                self._stream.flush()
            except (OSError, ValueError):  # the daemon went away; the run still writes its evidence
                pass


@dataclass(frozen=True)
class WorkerOptions:
    run_id: str
    case_path: Path | None = None
    project_dir: Path | None = None
    headed: bool = False
    mode: str = "auto"
    egress: str | None = None
    jev_key_env: str | None = None
    decider: str | None = None
    max_steps: int | None = None
    step_timeout_s: float | None = None
    resume: bool = False
    control: bool = False
    frames_fd: int | None = None
    confirm_destructive: bool = False
    destructive_terms: tuple[str, ...] | None = None
    ask_on_failure: bool = False
    approval_timeout_s: float | None = None
    quality: Any = None  # engine.quality.RunQualityOptions
    strict: bool | None = None
    plan_review: str | None = None
    plan_path: Path | None = None
    # Make the case's plan (written to plan_out as JSON) instead of running it: the daemon's plan jobs.
    plan_only: bool = False
    plan_out: Path | None = None
    explore_path: Path | None = None  # an exploration job: explore (or reproduce a clue) instead of running the case's steps

    def policy(self) -> Any:
        from testzeus_hercules.engine.control import DEFAULT_APPROVAL_TIMEOUT_S, DEFAULT_DESTRUCTIVE_TERMS, HumanPolicy

        return HumanPolicy(
            confirm_destructive=self.confirm_destructive,
            destructive_terms=self.destructive_terms or DEFAULT_DESTRUCTIVE_TERMS,
            ask_on_failure=self.ask_on_failure,
            approval_timeout_s=self.approval_timeout_s or DEFAULT_APPROVAL_TIMEOUT_S,
        )


class WorkerChannel:
    """Reads the daemon's JSON-lines messages on a stream and routes them to the run's control and live view."""

    def __init__(self, sink: JsonLinesSink, control: Any, live: Any = None) -> None:
        self.sink, self.control, self.live = sink, control, live

    def start(self, stream: TextIO) -> threading.Thread:
        thread = threading.Thread(target=self._read, args=(stream,), name="hercules-control", daemon=True)
        thread.start()
        return thread

    def _read(self, stream: TextIO) -> None:
        try:
            for line in stream:
                self.handle(line)
        except (OSError, ValueError):  # the daemon closed the pipe
            pass
        self.control.submit({"type": "detach"})  # nobody can resume or answer any more

    def handle(self, line: str) -> None:
        line = line.strip()
        if not line or len(line) > _MAX_COMMAND_BYTES:
            return
        try:
            message = json.loads(line)
        except ValueError:
            self.sink.emit("control_ignored", reason="not a JSON message")
            return
        kind = message.get("type") if isinstance(message, dict) else None
        if kind == "screencast":
            if self.live is not None:
                self.live.request(bool(message.get("on")))
        elif kind == "input":
            self._input(message)
        else:
            self.control.submit(message)

    def _input(self, message: dict[str, Any]) -> None:
        live, loop = self.live, getattr(self.live, "_loop", None)
        if live is None or loop is None:
            self.sink.emit("input_result", ok=False, detail="no live browser to drive")
            return
        if not self.control.at_pause_point:
            self.sink.emit("input_result", ok=False, detail="pause the run before driving the browser")
            return

        async def deliver() -> None:
            from testzeus_hercules.engine.live import InputRejected

            try:
                detail = await live.dispatch(message)
            except InputRejected as exc:
                self.sink.emit("input_result", ok=False, kind=message.get("kind"), detail=str(exc))
                return
            except Exception as exc:  # the page went away while it was being driven
                self.sink.emit("input_result", ok=False, kind=message.get("kind"), detail=f"input failed ({type(exc).__name__})")
                return
            self.sink.emit("input_result", ok=True, kind=message.get("kind"), detail=detail)

        try:
            asyncio.run_coroutine_threadsafe(deliver(), loop)
        except RuntimeError:  # the run is over
            self.sink.emit("input_result", ok=False, detail="the run has finished")


def _event_stream() -> TextIO:
    """Keep the real stdout for events and point fd 1 at stderr, so stray prints never corrupt the stream."""
    events_fd = os.dup(1)  # not inherited by child processes (PEP 446)
    os.dup2(2, 1)
    sys.stdout = io.TextIOWrapper(os.fdopen(1, "wb", closefd=False), encoding="utf-8", line_buffering=True)
    return io.TextIOWrapper(os.fdopen(events_fd, "wb"), encoding="utf-8", line_buffering=True)


_SAVED_CASES = ("case.json", "case.feature")


def _run_dir(options: WorkerOptions, env: Mapping[str, str] | None) -> Path:
    from testzeus_hercules.kernel.paths import HerculesPaths
    from testzeus_hercules.kernel.settings import Settings, SettingsError

    try:
        settings = Settings.load(options.project_dir, env=dict(os.environ) if env is None else env)
    except SettingsError as exc:
        raise ValueError(str(exc)) from None
    return HerculesPaths.from_settings(settings).run_dir(options.run_id)


def _saved_case(options: WorkerOptions, env: Mapping[str, str] | None) -> Path:
    """The case saved for this run (``--resume`` without ``--case``)."""
    run_dir = _run_dir(options, env)
    path = next((run_dir / name for name in _SAVED_CASES if (run_dir / name).is_file()), None)
    if path is None:
        raise ValueError(f"no saved case for run {options.run_id}; give --case")
    try:
        masked = REDACTED in path.read_text(encoding="utf-8")
    except OSError:
        masked = False
    if masked:
        raise ValueError(f"the saved copy of run {options.run_id}'s case has credentials masked in its URLs; give --case with the original case")
    return path


def _keep_case(options: WorkerOptions, env: Mapping[str, str] | None) -> None:
    """Keep a copy of the case in the run directory (the daemon already does), so the run can be resumed."""
    source = options.case_path
    if source is None or source.suffix.lower() not in (".json", ".feature"):
        return
    try:
        run_dir = _run_dir(options, env)
        target = run_dir / f"case{source.suffix.lower()}"
        if target.exists() or any((run_dir / name).exists() for name in _SAVED_CASES):
            return
        run_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(mask_url_credentials(source.read_text(encoding="utf-8")), encoding="utf-8")
        sidecar = data_file(source)
        if target.suffix == ".feature" and sidecar.is_file() and not sidecar.is_symlink():
            data_file(target).write_text(sidecar.read_text(encoding="utf-8"), encoding="utf-8")
    except (OSError, ValueError):  # best effort: only resuming needs it
        return


def run_worker(options: WorkerOptions, *, env: Mapping[str, str] | None = None, events: TextIO | None = None, err: TextIO | None = None, commands: TextIO | None = None) -> int:
    from testzeus_hercules.cli.run import RunCommandDeps, RunOptions, run_command
    from testzeus_hercules.kernel.paths import validate_run_id

    errors = err if err is not None else sys.stderr
    if options.plan_only:
        return _plan_only(options, env=env, err=errors)
    sink = JsonLinesSink(events if events is not None else _event_stream())
    log_handler = _masked_logging(errors)
    try:
        validate_run_id(options.run_id)
        case_path = options.case_path or _saved_case(options, env)
        if not options.resume:
            _keep_case(options, env)
    except ValueError as exc:
        sink.emit("error", message=str(exc))
        return EXIT_ERROR
    token = CancelToken()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda signum, frame: token.cancel("cancelled by request"))
    control = live = writer = None
    if options.control or options.frames_fd is not None:
        from testzeus_hercules.engine.control import RunControl
        from testzeus_hercules.engine.live import FrameWriter, LiveView

        control = RunControl(events=sink, policy=options.policy())
        if options.frames_fd is not None:
            writer = FrameWriter(options.frames_fd)
            live = LiveView(writer.offer)
        if options.control:
            WorkerChannel(sink, control, live).start(commands if commands is not None else sys.stdin)
    run_options = RunOptions(
        case_path=case_path,
        project_dir=options.project_dir,
        headed=options.headed,
        mode=options.mode,  # type: ignore[arg-type]
        egress=options.egress,  # type: ignore[arg-type]
        jev_key_env=options.jev_key_env,
        decider=options.decider,  # type: ignore[arg-type]
        json_output=True,
        max_steps=options.max_steps,
        step_timeout_s=options.step_timeout_s,
        run_id=options.run_id,
        resume=options.resume,
        quality=options.quality,
        strict=options.strict,
        plan_review=options.plan_review,
        plan_path=options.plan_path,
        explore_path=options.explore_path,
    )
    captured = io.StringIO()
    deps = RunCommandDeps(events=sink, cancel=token, control=control, on_page=live.attach if live is not None else None, checkpoints=True)
    try:
        code = run_command(run_options, env=env, deps=deps, out=captured, err=errors)
    except Exception as exc:  # report, never crash silently: the daemon shows this as the run's reason
        sink.emit("error", message=f"the worker failed ({type(exc).__name__}): {_first_line(exc)}")
        errors.write(mask_url_credentials(f"worker error: {type(exc).__name__}: {exc}") + "\n")
        return EXIT_ERROR
    finally:
        if writer is not None:
            writer.close()
        if log_handler is not None:
            logging.getLogger().removeHandler(log_handler)
    output = mask_url_credentials(captured.getvalue())
    errors.write(output)
    problems = [line[len("error: ") :] for line in output.splitlines() if line.startswith("error: ")]
    if problems:
        sink.emit("error", message=problems[-1][:500])
    return code


def _plan_only(options: WorkerOptions, *, env: Mapping[str, str] | None, err: TextIO) -> int:
    """Make the case's plan and write it (JSON) to ``plan_out``: what the daemon's ``POST /api/plans`` runs."""
    from testzeus_hercules.cli.plan_cmd import PlanCommandOptions, plan_command

    if options.case_path is None or options.plan_out is None:
        err.write("hercules worker --plan-only needs --case and --plan-out\n")
        return EXIT_ERROR
    captured = io.StringIO()
    command = PlanCommandOptions(
        case_path=options.case_path,
        project_dir=options.project_dir,
        mode=options.mode,
        egress=options.egress,
        jev_key_env=options.jev_key_env,
        decider=options.decider,
        out_path=options.plan_out,
        json_output=True,
        strict=options.strict,
        confirm_destructive=options.confirm_destructive,
    )
    handler = _masked_logging(err)
    try:
        code = plan_command(command, env=env, out=captured, err=err)
    except Exception as exc:  # report, never crash silently: the daemon shows this as the job's error
        err.write(mask_url_credentials(f"plan error: {type(exc).__name__}: {exc}") + "\n")
        return EXIT_ERROR
    finally:
        if handler is not None:
            logging.getLogger().removeHandler(handler)
    output = captured.getvalue()
    problems = [line for line in output.splitlines() if line.startswith("error: ")]
    if problems:
        err.write(mask_url_credentials(problems[-1]) + "\n")
    return code if code != 1 else 0


class _MaskingFormatter(logging.Formatter):
    """Log lines (and their tracebacks) with URL credentials masked: they end up in ``worker.log``."""

    def format(self, record: logging.LogRecord) -> str:
        return mask_url_credentials(super().format(record))


def _masked_logging(stream: TextIO) -> logging.Handler | None:
    """Send warnings to ``stream`` (as Python's last-resort handler would) with URL credentials masked."""
    root = logging.getLogger()
    if root.handlers:  # someone configured logging already; their handlers stay as they are
        return None
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(_MaskingFormatter("%(message)s"))
    root.addHandler(handler)
    return handler


def _first_line(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return lines[0][:300] if lines else ""
