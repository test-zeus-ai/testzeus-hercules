"""Monitoring daemon: run suites on a schedule, detect regressions, notify.

Config lives in ``.hercules/monitor.yaml`` (see
``.hercules/monitor.example.yaml``)::

    suites: [smoke, regression]
    config: .hercules/suites.yaml
    interval_hours: 24
    webhook_url: https://hooks.example.com/services/...
    notify_on: regression   # always | failure | regression
    baseline: last_run      # rolling, or a specific run_id
    state_file: monitor_state.json   # relative paths are anchored to this YAML's directory

State (``{"last_results": ..., "runs": {run_id: summary}, ...}``) is a plain
JSON file, written atomically: the previous cycle's per-scenario ok-set is the
regression baseline (``last_run`` rolling baseline) and ``baseline: <run_id>``
pins an older run. Before every cycle, ``suite_runs/`` trees (and
``.hercules/parallel_*`` run-manager workroots) older than
``HERCULES_RUN_RETENTION_HOURS`` (int hours, default 72) are swept; trees
younger than 1 hour are never swept, so ``0`` cannot delete a concurrent
manual ``hercules-suite`` run's workspace mid-flight.

Within one cycle, suites execute in their listed order and each suite runs its
OWN features; dependency results come from the per-cycle feature map, so a
cycle listing both ``smoke`` and ``regression`` (``depends_on: [smoke]``)
executes smoke's features exactly once.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import shutil
import sys
import time
from contextlib import suppress
from typing import Any

import yaml
from testzeus_hercules.orchestrator import run_manager, suites
from testzeus_hercules.orchestrator.suites import resolve_config_path
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.redact import redact_webhook_text, redact_webhook_url

DEFAULT_CONFIG_PATH = ".hercules/monitor.yaml"
DEFAULT_SUITES_CONFIG = suites.DEFAULT_SUITES_PATH
DEFAULT_STATE_FILE = ".hercules/monitor_state.json"
NOTIFY_ON_OPTIONS = ("always", "failure", "regression")
MAX_KEPT_RUNS = 10
RUN_SEQ = itertools.count(1)
HERCULES_DIR = ".hercules"
SUITE_RUNS_DIR = os.path.join(HERCULES_DIR, "suite_runs")
PARALLEL_WORKROOT_PREFIX = "parallel_"
DEFAULT_RETENTION_HOURS = 72
RETENTION_HOURS_ENV = "HERCULES_RUN_RETENTION_HOURS"
# Grace window: a tree created seconds ago may belong to a concurrent manual
# `hercules-suite` run; sweeps must never touch trees younger than this even
# when HERCULES_RUN_RETENTION_HOURS=0 asks for immediate deletion.
MIN_RETENTION_HOURS = 1


def _resolve_state_file(data: dict, config_dir: str) -> str:
    """Resolve the ``state_file`` entry: absolute as-is, relative to the monitor YAML's directory.

    A relative state file that already exists in the YAML's directory, or (for state written by
    older versions) in the cwd, keeps being used. One that exists nowhere is created next to
    the YAML, never wherever the daemon happened to be started from. The default
    (no ``state_file`` key) stays relative to the cwd, i.e. the project root.
    """
    entry = data.get("state_file")
    if entry is None:
        return resolve_config_path(DEFAULT_STATE_FILE, config_dir)
    entry = str(entry)
    resolved = resolve_config_path(entry, config_dir)
    if os.path.isabs(resolved):
        return resolved
    # An entry that already names a path inside the YAML's own directory when read from the cwd
    # (the old shipped example: `.hercules/monitor_state.json` next to `.hercules/monitor.yaml`)
    # must not be nested a second time under that directory.
    from_cwd = os.path.abspath(entry)
    if os.path.realpath(os.path.dirname(from_cwd)) == os.path.realpath(config_dir):
        return from_cwd
    return os.path.normpath(os.path.join(config_dir, entry))


def load_monitor_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Load and validate a monitor YAML file; returns a normalized config dict."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Monitor config not found: {path}")
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Monitor config {path} must be a mapping, got: {type(data).__name__}")

    config_dir = os.path.dirname(os.path.abspath(path))
    suites_list = data.get("suites")
    if not isinstance(suites_list, list) or not suites_list or not all(isinstance(name, str) for name in suites_list):
        raise ValueError(f"Monitor config {path}: 'suites' must be a non-empty list of suite names.")

    notify_on = data.get("notify_on", "regression")
    if notify_on not in NOTIFY_ON_OPTIONS:
        raise ValueError(f"Monitor config {path}: 'notify_on' must be one of {NOTIFY_ON_OPTIONS}, got: {notify_on!r}.")

    interval_hours = data.get("interval_hours", 24)
    try:
        interval_hours = float(interval_hours)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Monitor config {path}: 'interval_hours' must be a number, got: {data.get('interval_hours')!r}.") from exc
    if interval_hours <= 0:
        raise ValueError(f"Monitor config {path}: 'interval_hours' must be positive, got: {interval_hours}.")

    webhook_url = data.get("webhook_url")
    if webhook_url is not None and not isinstance(webhook_url, str):
        raise ValueError(f"Monitor config {path}: 'webhook_url' must be a string URL.")

    baseline = data.get("baseline", "last_run")
    if not isinstance(baseline, str) or not baseline:
        raise ValueError(f"Monitor config {path}: 'baseline' must be 'last_run' or a run_id string.")

    return {
        "suites": suites_list,
        "config": resolve_config_path(str(data.get("config", DEFAULT_SUITES_CONFIG)), config_dir),
        "interval_hours": interval_hours,
        "webhook_url": webhook_url,
        "notify_on": notify_on,
        "baseline": baseline,
        "state_file": _resolve_state_file(data, config_dir),
        "config_path": os.path.abspath(path),
    }


def load_state(path: str) -> dict:
    """Load the monitor state JSON; missing or corrupt files yield ``{}``."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read monitor state %s (%s); starting fresh.", path, exc)
        return {}
    return state if isinstance(state, dict) else {}


def save_state(path: str, state: dict) -> None:
    """Persist the monitor state JSON atomically (tmp file + ``os.replace``).

    A crash mid-write can never truncate the previous state (which holds the
    regression baseline): readers either see the old file or the new one, never
    a partial write.
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
    except Exception:
        with suppress(OSError):
            os.remove(tmp_path)
        raise


def retention_hours() -> int:
    """Retention window for old suite_runs trees (``HERCULES_RUN_RETENTION_HOURS``, default 72).

    ``0`` is a valid parse result ("delete as soon as possible"); the sweep
    itself never deletes trees younger than :data:`MIN_RETENTION_HOURS` (1
    hour) so a concurrent manual suite run cannot lose its workspace mid-run.
    """
    raw = os.getenv(RETENTION_HOURS_ENV, "").strip()
    try:
        hours = int(raw)
    except ValueError:
        return DEFAULT_RETENTION_HOURS
    return max(0, hours)


def _sweep_old_trees(base_dir: str, hours: int, name_prefix: str = "") -> list[str]:
    """Remove directories in ``base_dir`` (optionally only ``name_prefix*``) older than ``hours``."""
    if not os.path.isdir(base_dir):
        return []
    cutoff = time.time() - hours * 3600
    removed: list[str] = []
    for name in sorted(os.listdir(base_dir)):
        if name_prefix and not name.startswith(name_prefix):
            continue
        path = os.path.join(base_dir, name)
        if not os.path.isdir(path):
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime <= cutoff:
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                removed.append(path)
    return removed


def _effective_retention_hours(retention_hours_override: int | None) -> int:
    return max(retention_hours_override if retention_hours_override is not None else retention_hours(), MIN_RETENTION_HOURS)


def sweep_old_suite_runs(base_dir: str | None = None, retention_hours_override: int | None = None) -> list[str]:
    """Delete ``suite_runs/`` trees older than the retention window; returns removed paths.

    Every monitor cycle (and suite run) leaves a whole per-scenario workspace
    tree behind, so without a sweep the disk grows without bound. Only
    directories strictly older than the cutoff are removed; the cutoff never
    lands within the last hour (``max(hours, 1)``) so a retention of ``0``
    cannot sweep away a concurrent run's seconds-old tree. Failures to remove
    are ignored so monitoring never breaks over cleanup.
    """
    if base_dir is None:
        base_dir = os.path.join(os.getcwd(), SUITE_RUNS_DIR)
    hours = _effective_retention_hours(retention_hours_override)
    removed = _sweep_old_trees(base_dir, hours)
    if removed:
        logger.info("Retention sweep removed %d old suite run tree(s) older than %dh from %s", len(removed), hours, base_dir)
    return removed


def sweep_old_parallel_workroots(base_dir: str | None = None, retention_hours_override: int | None = None) -> list[str]:
    """Delete ``.hercules/parallel_*`` workroots (hercules-run-manager) older than the retention window.

    Same window and 1-hour floor as :func:`sweep_old_suite_runs`; failed
    scenarios keep their workspaces inside these trees, so they would otherwise
    accumulate forever. Never raises.
    """
    if base_dir is None:
        base_dir = os.path.join(os.getcwd(), HERCULES_DIR)
    hours = _effective_retention_hours(retention_hours_override)
    removed = _sweep_old_trees(base_dir, hours, name_prefix=PARALLEL_WORKROOT_PREFIX)
    if removed:
        logger.info("Retention sweep removed %d old parallel workroot(s) older than %dh from %s", len(removed), hours, base_dir)
    return removed


def _scenario_map(summary: dict | None) -> dict[str, bool]:
    """Flatten a run summary into ``{scenario_id: ok}`` (suite-qualified when combined)."""
    mapping: dict[str, bool] = {}
    if not summary:
        return mapping
    for suite_name, aggregate in (summary.get("suites") or {}).items():
        for entry in (aggregate or {}).get("per_scenario") or []:
            mapping[f"{suite_name}::{entry.get('scenario')}"] = bool(entry.get("ok"))
    for entry in summary.get("per_scenario") or []:
        mapping[str(entry.get("scenario"))] = bool(entry.get("ok"))
    return mapping


def _pass_rate(summary: dict | None) -> float:
    if not summary:
        return 0.0
    total = int(summary.get("total", 0) or 0)
    if total <= 0:
        return 0.0
    return int(summary.get("passed", 0) or 0) / total


def evaluate_regression(current: dict, baseline: dict | None) -> dict:
    """Compare per-scenario ok-sets between the current run and a baseline.

    ``new_failures`` are scenarios that passed in the baseline but fail now
    (that is what ``regressed`` means); ``fixed`` are the reverse. Scenarios
    that are new since the baseline count towards the pass rate but never as
    regressions. Without a baseline (first run) nothing is flagged regressed.

    Note: scenario keys differ between execution modes (``max_parallel > 1``
    reports per-scenario titles while sequential runs report feature-level
    names), so keep ``max_parallel`` stable across cycles or baselines will
    silently miss renames/mode flips and regressions go undetected.
    """
    current_map = _scenario_map(current)
    pass_rate_current = _pass_rate(current)
    if not baseline:
        return {"regressed": False, "new_failures": [], "fixed": [], "pass_rate_current": pass_rate_current, "pass_rate_baseline": None}
    baseline_map = _scenario_map(baseline)
    new_failures = sorted(scenario for scenario, ok in current_map.items() if not ok and baseline_map.get(scenario) is True)
    fixed = sorted(scenario for scenario, ok in current_map.items() if ok and baseline_map.get(scenario) is False)
    return {
        "regressed": bool(new_failures),
        "new_failures": new_failures,
        "fixed": fixed,
        "pass_rate_current": pass_rate_current,
        "pass_rate_baseline": _pass_rate(baseline),
    }


def _format_summary(summary: dict) -> str:
    """One-line human/Slack-readable run summary for webhook payloads."""
    regression = summary.get("regression") or {}
    new_failures = ", ".join(regression.get("new_failures") or []) or "none"
    return (
        f"Hercules monitor run {summary.get('run_id')}: {summary.get('passed', 0)}/{summary.get('total', 0)} passed, "
        f"{summary.get('failed', 0)} failed, {summary.get('errors', 0)} errors; regressed={bool(regression.get('regressed'))}; "
        f"new failures: {new_failures}"
    )


async def _post_webhook(url: str, payload: dict) -> None:
    """POST a JSON webhook payload; monkeypatched in tests."""
    import httpx  # lazy: only needed when a webhook is configured

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


def _should_notify(cfg: dict, summary: dict) -> bool:
    notify_on = cfg.get("notify_on", "regression")
    if notify_on == "always":
        return True
    if notify_on == "failure":
        return int(summary.get("failed", 0) or 0) > 0 or int(summary.get("errors", 0) or 0) > 0
    return bool((summary.get("regression") or {}).get("regressed"))


async def run_monitor_once(cfg: dict, state: dict) -> dict:
    """Run every configured suite once, evaluate regressions, notify, persist.

    Mutates and persists ``state`` (``last_results`` rolling baseline plus the
    last :data:`MAX_KEPT_RUNS` run summaries under ``runs``). Returns the
    combined summary including ``regression`` and ``notified`` keys.
    """
    suite_results: dict[str, Any] = {}
    # Per-cycle feature results (feature path -> ok): suites later in the list
    # gate on dependency features that already ran instead of re-running them,
    # so [smoke, regression(depends_on: [smoke])] executes smoke's features
    # exactly once. Dependency features absent from the map (dependency not
    # part of this cycle) are still executed by run_suite itself.
    cycle_feature_results: dict[str, bool] = {}
    sweep_old_suite_runs()  # every cycle would otherwise grow suite_runs/ forever
    sweep_old_parallel_workroots()
    for name in cfg["suites"]:
        suite_results[name] = await suites.run_suite(name, path=cfg["config"], dependency_results=cycle_feature_results)

    totals = {"total": 0, "passed": 0, "failed": 0, "errors": 0}
    for aggregate in suite_results.values():
        for key in totals:
            totals[key] += int(aggregate.get(key, 0) or 0)

    run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{next(RUN_SEQ):04d}"
    summary = {
        "run_id": run_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "suites": suite_results,
        **totals,
    }

    baseline_key = str(cfg.get("baseline", "last_run"))
    baseline_missing = False
    if baseline_key == "last_run":
        baseline = state.get("last_results")
    else:
        baseline = (state.get("runs") or {}).get(baseline_key)
        if not baseline:
            # A pinned run_id pruned from the MAX_KEPT_RUNS window would make
            # evaluate_regression treat the cycle as baseline-less: regressed
            # would silently stay False and notify_on: regression never fire.
            logger.warning("Configured baseline %s not in recent runs window; regression detection disabled this cycle", baseline_key)
            baseline_missing = True
    regression = evaluate_regression(summary, baseline)
    if baseline_missing:
        regression["baseline_missing"] = True
    summary["regression"] = regression

    runs: dict[str, Any] = dict(state.get("runs") or {})
    runs[run_id] = summary
    state["runs"] = dict(sorted(runs.items())[-MAX_KEPT_RUNS:])
    state["last_results"] = summary
    state["last_run_id"] = run_id
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    save_state(cfg["state_file"], state)

    summary["notified"] = False
    webhook_url = cfg.get("webhook_url")
    if webhook_url and _should_notify(cfg, summary):
        try:
            await _post_webhook(webhook_url, {"text": _format_summary(summary)})
            summary["notified"] = True
            logger.info("Webhook notification sent to %s", redact_webhook_url(webhook_url))
        except Exception as exc:  # a broken webhook must never break monitoring
            # httpx error text embeds the full request URL (token / Slack path secret).
            logger.warning("Webhook POST to %s failed: %s: %s", redact_webhook_url(webhook_url), type(exc).__name__, redact_webhook_text(str(exc), webhook_url))

    logger.info("Monitor cycle %s done: %s", run_id, _format_summary(summary))
    return summary


async def monitor_loop(cfg: dict) -> None:
    """Run monitor cycles forever, sleeping ``interval_hours`` between them.

    ``asyncio.sleep`` is cancellable, so SIGINT (KeyboardInterrupt under
    ``asyncio.run``) cleanly interrupts the wait between cycles.
    """
    state = load_state(cfg["state_file"])
    interval_s = float(cfg.get("interval_hours", 24)) * 3600
    logger.info("Monitor started: suites=%s interval=%.0fs state=%s", cfg["suites"], interval_s, cfg["state_file"])
    while True:
        try:
            summary = await run_monitor_once(cfg, state)
            logger.info("Cycle summary: %s", _format_summary(summary))
        except Exception as exc:  # keep the daemon alive across failing cycles
            logger.error("Monitor cycle failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(interval_s)


def _one_line(exc: BaseException) -> str:
    """Collapse an exception message (YAML errors span several lines) to a single line."""
    return " ".join(str(exc).split())


def main(argv: list[str] | None = None) -> int:
    """CLI entry (``hercules-monitor``): run the daemon or a single cycle."""
    parser = argparse.ArgumentParser(prog="hercules-monitor", description="Run Hercules suites on a schedule with regression monitoring.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help=f"Path to the monitor YAML (default: {DEFAULT_CONFIG_PATH}).")
    parser.add_argument("--once", action="store_true", help="Run a single monitor cycle, then exit (exit code reflects results).")
    parser.add_argument("--interval-hours", type=float, default=None, help="Override interval_hours from the config.")
    args = parser.parse_args(argv)

    try:
        cfg = load_monitor_config(args.config)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        # Missing/unreadable/invalid config: one clear line, not a traceback (same contract as hercules-suite).
        print(f"hercules-monitor: error: {_one_line(exc)}", file=sys.stderr)
        return 2
    if args.interval_hours is not None:
        if args.interval_hours <= 0:
            parser.error("--interval-hours must be positive.")
        cfg["interval_hours"] = args.interval_hours

    if not args.once:
        try:
            run_manager.run_cli(monitor_loop(cfg))
        except run_manager.CliInterrupted as exc:
            # SIGINT/SIGTERM is the normal way to stop the daemon: running
            # scenario process trees were already terminated, exit cleanly.
            logger.info("Monitor stopped by signal %s.", exc.signum)
        return 0

    try:
        for suite_name in cfg["suites"]:
            suites.check_suite_config(suite_name, cfg["config"])
    except (OSError, ValueError, yaml.YAMLError) as exc:
        # The suites config the monitor points at is missing, unreadable or invalid. Checked up
        # front so this handler never hides a runtime failure of the cycle itself.
        logger.debug("Suite configuration check failed", exc_info=True)
        print(f"hercules-monitor: error: {_one_line(exc)}", file=sys.stderr)
        return 2

    state = load_state(cfg["state_file"])
    try:
        summary = run_manager.run_cli(run_monitor_once(cfg, state))
    except run_manager.CliInterrupted as exc:
        print(f"hercules-monitor: interrupted (signal {exc.signum}); scenario processes terminated.", file=sys.stderr)
        return exc.exit_code
    print(_format_summary(summary))
    regression = summary.get("regression") or {}
    print(f"Regressed: {bool(regression.get('regressed'))}; new failures: {', '.join(regression.get('new_failures') or []) or 'none'}; fixed: {', '.join(regression.get('fixed') or []) or 'none'}")
    if os.getenv("EXIT_CODE_COMPAT", "") == "1":
        return 0
    return 1 if (summary.get("failed", 0) or 0) > 0 or (summary.get("errors", 0) or 0) > 0 else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
