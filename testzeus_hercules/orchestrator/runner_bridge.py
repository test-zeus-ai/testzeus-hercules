"""In-process execution bridge between the MCP server and the Hercules runner.

Instead of shelling out to ``python -m testzeus_hercules``, this module calls
``testzeus_hercules.__main__.sequential_process`` directly (lazy import: that
module pulls in the full automation stack). Progress is observed through the
process-wide notification hub; the executor node in ``simple_hercules.py``
emits a ``step_completed`` event after every agent step.
"""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from testzeus_hercules.config import set_global_conf
from testzeus_hercules.core.notification_manager import get_notification_hub
from testzeus_hercules.intelligence.coverage import filter_plan_by_test_ids
from testzeus_hercules.intelligence.test_plan import TestPlan, compile_plan_to_gherkin
from testzeus_hercules.utils.logger import logger

MAX_PROGRESS_EVENTS = 500


@dataclass
class ExecutionOutcome:
    """Result of one in-process feature-file execution.

    ``progress_events`` is bounded at ``MAX_PROGRESS_EVENTS`` (newest kept);
    ``steps_completed`` is the authoritative count of ``step_completed`` events
    for the whole run, independent of that cap.
    """

    summaries: list[dict[str, Any]] = field(default_factory=list)
    success: bool = False
    duration_s: float = 0.0
    progress_events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    steps_completed: int = 0


def _make_listener(
    events: deque[dict[str, Any]],
    step_counter: list[int],
    progress_sink: Callable[[dict], None] | None,
) -> Callable[[dict], None]:
    """Build a hub listener that bounds buffered events at MAX_PROGRESS_EVENTS, counts steps and optionally sinks them."""

    def _listener(notification: dict) -> None:
        # deque(maxlen=...) keeps the NEWEST events once the cap is reached; the
        # step counter keeps counting so long runs are not underreported.
        events.append(dict(notification))
        if notification.get("type") == "step_completed":
            step_counter[0] += 1
        if progress_sink is not None:
            try:
                progress_sink(dict(notification))
            except Exception as exc:  # a broken sink must never break a run
                logger.warning("progress_sink raised; ignoring: %s", exc)

    return _listener


async def execute_feature_file(feature_path: str, progress_sink: Callable[[dict], None] | None = None) -> ExecutionOutcome:
    """Run one Gherkin feature file in-process via ``sequential_process``.

    Registers a listener on the notification hub for the duration of the run,
    points the global config at ``feature_path`` and returns an
    :class:`ExecutionOutcome`. Never raises for run failures: those are
    reported through ``ExecutionOutcome.error``. A ``SystemExit(1)`` (the
    no-scenarios configuration error) is converted into a failed outcome with
    a descriptive message; any other ``SystemExit`` is re-raised.
    """
    events: deque[dict[str, Any]] = deque(maxlen=MAX_PROGRESS_EVENTS)
    step_counter = [0]
    listener = _make_listener(events, step_counter, progress_sink)
    hub = get_notification_hub()
    hub.register_listener(listener)

    summary: dict[str, Any] | None = None
    error: str | None = None
    start = time.monotonic()
    try:
        set_global_conf({"INPUT_GHERKIN_FILE_PATH": os.path.abspath(feature_path)}, override=True)
        from testzeus_hercules.__main__ import sequential_process  # lazy: heavy import

        try:
            summary = await sequential_process()
        except SystemExit as exc:
            code = getattr(exc, "code", None)
            if code in (1, "1"):
                error = (
                    f"Execution aborted with exit code 1: no scenarios could be run from feature file "
                    f"{os.path.basename(feature_path)}. Ensure the file exists and contains at least one 'Scenario:' block."
                )
            else:
                raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            hub.unregister_listener(listener)
        except ValueError:
            pass

    duration = time.monotonic() - start
    progress_events = list(events)

    if summary is None:
        return ExecutionOutcome(
            summaries=[],
            success=False,
            duration_s=duration,
            progress_events=progress_events,
            error=error or "execution failed",
            steps_completed=step_counter[0],
        )

    success = int(summary.get("failed", 0) or 0) == 0 and int(summary.get("errors", 0) or 0) == 0
    return ExecutionOutcome(
        summaries=[summary],
        success=success,
        duration_s=duration,
        progress_events=progress_events,
        error=None,
        steps_completed=step_counter[0],
    )


def write_plan_scenarios_feature(plan: TestPlan, test_ids: list[str] | None, out_path: str) -> str:
    """Compile a test plan (optionally restricted to ``test_ids``) into a Gherkin feature file.

    Args:
        plan: The :class:`TestPlan` to compile.
        test_ids: When given, only these test case ids are included.
        out_path: Where to write the ``.feature`` file.

    Returns:
        The absolute path of the written feature file.
    """
    if test_ids:
        plan = filter_plan_by_test_ids(plan, test_ids)
    gherkin = compile_plan_to_gherkin(plan)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(gherkin)
    return os.path.abspath(out_path)


def describe_run(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate run summaries into totals plus junit/report/bundle paths."""
    totals: dict[str, int] = {"total": 0, "passed": 0, "failed": 0, "errors": 0}
    junit_xml: str | None = None
    report_md: str | None = None
    bundles: list[str] = []
    feature_files: list[str] = []
    for summary in summaries or []:
        for key in totals:
            totals[key] += int(summary.get(key, 0) or 0)
        if summary.get("junit_xml"):
            junit_xml = summary["junit_xml"]
        if summary.get("report_md"):
            report_md = summary["report_md"]
        bundles.extend(summary.get("bundles") or [])
        if summary.get("feature_file"):
            feature_files.append(summary["feature_file"])
    return {
        **totals,
        "junit_xml": junit_xml,
        "report_md": report_md,
        "bundles": bundles,
        "feature_files": feature_files,
    }
