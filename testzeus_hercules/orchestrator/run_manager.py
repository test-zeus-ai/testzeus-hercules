"""Parallel scenario execution via isolated subprocesses.

In-process parallelism is unsafe (global config singleton plus PlaywrightManager
singletons), so every scenario runs in its own subprocess with a private
workspace: ``<workroot>/scenario_<n>/opt/input/test.feature`` plus copies of the
sibling ``test_data/`` directory. ``PROJECT_SOURCE_ROOT`` is resolved relative
to the subprocess cwd (see ``config._finalize_defaults``), so pointing the
child's cwd at the workspace fully isolates its outputs (``opt/output``,
``opt/proofs``, ``opt/log_files``) from sibling runs.

The subprocess command is injectable through :func:`_subprocess_command` so the
orchestration logic (parallelism, retries, junit merging) can be tested with
trivial real subprocesses instead of full Hercules runs (which need LLMs and
browsers).

Disk hygiene: workspaces of *successful* scenario runs are removed by the
orchestration entry points once their junit XML has been staged into the merged
report (see :func:`cleanup_successful_workspaces`); failed runs always keep
their workspace (opt/output, videos, logs) for debugging. Set
``HERCULES_KEEP_WORKSPACES=1`` to keep everything. Whole ``.hercules/parallel_*``
workroots older than ``HERCULES_RUN_RETENTION_HOURS`` (default 72, never younger
than 1h) are swept at the start of every run and every monitor cycle.

Verdict: children run with ``STRICT_EXIT_CODE=true`` (``EXIT_CODE_COMPAT`` is
removed from their env), and a scenario is only ``ok`` when the child exited 0
*and* its junit XML (when present) reports no failed/errored testcases.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import itertools
import os
import shutil
import signal
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace

from testzeus_hercules.reporting.bundle import parse_junit_testcases
from testzeus_hercules.utils.gherkin_helper import split_feature_file
from testzeus_hercules.utils.junit_helper import JUnitXMLGenerator
from testzeus_hercules.utils.logger import logger

STDOUT_TAIL_LINES = 100
DEFAULT_TIMEOUT_S = 1800.0
WORKSPACE_SEQ = itertools.count(1)
KEEP_WORKSPACES_ENV = "HERCULES_KEEP_WORKSPACES"
# Workspace-scoped paths injected into every subprocess env; a suite YAML env
# entry with the same name replaces them (operator config is honored, loudly).
RESERVED_INJECTED_ENV_VARS = frozenset({"HERCULES_HISTORY_DB", "PROJECT_SOURCE_ROOT", "INPUT_GHERKIN_FILE_PATH", "TEST_DATA_PATH"})
# Upper bound on waiting out a killed child's pipes: when a wedged browser
# process group survived the kill, reaping must not outlive the deadline.
REAP_TIMEOUT_S = 10.0
# Grace between SIGTERM and SIGKILL when the runner itself is interrupted or exits
# with scenario subprocesses still alive (lets chromium close cleanly).
TERMINATE_GRACE_S = 3.0
# Process-group leaders (== pids; children are started with start_new_session=True)
# of scenario subprocesses that are currently running.
_LIVE_GROUPS: set[int] = set()


@dataclass
class FeatureResult:
    """Outcome of one isolated feature/scenario subprocess run."""

    scenario: str
    feature_path: str
    workspace: str
    ok: bool = False
    exit_code: int | None = None
    duration_s: float = 0.0
    junit_xml: str | None = None
    stdout_tail: str = ""
    error: str | None = None


def _subprocess_command(workspace: str) -> list[str]:
    """Command executed inside ``workspace`` (cwd); injectable for tests."""
    del workspace  # the default command relies on cwd-relative config defaults
    return [sys.executable, "-u", "-m", "testzeus_hercules"]


def _prepare_workspace(base_dir: str, feature_path: str, index: int) -> str:
    """Create an isolated ``<base>/scenario_<index>`` workspace for one run.

    Layout: ``<workspace>/opt/input/test.feature`` (a copy of the single-scenario
    feature file) plus a copy of the ``test_data/`` directory when it sits next
    to the source feature. Returns the workspace path (the subprocess cwd).
    """
    feature_path = os.path.abspath(feature_path)
    workspace = os.path.join(os.path.abspath(base_dir), f"scenario_{index}")
    input_dir = os.path.join(workspace, "opt", "input")
    os.makedirs(input_dir, exist_ok=True)
    shutil.copy2(feature_path, os.path.join(input_dir, "test.feature"))

    sibling_test_data = os.path.join(os.path.dirname(feature_path), "test_data")
    if os.path.isdir(sibling_test_data):
        shutil.copytree(sibling_test_data, os.path.join(workspace, "opt", "test_data"), dirs_exist_ok=True)
    return workspace


def keep_workspaces_enabled() -> bool:
    """True when ``HERCULES_KEEP_WORKSPACES=1`` opts out of workspace cleanup."""
    return os.getenv(KEEP_WORKSPACES_ENV, "") == "1"


def cleanup_successful_workspaces(results: list[FeatureResult]) -> None:
    """Delete the workspaces of successful scenario runs; failures are always kept.

    Callers must invoke this only after the per-scenario junit XMLs were staged
    into a merged report (the XMLs live inside the workspaces). Failures keep
    their whole workspace (opt/output, videos, logs) for debugging, and
    ``HERCULES_KEEP_WORKSPACES=1`` keeps everything. Never raises.
    """
    if keep_workspaces_enabled():
        return
    for result in results:
        if not result.ok or not result.workspace or not os.path.isdir(result.workspace):
            continue
        shutil.rmtree(result.workspace, ignore_errors=True)
        logger.debug("Removed successful workspace %s", result.workspace)


def redact_cleaned_workspace_paths(results: list[FeatureResult]) -> None:
    """Blank workspace/junit paths that :func:`cleanup_successful_workspaces` removed.

    Call right after cleanup: the workspaces (and the junit XMLs inside them)
    of successful scenarios no longer exist, so returned summaries must not
    surface dangling paths. Failures keep their workspace (and real paths), and
    merged-report paths live outside the workspaces so they are untouched.
    Mutates the results in place; never raises.
    """
    for result in results:
        if result.ok and result.workspace and not os.path.isdir(result.workspace):
            result.workspace = ""
            result.junit_xml = None


def _history_db_for_subprocess() -> str | None:
    """``HERCULES_HISTORY_DB`` value to hand to subprocesses; None to leave unset.

    Every scenario gets its own throwaway workspace (and its own
    ``PROJECT_SOURCE_ROOT``), so without help each child's run history DB would
    land inside a directory that is deleted after the run, silently forking the
    run history. When the parent itself has not pinned ``HERCULES_HISTORY_DB``,
    inject the host's default location, resolved exactly like
    ``run_db.RunHistory._default_db_url`` (env var first, then config), so the
    parent and all children share one history database. A relative fallback
    path is anchored to the parent cwd so it cannot resolve into a child's
    workspace.
    """
    if os.getenv("HERCULES_HISTORY_DB", "").strip():
        return None  # parent already pinned it; leave untouched
    try:
        from testzeus_hercules.reporting.run_db import (
            RunHistory,  # lazy: pulls in sqlalchemy
        )

        url = RunHistory._default_db_url()
    except Exception as exc:  # noqa: BLE001 - a missing history DB must never break a run
        logger.warning("Could not resolve host history DB for subprocess; child uses its own default: %s", exc)
        return None
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url[len(prefix) :].startswith("/"):
        url = prefix + os.path.abspath(url[len(prefix) :])
    return url


def _sweep_old_workroots() -> None:
    """Retention sweep of old ``.hercules/parallel_*`` workroots (failed runs keep theirs)."""
    try:
        from testzeus_hercules.orchestrator.scheduler import (  # lazy: scheduler imports this module
            sweep_old_parallel_workroots,
        )

        sweep_old_parallel_workroots()
    except Exception as exc:  # noqa: BLE001 - cleanup must never break a run
        logger.warning("Retention sweep of old parallel workroots failed: %s", exc)


def _default_workroot() -> str:
    """Unique-per-invocation default workroot (same-second runs must not collide)."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(os.getcwd(), ".hercules", f"parallel_{stamp}_{uuid.uuid4().hex}")


def _find_junit_xml(workspace: str) -> str | None:
    """Newest ``<workspace>/opt/output/*/*_result.xml`` produced by the run."""
    pattern = os.path.join(workspace, "opt", "output", "*", "*_result.xml")
    candidates = [path for path in glob.glob(pattern) if os.path.isfile(path)]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _junit_failure_count(junit_xml: str | None) -> int:
    """Number of failed/errored testcases in a child's junit XML (0 when absent/unparsable)."""
    if not junit_xml or not os.path.isfile(junit_xml):
        return 0
    try:
        return sum(1 for case in parse_junit_testcases(junit_xml) if case["status"] in ("failed", "error"))
    except Exception as exc:  # noqa: BLE001 - a corrupt report falls back to the exit code
        logger.warning("Could not parse junit xml %s: %s", junit_xml, exc)
        return 0


def _stdout_tail(stdout: bytes | str, stderr: bytes | str = b"") -> str:
    """Last STDOUT_TAIL_LINES lines of combined subprocess output."""
    if isinstance(stdout, bytes):
        stdout = stdout.decode(errors="replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    combined = (stdout or "") + "\n" + (stderr or "")
    lines = [line for line in combined.splitlines()]
    return "\n".join(lines[-STDOUT_TAIL_LINES:])


def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill the child's whole process group, falling back to the direct child.

    Subprocesses are launched with ``start_new_session=True``, so the child
    leads its own process group: killing the group also takes down descendants
    (the Playwright node driver and chromium) that a bare ``proc.kill()`` would
    orphan. Grandchildren inherit the stdout/stderr pipes, so orphaning them
    would stall pipe reaping long after the per-scenario deadline.
    """
    # os.killpg/getpgid do not exist on Windows: guard like
    # core/tools/execute_python_sandbox._kill_process_tree so the AttributeError
    # cannot skip the direct-kill fallback and leave the child running.
    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass  # child already reaped / ungroupable; the direct kill still runs
    try:
        proc.kill()
    except OSError:
        pass  # the child was reaped between the timeout and the kill


def _signal_group(pgid: int, sig: int) -> bool:
    """Send ``sig`` to a process group; True while the group (still) exists."""
    if not hasattr(os, "killpg"):
        return False
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def terminate_live_children(grace_s: float = TERMINATE_GRACE_S) -> int:
    """SIGTERM every live scenario process group, wait ``grace_s``, then SIGKILL the rest.

    Safety net for the CLI entry points: whatever way the runner exits
    (SIGTERM, SIGINT, an exception, normal exit) no ``python -m testzeus_hercules``
    or browser tree may be left behind (orphaned to ppid 1). Returns the number
    of groups that were still alive. Never raises.
    """
    groups = sorted(_LIVE_GROUPS)
    _LIVE_GROUPS.clear()
    alive = [pgid for pgid in groups if _signal_group(pgid, signal.SIGTERM)]
    deadline = time.monotonic() + max(0.0, grace_s)
    while alive and time.monotonic() < deadline:
        time.sleep(0.05)
        for pgid in list(alive):
            try:
                # Reap the leader if it is our (zombie) child so the group can disappear.
                os.waitpid(pgid, os.WNOHANG)
            except (ChildProcessError, OSError):
                pass
        alive = [pgid for pgid in alive if _signal_group(pgid, 0)]
    for pgid in alive:
        _signal_group(pgid, signal.SIGKILL)
    return len(groups)


async def _terminate_process_tree(proc: asyncio.subprocess.Process, grace_s: float = TERMINATE_GRACE_S) -> None:
    """Graceful tree shutdown for a cancelled run: SIGTERM the group, wait, then SIGKILL it."""
    if hasattr(os, "killpg"):
        _signal_group(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=grace_s)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - escalate to SIGKILL below
            pass
    _kill_process_tree(proc)
    try:
        await asyncio.wait_for(proc.wait(), timeout=REAP_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - reaping a dead child must never raise
        pass


class CliInterrupted(Exception):
    """Raised by :func:`run_cli` when SIGINT/SIGTERM interrupted the run (``exit_code`` = 128 + signal)."""

    def __init__(self, signum: int):
        super().__init__(f"interrupted by signal {signum}")
        self.signum = signum
        self.exit_code = 128 + int(signum)


def run_cli(coro):
    """``asyncio.run`` for the CLI entry points with orphan-proof signal handling.

    SIGINT/SIGTERM cancel the main task (so every running scenario subprocess
    gets a SIGTERM -> grace -> SIGKILL of its whole process group, see
    :func:`run_feature_isolated`) and surface as :class:`CliInterrupted` instead
    of a traceback. A ``finally`` safety net terminates any process group that
    is still registered, whatever the exit path.
    """
    received: list[int] = []

    async def _runner():
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        installed: list[int] = []

        def _on_signal(signum: int) -> None:
            received.append(signum)
            task.cancel()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _on_signal, int(sig))
                installed.append(sig)
            except (NotImplementedError, RuntimeError, ValueError):  # Windows / non-main thread
                pass
        try:
            return await coro
        finally:
            for sig in installed:
                try:
                    loop.remove_signal_handler(sig)
                except Exception:  # noqa: BLE001
                    pass

    try:
        return asyncio.run(_runner())
    except asyncio.CancelledError:
        if received:
            raise CliInterrupted(received[0]) from None
        raise
    except KeyboardInterrupt:
        raise CliInterrupted(int(signal.SIGINT)) from None
    finally:
        terminate_live_children()


async def run_feature_isolated(feature_path: str, workroot: str, env_extra: dict[str, str] | None = None, timeout_s: float = DEFAULT_TIMEOUT_S) -> FeatureResult:
    """Run one (single-scenario) feature file in an isolated subprocess.

    The subprocess gets its own workspace under ``workroot``, runs
    ``python -u -m testzeus_hercules`` with cwd=workspace and
    ``os.environ + env_extra``, and its stdout/stderr is captured (tail kept).
    On timeout the child's whole process group is killed and the result is
    marked with an error.
    """
    index = next(WORKSPACE_SEQ)
    workspace = _prepare_workspace(workroot, feature_path, index)

    env = os.environ.copy()
    env["PROJECT_SOURCE_ROOT"] = os.path.join(workspace, "opt")
    env["INPUT_GHERKIN_FILE_PATH"] = os.path.join(workspace, "opt", "input", "test.feature")
    env["TEST_DATA_PATH"] = os.path.join(workspace, "opt", "test_data")
    history_db = _history_db_for_subprocess()
    if history_db is not None:
        env["HERCULES_HISTORY_DB"] = history_db
    # The child's exit status is part of the verdict: Hercules exits 0 on test
    # failures unless STRICT_EXIT_CODE is on, and an inherited EXIT_CODE_COMPAT=1
    # would force 0 even then. (The junit verdict below is the second line.)
    env["STRICT_EXIT_CODE"] = "true"
    env.pop("EXIT_CODE_COMPAT", None)
    for key, value in (env_extra or {}).items():
        key = str(key)
        if key in RESERVED_INJECTED_ENV_VARS:
            # Operator config is honored, but loudly: e.g. a relative
            # HERCULES_HISTORY_DB would resolve inside this throwaway workspace
            # and silently vanish when the workspace is cleaned up.
            logger.warning("env override %s=%r replaces the workspace-scoped %s injected for this subprocess; the override wins.", key, value, key)
        env[key] = str(value)

    command = _subprocess_command(workspace)
    scenario = os.path.splitext(os.path.basename(feature_path))[0]
    logger.info("Starting isolated run for %s in %s", scenario, workspace)
    start = time.monotonic()

    exit_code: int | None = None
    error: str | None = None
    stdout: bytes | str = ""
    stderr: bytes | str = ""
    try:
        proc = await asyncio.create_subprocess_exec(*command, cwd=workspace, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    except Exception as exc:
        duration = time.monotonic() - start
        return FeatureResult(
            scenario=scenario,
            feature_path=feature_path,
            workspace=workspace,
            ok=False,
            exit_code=None,
            duration_s=duration,
            junit_xml=None,
            stdout_tail="",
            error=f"Failed to start subprocess: {type(exc).__name__}: {exc}",
        )

    _LIVE_GROUPS.add(proc.pid)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        exit_code = proc.returncode
    except asyncio.CancelledError:
        # The runner is being interrupted (SIGINT/SIGTERM or an outer cancel):
        # take the child's whole tree down instead of orphaning chromium.
        await _terminate_process_tree(proc)
        raise
    except asyncio.TimeoutError:
        _kill_process_tree(proc)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=REAP_TIMEOUT_S)
        except asyncio.TimeoutError:
            # A wedged browser can survive even the group kill and keep the
            # pipes open; never let reaping outlive the deadline.
            logger.warning("Subprocess %s pipes still held %gs after timeout kill; proceeding without its full output.", proc.pid, REAP_TIMEOUT_S)
            stdout, stderr = b"", b""
        except Exception:  # noqa: BLE001 - reaping a dead child must never raise
            stdout, stderr = b"", b""
        error = f"Timed out after {timeout_s:g}s; process killed."
    finally:
        _LIVE_GROUPS.discard(proc.pid)

    duration = time.monotonic() - start
    junit_xml = _find_junit_xml(workspace)
    if error is None and exit_code != 0:
        error = f"Subprocess exited with code {exit_code}."
    if error is None:
        # Exit 0 is not proof of success (e.g. an operator env override
        # re-enabling EXIT_CODE_COMPAT): trust the child's junit verdict.
        junit_failures = _junit_failure_count(junit_xml)
        if junit_failures:
            error = f"{junit_failures} testcase(s) failed or errored per junit report (subprocess exited 0)."
    result = FeatureResult(
        scenario=scenario,
        feature_path=feature_path,
        workspace=workspace,
        ok=error is None,
        exit_code=exit_code,
        duration_s=duration,
        junit_xml=junit_xml,
        stdout_tail=_stdout_tail(stdout, stderr),
        error=error,
    )
    logger.info("Isolated run for %s finished: ok=%s exit_code=%s (%.1fs)", scenario, result.ok, result.exit_code, duration)
    return result


async def run_scenarios_parallel(
    scenario_files: list[dict],
    max_parallel: int = 4,
    workroot: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    progress_cb: Callable[[str, FeatureResult], None] | None = None,
    env_extra: dict[str, str] | None = None,
) -> list[FeatureResult]:
    """Run ``scenario_files`` (as produced by ``split_feature_file``) in parallel.

    Each entry needs keys ``{"feature", "scenario", "output_file"}``. At most
    ``max_parallel`` subprocesses run concurrently (semaphore-bounded gather);
    results are returned in input order. ``progress_cb(scenario, result)`` is
    called once per scenario on completion; a raising callback is logged and
    ignored. ``env_extra`` is merged into every subprocess environment.
    """
    if not scenario_files:
        return []
    max_parallel = max(1, int(max_parallel))
    if workroot is None:
        workroot = _default_workroot()
    os.makedirs(workroot, exist_ok=True)
    semaphore = asyncio.Semaphore(max_parallel)

    async def _run_one(item: dict) -> FeatureResult:
        async with semaphore:
            result = await run_feature_isolated(item["output_file"], workroot, env_extra=env_extra, timeout_s=timeout_s)
            result = replace(result, scenario=item.get("scenario") or result.scenario)
            if progress_cb is not None:
                try:
                    progress_cb(result.scenario, result)
                except Exception as exc:  # a broken sink must never break a run
                    logger.warning("progress_cb raised for %s; ignoring: %s", result.scenario, exc)
            return result

    outcomes = await asyncio.gather(*(_run_one(item) for item in scenario_files), return_exceptions=True)
    results: list[FeatureResult] = []
    for item, outcome in zip(scenario_files, outcomes):
        if isinstance(outcome, FeatureResult):
            results.append(outcome)
            continue
        # One crashed _run_one (e.g. workspace prep OSError on a vanished
        # feature file) must not discard the sibling results that are already
        # running or finished: record a failed entry in its place.
        scenario = item.get("scenario") or os.path.splitext(os.path.basename(item.get("output_file", "")))[0]
        logger.error("Scenario %s crashed outside the subprocess: %s", scenario, outcome)
        results.append(
            FeatureResult(
                scenario=str(scenario),
                feature_path=str(item.get("output_file", "")),
                workspace="",
                ok=False,
                exit_code=None,
                duration_s=0.0,
                junit_xml=None,
                stdout_tail="",
                error=f"{type(outcome).__name__}: {outcome}",
            )
        )
    return results


def _stage_junit_copies(results: list[FeatureResult], out_dir: str) -> list[str]:
    """Copy each result's junit XML into ``out_dir`` and return the copy paths.

    ``JUnitXMLGenerator.merge_junit_xml`` deletes its inputs, so merging staged
    copies keeps the originals inside each workspace intact for debugging.
    """
    staged: list[str] = []
    for index, result in enumerate(results):
        source = result.junit_xml
        if not source or not os.path.isfile(source):
            if result.junit_xml:
                logger.warning("junit xml missing for scenario %s: %s", result.scenario, result.junit_xml)
            continue
        dest = os.path.join(out_dir, f"part_{index:04d}.xml")
        shutil.copy2(source, dest)
        staged.append(dest)
    return staged


async def merge_results_async(results: list[FeatureResult], out_dir: str) -> dict:
    """Merge per-scenario junit XMLs into ``<out_dir>/merged_result.xml``.

    Returns totals ``{"total", "passed", "failed", "errors", "junit_xml"}``
    computed by parsing the merged XML. Handles the no-results case (empty
    merged file, zero totals).
    """
    os.makedirs(out_dir, exist_ok=True)
    merged_path = os.path.join(out_dir, "merged_result.xml")
    staged = _stage_junit_copies(results, out_dir)
    await JUnitXMLGenerator.merge_junit_xml(staged, merged_path)

    testcases = parse_junit_testcases(merged_path)
    totals = {
        "total": len(testcases),
        "passed": sum(1 for case in testcases if case["status"] == "passed"),
        "failed": sum(1 for case in testcases if case["status"] == "failed"),
        "errors": sum(1 for case in testcases if case["status"] == "error"),
        "junit_xml": merged_path,
    }
    logger.info("Merged junit results: %s", {key: totals[key] for key in ("total", "passed", "failed", "errors")})
    return totals


def merge_results(results: list[FeatureResult], out_dir: str) -> dict:
    """Sync wrapper around :func:`merge_results_async`.

    Uses ``asyncio.run`` only when no event loop is running; when called from
    inside a running loop, await :func:`merge_results_async` instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(merge_results_async(results, out_dir))
    raise RuntimeError("merge_results() called inside a running event loop; await merge_results_async() instead.")


def _fold_missing_results_into_totals(results: list[FeatureResult], merged: dict) -> dict:
    """Testcase-level totals: merged XML counts plus runs that produced no junit.

    Results without a junit XML (crashed/timeout subprocesses) are not
    represented in the merged file and are added here so failures never vanish
    (mirrors ``suites._totals_from_results``; kept local because ``suites``
    imports this module, so the reverse import would be circular). Non-count
    keys of ``merged`` (e.g. ``junit_xml``) are passed through untouched.
    """
    totals = dict(merged)
    for key in ("total", "passed", "failed", "errors"):
        totals[key] = int(merged.get(key, 0) or 0)
    for result in results:
        if result.junit_xml and os.path.isfile(result.junit_xml):
            continue  # already counted via the merged XML
        totals["total"] += 1
        if result.ok:
            totals["passed"] += 1
        elif result.exit_code is None:
            totals["errors"] += 1
        else:
            totals["failed"] += 1
    return totals


async def run_feature_file_parallel(feature_file: str, max_parallel: int = 4, out_dir: str | None = None, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict:
    """Main entry: split a feature file and run its scenarios in parallel.

    Splits ``feature_file`` into per-scenario files, runs them through
    :func:`run_scenarios_parallel`, merges the junit XMLs and returns totals
    plus a per-scenario summary and the merged xml path.
    """
    feature_file = os.path.abspath(feature_file)
    if not os.path.isfile(feature_file):
        raise FileNotFoundError(f"Feature file not found: {feature_file}")

    _sweep_old_workroots()
    workroot = _default_workroot()
    out_dir = out_dir or os.path.join(workroot, "merged")
    split_dir = os.path.join(workroot, "split")
    scenario_items = await split_feature_file(feature_file, split_dir)

    if not scenario_items:
        logger.error("No scenarios found in feature file: %s", feature_file)
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "junit_xml": None,
            "workroot": workroot,
            "merged_xml": None,
            "results": [],
            "error": f"No scenarios found in feature file: {feature_file}",
        }

    results = await run_scenarios_parallel(scenario_items, max_parallel=max_parallel, workroot=workroot, timeout_s=timeout_s)

    os.makedirs(out_dir, exist_ok=True)
    totals = await merge_results_async(results, out_dir)
    # Failures must not vanish from the totals: a run whose junit XML is
    # missing (timeout/crash) is absent from the merged file, so fold it in
    # here. Must run before the cleanup/redaction below, which nulls the
    # junit_xml of successful runs and would make them look "missing" (and
    # double-count them).
    totals = _fold_missing_results_into_totals(results, totals)
    # The junit XMLs are staged inside out_dir now, so the workspaces of
    # successful runs are no longer needed (failures keep theirs). The returned
    # per-scenario summaries must not point into the removed directories.
    cleanup_successful_workspaces(results)
    redact_cleaned_workspace_paths(results)
    return {
        **totals,
        "workroot": workroot,
        "merged_xml": totals["junit_xml"],
        "feature_file": feature_file,
        "results": [
            {
                "scenario": result.scenario,
                "ok": result.ok,
                "exit_code": result.exit_code,
                "duration_s": result.duration_s,
                "junit_xml": result.junit_xml,
                "error": result.error,
            }
            for result in results
        ],
    }


def _exit_code_for(totals: dict) -> int:
    """1 when any scenario failed/errored; 0 when EXIT_CODE_COMPAT=1 is set."""
    if os.getenv("EXIT_CODE_COMPAT", "") == "1":
        return 0
    return 1 if (totals.get("failed", 0) or 0) > 0 or (totals.get("errors", 0) or 0) > 0 else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry: run a feature file's scenarios in parallel and report."""
    parser = argparse.ArgumentParser(prog="hercules-run-manager", description="Run a Gherkin feature file with parallel isolated scenario subprocesses.")
    parser.add_argument("feature_file", help="Path to the .feature file to run.")
    parser.add_argument("--max-parallel", type=int, default=4, help="Maximum concurrent scenario subprocesses (default: 4).")
    parser.add_argument("--out-dir", default=None, help="Directory for the merged junit XML (default: <workroot>/merged).")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="Per-scenario timeout in seconds (default: 1800).")
    args = parser.parse_args(argv)

    try:
        summary = run_cli(run_feature_file_parallel(args.feature_file, max_parallel=args.max_parallel, out_dir=args.out_dir, timeout_s=args.timeout))
    except CliInterrupted as exc:
        print(f"hercules-run-manager: interrupted (signal {exc.signum}); scenario processes terminated.", file=sys.stderr)
        return exc.exit_code
    except FileNotFoundError as exc:
        print(f"hercules-run-manager: error: {exc}", file=sys.stderr)
        return 2
    if summary.get("error") and not summary.get("results"):
        # Nothing ran (e.g. a feature file without scenarios): that is not a
        # green run, whatever EXIT_CODE_COMPAT says.
        print(f"Error: {summary['error']}", file=sys.stderr)
        return 2

    for result in summary.get("results", []):
        status = "ok" if result["ok"] else ("error" if result["error"] and result["exit_code"] is None else "FAILED")
        suffix = f" :: {result['error']}" if result["error"] else ""
        print(f"[{status}] {result['scenario']} ({result['duration_s']:.1f}s){suffix}")

    print(f"Totals: {summary['total']} total, {summary['passed']} passed, {summary['failed']} failed, {summary['errors']} errors")
    if summary.get("merged_xml"):
        print(f"Merged junit XML: {summary['merged_xml']}")
    if summary.get("workroot"):
        print(f"Workspaces: {summary['workroot']}")
    return _exit_code_for(summary)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
