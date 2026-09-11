"""Named test suites: YAML-defined groups of feature files with dependencies.

Suite config lives in ``.hercules/suites.yaml`` (see
``.hercules/suites.example.yaml``)::

    smoke:
      description: Quick smoke checks
      tests: [tests/test_features/smoke]
      retry: 1
      max_parallel: 2
    regression:
      tests: [tests/test_features/regression]
      depends_on: [smoke]
      env: {FOO: bar}
      shared_state: true   # exports HERCULES_SUITE_STATE_FILE to every scenario

Execution goes through :mod:`testzeus_hercules.orchestrator.run_manager`
(isolated subprocess per scenario). With ``max_parallel > 1`` every feature is
split into per-scenario runs executed in parallel; with the default
``max_parallel: 1`` features run sequentially, one subprocess per feature file
(scenario granularity then equals feature granularity for totals and retries).

``depends_on`` gates, not just orders: dependency features run first, and when
any of them fails (after its own retries) the suite's own features are skipped
(recorded as ``ok=False`` with a ``skipped: dependency '<name>' failed`` error
and counted as errors) unless the suite sets ``skip_on_dependency_failure:
false``. :func:`run_suite` accepts a ``dependency_results`` map (feature path
to ``ok``) so callers such as the monitor can gate on dependency features that
already ran earlier in the cycle instead of re-running them.

The shared-state file (``{workroot}/suite_state.json``, exported to every
scenario via ``HERCULES_SUITE_STATE_FILE``) is append-only JSONL: one JSON
object per line. :func:`run_suite` appends one completion record per suite
(a path-free summary) via :func:`append_suite_state`, which locks each append
with ``fcntl.flock`` on POSIX so concurrent writers never interleave lines.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from typing import Any

import yaml
from testzeus_hercules.orchestrator import run_manager
from testzeus_hercules.reporting.bundle import parse_junit_testcases
from testzeus_hercules.utils.logger import logger

try:
    import fcntl
except ImportError:  # pragma: no cover - platform guard for non-POSIX (Windows)
    fcntl = None  # type: ignore[assignment]

DEFAULT_SUITES_PATH = ".hercules/suites.yaml"
SHARED_STATE_ENV_VAR = "HERCULES_SUITE_STATE_FILE"
SUITE_STATE_FILENAME = "suite_state.json"
VALID_SUITE_KEYS = ("description", "tests", "plan", "tags", "retry", "depends_on", "env", "shared_state", "max_parallel", "skip_on_dependency_failure")


def _sanitize_name(name: str) -> str:
    """Filesystem-safe suite/run directory name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "suite"


def resolve_config_path(entry: str, config_dir: str) -> str:
    """Anchor a configured path: absolute as-is, else config dir, else cwd.

    Paths that exist nowhere are returned cwd-relative so downstream code can
    raise a precise error naming the missing path. Shared with the scheduler
    for its ``config``/``state_file`` entries.
    """
    if os.path.isabs(entry):
        return entry
    anchored = os.path.normpath(os.path.join(config_dir, entry))
    if os.path.exists(anchored):
        return os.path.abspath(anchored)
    if os.path.exists(entry):
        return os.path.abspath(entry)
    return entry


def _validate_str_list(value: Any, field: str, suite: str) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Suite '{suite}': '{field}' must be a string or a list of strings, got: {value!r}")
    return value


def _normalize_suite(name: str, cfg: Any, config_dir: str) -> dict:
    """Validate one suite entry and fill defaults; raises clear ValueErrors."""
    if not isinstance(cfg, dict):
        raise ValueError(f"Suite '{name}' must be a mapping with at least one of 'tests' or 'plan', got: {type(cfg).__name__}")

    unknown = sorted(set(cfg) - set(VALID_SUITE_KEYS))
    if unknown:
        logger.warning("Suite '%s' has unknown keys (ignored): %s", name, ", ".join(unknown))

    tests = cfg.get("tests")
    plan = cfg.get("plan")
    if tests is None and plan is None:
        raise ValueError(f"Suite '{name}' must define 'tests' (list of .feature files or directories) and/or 'plan' (path to test_plan.json).")
    if tests is not None and plan is not None:
        logger.warning("Suite '%s' defines both tests and plan; plan is ignored", name)

    normalized: dict[str, Any] = {}
    if cfg.get("description") is not None:
        if not isinstance(cfg["description"], str):
            raise ValueError(f"Suite '{name}': 'description' must be a string.")
        normalized["description"] = cfg["description"]
    if tests is not None:
        normalized["tests"] = [resolve_config_path(entry, config_dir) for entry in _validate_str_list(tests, "tests", name)]
    if plan is not None:
        if not isinstance(plan, str):
            raise ValueError(f"Suite '{name}': 'plan' must be a path string to a test_plan.json file.")
        normalized["plan"] = resolve_config_path(plan, config_dir)
    if cfg.get("tags") is not None:
        normalized["tags"] = _validate_str_list(cfg["tags"], "tags", name)

    retry = cfg.get("retry", 0)
    if not isinstance(retry, int) or isinstance(retry, bool) or retry < 0:
        raise ValueError(f"Suite '{name}': 'retry' must be a non-negative integer, got: {retry!r}")
    normalized["retry"] = retry

    depends_on = cfg.get("depends_on") or []
    normalized["depends_on"] = _validate_str_list(depends_on, "depends_on", name)

    env = cfg.get("env") or {}
    if not isinstance(env, dict) or not all(isinstance(key, str) for key in env):
        raise ValueError(f"Suite '{name}': 'env' must be a mapping of string keys to values.")
    normalized["env"] = {key: str(value) for key, value in env.items()}

    shared_state = cfg.get("shared_state", False)
    if not isinstance(shared_state, bool):
        raise ValueError(f"Suite '{name}': 'shared_state' must be a boolean.")
    normalized["shared_state"] = shared_state

    max_parallel = cfg.get("max_parallel", 1)
    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or max_parallel < 1:
        raise ValueError(f"Suite '{name}': 'max_parallel' must be a positive integer, got: {max_parallel!r}")
    normalized["max_parallel"] = max_parallel

    skip_on_dependency_failure = cfg.get("skip_on_dependency_failure", True)
    if not isinstance(skip_on_dependency_failure, bool):
        raise ValueError(f"Suite '{name}': 'skip_on_dependency_failure' must be a boolean, got: {skip_on_dependency_failure!r}")
    normalized["skip_on_dependency_failure"] = skip_on_dependency_failure
    return normalized


def load_suites(path: str = DEFAULT_SUITES_PATH) -> dict:
    """Load and validate a suites YAML file into ``{name: normalized cfg}``."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Suites config not found: {path}")
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        raise ValueError(f"Suites config {path} is empty; define at least one suite.")
    if not isinstance(data, dict):
        raise ValueError(f"Suites config {path} must be a mapping of suite name to suite config, got: {type(data).__name__}")

    config_dir = os.path.dirname(os.path.abspath(path))
    return {str(name): _normalize_suite(str(name), cfg, config_dir) for name, cfg in data.items()}


def _compile_suite_plan(name: str, plan_path: str, compiled_dirs: list[str] | None = None) -> str:
    """Compile a whole test_plan.json into a temporary .feature file.

    The compilation directory (a ``tempfile.mkdtemp``) is appended to
    ``compiled_dirs`` when given, so the caller can remove it once the suite
    finished (see :func:`cleanup_compiled_plans`).
    """
    from testzeus_hercules.intelligence.test_plan import (  # lazy: intelligence stack
        compile_plan_to_gherkin,
        plan_from_json,
    )

    plan = plan_from_json(plan_path)
    out_dir = tempfile.mkdtemp(prefix=f"hercules_suite_{_sanitize_name(name)}_")
    if compiled_dirs is not None:
        compiled_dirs.append(out_dir)
    out_path = os.path.join(out_dir, "plan.feature")
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(compile_plan_to_gherkin(plan))
    logger.info("Suite '%s': compiled plan %s into %s", name, plan_path, out_path)
    return out_path


def cleanup_compiled_plans(compiled_dirs: list[str]) -> None:
    """Remove the temporary compiled-plan directories of a finished suite.

    ``HERCULES_KEEP_WORKSPACES=1`` opts out (same switch as scenario workspace
    cleanup in run_manager). Never raises.
    """
    if run_manager.keep_workspaces_enabled():
        return
    for path in compiled_dirs:
        shutil.rmtree(path, ignore_errors=True)


def _expand_entry(entry: str) -> list[str]:
    """Expand one tests entry: ``*.feature`` file, or a dir's ``*.feature`` children."""
    if os.path.isdir(entry):
        features = sorted(glob.glob(os.path.join(entry, "*.feature")))
        if not features:
            raise ValueError(f"Suite tests entry '{entry}' is a directory containing no .feature files.")
        return [os.path.abspath(path) for path in features]
    if os.path.isfile(entry):
        return [os.path.abspath(entry)]
    raise ValueError(f"Suite tests entry not found: {entry}")


def _warn_ignored_dep_config(dep: str, root: str, suites: dict) -> None:
    """Warn when a dependency suite's retry/env/max_parallel would be ignored.

    Folded dependency features execute under the invoking suite's settings, so
    a dependency suite whose own ``retry``/``max_parallel``/``env`` differ is
    silently misconfigured; surface it loudly at resolve time.
    """
    dep_cfg = suites[dep]
    root_cfg = suites[root]
    differing = [
        key
        for key, default in (("retry", 0), ("max_parallel", 1), ("env", {}))
        if (dep_cfg.get(key) if dep_cfg.get(key) is not None else default) != (root_cfg.get(key) if root_cfg.get(key) is not None else default)
    ]
    if differing:
        logger.warning("Dependency suite '%s' config (retry/env/max_parallel) is ignored when folded into '%s' (differs: %s)", dep, root, ", ".join(differing))


def resolve_suite(
    name: str, suites: dict, _seen: set | None = None, compiled_dirs: list[str] | None = None, include_dependencies: bool = True, _memo: dict[str, list[str]] | None = None, _root: str | None = None
) -> list[str]:
    """Resolve a suite (dependency-first) into ordered unique feature paths.

    ``depends_on`` suites are expanded before the suite's own tests; cycles
    raise a ``ValueError``. Directories expand to their sorted direct
    ``*.feature`` children. A suite with no tests but a ``plan`` compiles the
    whole test plan into a temporary feature file; when ``compiled_dirs`` is
    given, the temporary compilation directories are appended there so the
    caller can clean them up after the run (``run_suite`` does).

    With ``include_dependencies=False`` only the suite's own features are
    returned (no ``depends_on`` expansion) — the monitor uses this to execute
    each suite's own features once per cycle and gate on dependency results
    from the already-run suites instead. Compiled plan features are memoized
    per suite name within one top-level call (``_memo``), so a diamond
    dependency through a plan-based suite compiles the plan once.

    Suite shared state (the ``shared_state`` knob) lives in an append-only
    JSONL file at ``{workroot}/suite_state.json``; ``run_suite`` appends one
    completion record per suite via :func:`append_suite_state`.
    """
    if name not in suites:
        known = ", ".join(sorted(map(str, suites))) or "(none)"
        raise ValueError(f"Unknown suite '{name}'. Known suites: {known}.")
    seen = _seen if _seen is not None else set()
    if name in seen:
        raise ValueError(f"Dependency cycle detected involving suite '{name}' (chain: {sorted(seen)}).")
    seen = seen | {name}
    memo = _memo if _memo is not None else {}
    root = _root or name

    cfg = suites[name]
    paths: list[str] = []
    if include_dependencies:
        for dep in cfg.get("depends_on") or []:
            if dep in suites:
                _warn_ignored_dep_config(dep, root, suites)
            paths.extend(resolve_suite(dep, suites, seen, compiled_dirs, include_dependencies, memo, root))

    tests = cfg.get("tests") or []
    if not tests and cfg.get("plan"):
        compiled = memo.get(name)
        if compiled is None:
            compiled = [_compile_suite_plan(name, cfg["plan"], compiled_dirs)]
            memo[name] = compiled
        paths.extend(compiled)

    for entry in tests:
        paths.extend(_expand_entry(entry))

    return list(dict.fromkeys(paths))  # unique, order-preserving


def _totals_from_results(results: list[run_manager.FeatureResult], merged: dict) -> dict:
    """Testcase-level totals: merged XML counts plus runs that produced no junit.

    Results without a junit XML (crashed/timeout subprocesses) are not
    represented in the merged file and are added here so failures never vanish.
    """
    totals = {key: int(merged.get(key, 0) or 0) for key in ("total", "passed", "failed", "errors")}
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


def append_suite_state(workroot: str, suite: str, entry: dict) -> None:
    """Append one JSON record for ``suite`` to the suite state file.

    The state file (``{workroot}/{SUITE_STATE_FILENAME}``) is append-only JSONL:
    one JSON object per line, so sequential/concurrent suite completions are
    collected by appending and readers can tail the file. The record is
    ``{"suite": suite, **entry}``. Each append holds an exclusive
    ``fcntl.flock`` on POSIX (plain append elsewhere) so concurrent writers
    never interleave lines. Best-effort telemetry: I/O failures are logged and
    swallowed, never raised into the suite run.
    """
    line = json.dumps({"suite": suite, **entry}, default=str) + "\n"
    try:
        os.makedirs(workroot, exist_ok=True)
        with open(os.path.join(workroot, SUITE_STATE_FILENAME), "a", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(line)
                handle.flush()
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        logger.warning("Suite '%s': could not append suite state to %s: %s", suite, os.path.join(workroot, SUITE_STATE_FILENAME), exc)


async def _execute_features(
    name: str, features: list[str], workroot: str, max_parallel: int, retries_allowed: int, env_extra: dict[str, str], timeout_s: float, split_offset: int = 0
) -> tuple[list[run_manager.FeatureResult], list[dict], list[int], int, list[str]]:
    """Run ``features`` (sequential or split-parallel) with per-scenario retries.

    Returns ``(results, meta, retry_counts, retries_used, result_features)``
    where ``result_features[i]`` is the original feature path behind
    ``results[i]`` (the parallel path runs per-scenario split files, so the
    mapping is not 1:1 there). ``split_offset`` keeps the per-feature
    ``split/feature_NNN`` directories unique when one suite run executes
    several phases (dependency features, then its own features).
    """
    retries_used = 0
    results: list[run_manager.FeatureResult] = []
    meta: list[dict] = []  # {"feature": ...} aligned with results
    retry_counts: list[int] = []  # extra attempts per result, aligned with results
    result_features: list[str] = []

    if max_parallel > 1:
        items: list[dict] = []
        for index, feature_path in enumerate(features):
            split_dir = os.path.join(workroot, "split", f"feature_{split_offset + index:03d}")
            split_items = await run_manager.split_feature_file(feature_path, split_dir)
            items.extend(split_items)
            result_features.extend([feature_path] * len(split_items))
        if not items:
            raise ValueError(f"Suite '{name}': no scenarios found in any feature file.")
        results = list(await run_manager.run_scenarios_parallel(items, max_parallel=max_parallel, workroot=workroot, timeout_s=timeout_s, env_extra=env_extra or None))
        meta = [{"feature": item["feature"]} for item in items]
        retry_counts = [0] * len(items)

        retry_round = 0
        while retry_round < retries_allowed:
            failed_positions = [index for index, result in enumerate(results) if not result.ok]
            if not failed_positions:
                break
            retries_used += len(failed_positions)
            logger.info("Suite '%s': retry round %d of %d re-running %d failed scenario(s)", name, retry_round + 1, retries_allowed, len(failed_positions))
            retry_items = [items[index] for index in failed_positions]
            retry_results = await run_manager.run_scenarios_parallel(retry_items, max_parallel=max_parallel, workroot=workroot, timeout_s=timeout_s, env_extra=env_extra or None)
            for position, index in enumerate(failed_positions):
                results[index] = retry_results[position]
                retry_counts[index] += 1
            retry_round += 1
    else:
        for feature_path in features:
            result = await run_manager.run_feature_isolated(feature_path, workroot, env_extra=env_extra or None, timeout_s=timeout_s)
            attempts = 0
            while not result.ok and attempts < retries_allowed:
                attempts += 1
                retries_used += 1
                logger.info("Suite '%s': retrying %s (attempt %d of %d)", name, os.path.basename(feature_path), attempts + 1, retries_allowed + 1)
                result = await run_manager.run_feature_isolated(feature_path, workroot, env_extra=env_extra or None, timeout_s=timeout_s)
            results.append(result)
            meta.append({"feature": os.path.splitext(os.path.basename(feature_path))[0]})
            retry_counts.append(attempts)
            result_features.append(feature_path)

    return results, meta, retry_counts, retries_used, result_features


def _warn_junit_result_disagreements(results: list[run_manager.FeatureResult]) -> None:
    """Warn when a run's junit verdict and subprocess ``ok`` flag disagree.

    Totals are junit-derived, so a junit that says "passed" while the
    subprocess failed (or the inverse) would otherwise silently steer the
    numbers; surface both signals per scenario. Best-effort: results without a
    readable junit XML are skipped (junitless runs are folded into totals).
    """
    for result in results:
        if not result.junit_xml or not os.path.isfile(result.junit_xml):
            continue
        try:
            cases = parse_junit_testcases(result.junit_xml)
        except Exception as exc:  # noqa: BLE001 - diagnostics must never break the run
            logger.warning("Could not parse junit XML %s for scenario '%s': %s", result.junit_xml, result.scenario, exc)
            continue
        if not cases:
            continue
        junit_ok = all(case.get("status") == "passed" for case in cases)
        if junit_ok != result.ok:
            logger.warning(
                "Scenario '%s' (%s): junit and subprocess result disagree (junit=%s, ok=%s, exit_code=%s); totals follow junit",
                result.scenario,
                result.feature_path,
                "passed" if junit_ok else "failed",
                result.ok,
                result.exit_code,
            )


async def run_suite(
    name: str,
    path: str = DEFAULT_SUITES_PATH,
    max_parallel_override: int | None = None,
    out_dir: str | None = None,
    timeout_s: float = run_manager.DEFAULT_TIMEOUT_S,
    dependency_results: dict[str, bool] | None = None,
) -> dict:
    """Resolve and run one suite; aggregate per-scenario results with retries.

    Returns ``{"suite", "total", "passed", "failed", "errors", "retries_used",
    "per_scenario", "merged_xml", "workroot", "config"}``. ``per_scenario``
    entries carry ``{scenario, feature, ok, retries, duration_s, junit_xml,
    error, workspace}``. Totals are testcase-level (merged XML) with runs that
    produced no junit XML folded in.

    Dependency features (``depends_on``) execute first. When any of them fails
    (after its own retry rounds) and ``skip_on_dependency_failure`` is on (the
    default, matching the documented semantics), the suite's own features are
    NOT executed: they are recorded in ``per_scenario`` with ``ok=False`` and
    the error ``skipped: dependency '<name>' failed``, counted as errors, and
    the CLI exits 1.

    ``dependency_results`` (an optional ``{feature_path: ok}`` map, mutated in
    place) lets a caller such as :func:`scheduler.run_monitor_once` gate on
    dependency features that already ran earlier in the cycle instead of
    re-running them: entries found in the map are used as-is, missing ones are
    executed here (so a dependency outside the cycle still gates), and every
    feature this suite executes (or skips) is written back into the map for
    later suites. Without the map (plain ``hercules-suite`` invocation) all
    dependency features are folded into this run, as documented. Plan-compiled
    features get a fresh temporary path per call, so plan-based dependencies
    are re-executed (and still gate) rather than deduped across calls.

    On completion a path-free summary (totals plus retry count) is appended to
    the suite's append-only JSONL state file via :func:`append_suite_state`.
    """
    suites = load_suites(path)
    if name not in suites:
        raise ValueError(f"Unknown suite '{name}'. Known suites: {', '.join(sorted(suites)) or '(none)'}.")
    cfg = suites[name]

    compiled_dirs: list[str] = []  # tempdirs of compiled test plans, removed in finally
    try:
        memo: dict[str, list[str]] = {}  # compiled plan features per suite, shared by one resolve
        own_features = resolve_suite(name, suites, compiled_dirs=compiled_dirs, include_dependencies=False, _memo=memo)
        dep_paths: list[str] = []  # ordered, deduped feature paths of every depends_on suite
        dep_owner: dict[str, str] = {}  # feature path -> dependency suite name (first dep wins)
        for dep in cfg.get("depends_on") or []:
            for feature_path in resolve_suite(dep, suites, compiled_dirs=compiled_dirs, _memo=memo, _root=name):
                dep_owner.setdefault(feature_path, dep)
                if feature_path not in dep_paths:
                    dep_paths.append(feature_path)

        features = dep_paths + [feature_path for feature_path in own_features if feature_path not in dep_owner]
        if not features:
            raise ValueError(f"Suite '{name}' resolved to zero feature files.")

        skip_on_dependency_failure = bool(cfg.get("skip_on_dependency_failure", True))
        preknown: dict[str, bool] = {}  # dep feature results already available from earlier suites this cycle
        if dependency_results is not None:
            preknown = {feature_path: bool(dependency_results[feature_path]) for feature_path in dep_paths if feature_path in dependency_results}
        # Plain invocation: fold every dependency feature into this run.
        # Monitor cycle: reuse results already in the map; only dependencies
        # absent from it still run here so gating stays honest. With gating
        # disabled, monitor cycles execute this suite's own features only.
        dep_to_run = dep_paths if dependency_results is None else ([feature_path for feature_path in dep_paths if feature_path not in preknown] if skip_on_dependency_failure else [])
        # Dependency features executed HERE run under this suite's retry/env/
        # max_parallel, so a differing dependency config is genuinely ignored;
        # when the monitor already ran a dependency with its own config, it is
        # not, and no warning is due.
        for dep in dict.fromkeys(dep_owner[feature_path] for feature_path in dep_to_run):
            if dep in suites:
                _warn_ignored_dep_config(dep, name, suites)

        max_parallel = max(1, int(max_parallel_override if max_parallel_override is not None else cfg.get("max_parallel", 1)))
        retries_allowed = int(cfg.get("retry", 0))
        env_extra = dict(cfg.get("env") or {})

        # uuid suffix: two in-process runs of the same suite within one second
        # must not share a workroot.
        workroot = os.path.join(os.getcwd(), ".hercules", "suite_runs", f"{_sanitize_name(name)}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}")
        os.makedirs(workroot, exist_ok=True)
        out_dir = out_dir or os.path.join(workroot, "merged")

        if cfg.get("shared_state"):
            if SHARED_STATE_ENV_VAR in env_extra:
                logger.warning(
                    "Suite '%s': env %s=%r is overridden by the injected shared-state file for this suite; the injected value wins.", name, SHARED_STATE_ENV_VAR, env_extra[SHARED_STATE_ENV_VAR]
                )
            env_extra[SHARED_STATE_ENV_VAR] = os.path.join(workroot, SUITE_STATE_FILENAME)
            logger.info("Suite '%s': shared state file at %s", name, env_extra[SHARED_STATE_ENV_VAR])

        logger.info("Running suite '%s': %d feature file(s), max_parallel=%d, retry=%d", name, len(features), max_parallel, retries_allowed)

        results: list[run_manager.FeatureResult] = []
        meta: list[dict] = []
        retry_counts: list[int] = []
        retries_used = 0
        result_features: list[str] = []
        split_offset = 0

        dep_result_features: list[str] = []
        if dep_to_run:
            dep_results, dep_meta, dep_retry_counts, dep_retries, dep_result_features = await _execute_features(
                name, dep_to_run, workroot, max_parallel, retries_allowed, env_extra, timeout_s, split_offset
            )
            split_offset += len(dep_to_run)
            results.extend(dep_results)
            meta.extend(dep_meta)
            retry_counts.extend(dep_retry_counts)
            retries_used += dep_retries
            result_features.extend(dep_result_features)

        dep_ok: dict[str, bool] = {}
        for feature_path, result in zip(dep_result_features, results):
            dep_ok[feature_path] = dep_ok.get(feature_path, True) and result.ok
        failed_dep_paths = {feature_path for feature_path, ok in dep_ok.items() if not ok} | {feature_path for feature_path, ok in preknown.items() if not ok}

        own_to_run = [feature_path for feature_path in own_features if feature_path not in dep_owner]
        gate = skip_on_dependency_failure and bool(failed_dep_paths)
        if gate:
            first_failed = next(feature_path for feature_path in dep_paths if feature_path in failed_dep_paths)
            gate_dep = dep_owner.get(first_failed, first_failed)
            logger.warning("Suite '%s': skipping %d own feature(s); dependency '%s' failed", name, len(own_to_run), gate_dep)
            for feature_path in own_to_run:
                scenario = os.path.splitext(os.path.basename(feature_path))[0]
                results.append(
                    run_manager.FeatureResult(
                        scenario=scenario,
                        feature_path=feature_path,
                        workspace="",
                        ok=False,
                        exit_code=None,
                        duration_s=0.0,
                        junit_xml=None,
                        stdout_tail="",
                        error=f"skipped: dependency '{gate_dep}' failed",
                    )
                )
                meta.append({"feature": scenario})
                retry_counts.append(0)
                result_features.append(feature_path)
        elif own_to_run:
            own_results, own_meta, own_retry_counts, own_retries, own_result_features = await _execute_features(
                name, own_to_run, workroot, max_parallel, retries_allowed, env_extra, timeout_s, split_offset
            )
            results.extend(own_results)
            meta.extend(own_meta)
            retry_counts.extend(own_retry_counts)
            retries_used += own_retries
            result_features.extend(own_result_features)

        if dependency_results is not None:
            for feature_path, result in zip(result_features, results):
                dependency_results[feature_path] = bool(result.ok)

        merged = await run_manager.merge_results_async(results, out_dir)
        totals = _totals_from_results(results, merged)
        _warn_junit_result_disagreements(results)
        run_manager.cleanup_successful_workspaces(results)
        # The removed workspaces (and the junit XMLs inside them) must not be
        # surfaced as dangling paths; failures keep theirs, merged_xml lives
        # outside the workspaces and is untouched.
        run_manager.redact_cleaned_workspace_paths(results)

        per_scenario = []
        for result, info, retries in zip(results, meta, retry_counts):
            per_scenario.append(
                {
                    "scenario": result.scenario,
                    "feature": info["feature"],
                    "ok": result.ok,
                    "retries": retries,
                    "duration_s": result.duration_s,
                    "junit_xml": result.junit_xml,
                    "error": result.error,
                    "workspace": result.workspace,
                }
            )

        summary = {
            "suite": name,
            **totals,
            "retries_used": retries_used,
            "per_scenario": per_scenario,
            "merged_xml": merged["junit_xml"],
            "workroot": workroot,
            "config": os.path.abspath(path),
        }
        logger.info("Suite '%s' finished: %d total, %d passed, %d failed, %d errors, %d retries", name, totals["total"], totals["passed"], totals["failed"], totals["errors"], retries_used)
        # The summary minus its path-bearing entries (per_scenario, merged_xml,
        # workroot, config) is appended to the suite state JSONL.
        append_suite_state(workroot, name, {key: summary[key] for key in ("suite", "total", "passed", "failed", "errors", "retries_used")})
        return summary
    finally:
        cleanup_compiled_plans(compiled_dirs)


def _exit_code_for(summary: dict) -> int:
    """1 when the suite had failures/errors; 0 when EXIT_CODE_COMPAT=1 is set."""
    if os.getenv("EXIT_CODE_COMPAT", "") == "1":
        return 0
    return 1 if (summary.get("failed", 0) or 0) > 0 or (summary.get("errors", 0) or 0) > 0 else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry (``hercules-suite``): run one named suite and report."""
    parser = argparse.ArgumentParser(prog="hercules-suite", description="Run a named test suite from .hercules/suites.yaml.")
    parser.add_argument("suite_name", help="Name of the suite to run.")
    parser.add_argument("--config", default=DEFAULT_SUITES_PATH, help=f"Path to the suites YAML (default: {DEFAULT_SUITES_PATH}).")
    parser.add_argument("--max-parallel", type=int, default=None, help="Override the suite's max_parallel (default: suite value, itself 1).")
    parser.add_argument("--out-dir", default=None, help="Directory for the merged junit XML (default: <workroot>/merged).")
    args = parser.parse_args(argv)

    summary = asyncio.run(run_suite(args.suite_name, path=args.config, max_parallel_override=args.max_parallel, out_dir=args.out_dir))

    for entry in summary.get("per_scenario", []):
        error_text = str(entry.get("error") or "")
        if entry["ok"]:
            status = "ok"
        elif error_text.startswith("skipped:"):
            status = "SKIPPED"  # dependency gating: the feature never executed
        elif error_text and "Timed out" in error_text:
            status = "error"
        else:
            status = "FAILED"
        suffix = f" :: {entry['error']}" if entry["error"] else ""
        print(f"[{status}] {entry['scenario']} ({entry['duration_s']:.1f}s){suffix}")

    print(f"Suite '{summary['suite']}' totals: {summary['total']} total, {summary['passed']} passed, {summary['failed']} failed, {summary['errors']} errors, {summary['retries_used']} retries")
    if summary.get("merged_xml"):
        print(f"Merged junit XML: {summary['merged_xml']}")
    return _exit_code_for(summary)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
