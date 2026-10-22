import asyncio
import contextlib
import json
import os
import uuid

from junit2htmlreport.runner import run as prepare_html
from testzeus_hercules.config import (
    explicit_config_overrides,
    get_global_conf,
    set_global_conf,
)
from testzeus_hercules.core.runner import SingleCommandInputRunner
from testzeus_hercules.reporting.bundle import (
    UNCLASSIFIED_TEXT,
    create_failure_bundles,
    parse_junit_testcases,
    resolve_bundle_dir_names,
)
from testzeus_hercules.reporting.markdown_report import (
    REPORT_FILE_NAME,
    generate_markdown_report,
)
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.env_flags import (
    failure_bundles_enabled,
    failure_classification_enabled,
    run_history_enabled,
    run_reporting_enabled,
    strict_exit_code_enabled,
)
from testzeus_hercules.utils.gherkin_generator import (
    generate_gherkin_from_description,
    print_feature_block,
)
from testzeus_hercules.utils.gherkin_helper import (
    process_feature_file,
    serialize_feature_file,
)
from testzeus_hercules.utils.junit_helper import (
    JUnitXMLGenerator,
    build_fallback_junit_xml,
    build_junit_xml,
)
from testzeus_hercules.utils.litellm_helper import get_litellm_chat_model
from testzeus_hercules.utils.llm_helper import parse_agent_response
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.test_builder import run_guided_mode


async def _shutdown_runner_best_effort(runner: SingleCommandInputRunner) -> None:
    """Tear a crashed/cancelled runner down; never raises (CancelledError of the caller aside)."""
    runner_shutdown = getattr(runner, "shutdown", None)
    if callable(runner_shutdown):
        with contextlib.suppress(Exception):
            await runner_shutdown()


async def sequential_process() -> dict:
    """Run every scenario in the configured feature file and build the result summary.

    Produces the merged JUnit XML and the HTML conversion (best effort,
    ``summary["html"]`` is ``None`` on failure). The following extra outputs are
    opt-in (off by default, each best effort: a failure logs a warning and never
    fails the run itself):
    - ``ENABLE_RUN_REPORTING``: markdown report (``summary["report_md"]``);
    - ``ENABLE_FAILURE_BUNDLES``: per-failure evidence bundles (``summary["bundles"]``);
    - ``ENABLE_RUN_HISTORY``: the run and its cases are recorded in the run-history DB
      (``summary["run_id"]``);
    - ``ENABLE_FAILURE_CLASSIFICATION``: every failed/errored testcase is classified
      (``summary["classifications"]``), its bundle.md gets the classification
      section and a "classification" healing event is recorded in the run history.
    """
    dont_close_browser = get_global_conf().get_dont_close_browser()

    # The run timestamp has second resolution and is fixed at import time, so two
    # runs sharing a project root within the same second would overwrite each
    # other's evidence and inherit stale failure bundles. Keep the plain
    # timestamp name for the normal (fresh) case; when the dir already holds
    # artifacts from another run, disambiguate with a short uuid suffix.
    junit_base_path = get_global_conf().get_junit_xml_base_path()
    if os.path.isdir(junit_base_path) and os.listdir(junit_base_path):
        junit_base_path = f"{junit_base_path}_{uuid.uuid4().hex[:8]}"
        os.makedirs(junit_base_path, exist_ok=True)

    list_of_feats = await process_feature_file(dont_append_header=dont_close_browser)
    input_gherkin_file_path = get_global_conf().get_input_gherkin_file_path()
    feature_file_name = os.path.basename(input_gherkin_file_path)

    result_of_tests = []
    add_event(EventType.RUN, EventData(detail="Total Runs: " + str(len(list_of_feats))))

    if not list_of_feats:
        logger.error(
            "No scenarios found in feature file: %s. " "Ensure the file contains at least one 'Scenario:' block.",
            input_gherkin_file_path,
        )
        raise SystemExit(1)

    for feat in list_of_feats:
        file_path = feat["output_file"]
        feature_name = feat["feature"]
        scenario = feat["scenario"]
        stake_id = scenario.replace(" ", "_").replace(":", "_").replace("/", "_").replace("\\", "_").replace(".", "_")

        get_global_conf().set_default_test_id(stake_id)

        cmd = await serialize_feature_file(file_path)
        logger.info(f"Running testcase: {stake_id}")
        logger.info(f"testcase details: {cmd}")

        runner = SingleCommandInputRunner(
            stake_id=stake_id,
            command=cmd,
            dont_terminate_browser_after_run=dont_close_browser,
        )

        runner_result: dict = {}
        cost_metrics: dict = {}
        execution_time: float = 0.0
        cancelled_exc: asyncio.CancelledError | None = None

        try:
            await runner.start()

            if runner.result and getattr(runner.result, "cost", None):
                cost_metrics = runner.result.cost
            elif get_global_conf().get_token_verbose():
                logger.warning("Token verbose enabled, but LangGraph result did not include cost metrics.")

            execution_time = runner.execution_time

            if runner.result:
                summary = runner.result.summary
                if summary:
                    runner_result = parse_agent_response(summary)
                    if not runner_result:
                        logger.warning("Could not parse planner result from test output; marking as incomplete.")
                elif getattr(runner.result, "terminate", "no") == "yes":
                    runner_result = {
                        "terminate": "yes",
                        "is_passed": False,
                        "final_response": "Test ended without planner output.",
                    }
                else:
                    # runner.result exists but produced neither a summary nor a clean
                    # termination signal — this is an unexpected runner state, not a
                    # controlled failure. Mark it failed explicitly so it surfaces in
                    # CI rather than drifting through as an empty result.
                    logger.error(
                        "runner.result present but has no summary and did not terminate " "cleanly for scenario: %s. Marking as failed.",
                        scenario,
                    )
                    runner_result = {
                        "terminate": "yes",
                        "is_passed": False,
                        "final_response": "Unexpected runner state: no summary, no clean termination.",
                    }
            else:
                # runner.start() completed but produced no result object — record an
                # explicit failure instead of letting an empty entry through.
                logger.error("Runner produced no result for scenario: %s. Marking as failed.", scenario)
                runner_result = {
                    "terminate": "yes",
                    "is_assert": True,
                    "is_passed": False,
                    "final_response": "Runner produced no result for this scenario.",
                    "assert_summary": "EXPECTED RESULT: the runner produces a planner result.\nACTUAL RESULT: no result was produced.",
                }

            logger.info(f"Run completed for testcase: {scenario}")
            if cost_metrics:
                logger.info(f"Test run cost is : {cost_metrics}")
        except asyncio.CancelledError as exc:
            # Cancellation (Ctrl-C, asyncio.wait_for / asyncio.timeout around the run)
            # must stop the whole run, not just this scenario: record the scenario as
            # cancelled, tear its sessions down, then re-raise after its junit entry
            # has been written (see below).
            cancelled_exc = exc
            logger.warning("Scenario %s was cancelled; cleaning up and aborting the run.", scenario)
            runner_result = {
                "terminate": "yes",
                "is_assert": True,
                "is_passed": False,
                "final_response": "Scenario cancelled before completion.",
                "assert_summary": "EXPECTED RESULT: the scenario runs to completion.\nACTUAL RESULT: it was cancelled.",
            }
            await _shutdown_runner_best_effort(runner)
        except Exception as exc:
            # One scenario's crash must not abort the run (which would silently
            # skip remaining scenarios and the merged reports): record it as a
            # failed junit entry and continue with the next scenario.
            logger.error(f"Scenario {scenario} crashed with an unexpected error: {type(exc).__name__}: {exc}", exc_info=True)
            crash_summary = f"Scenario crashed with an unexpected error: {type(exc).__name__}: {exc}"
            runner_result = {
                "terminate": "yes",
                "is_assert": True,
                "is_passed": False,
                "final_response": crash_summary,
                "assert_summary": (f"EXPECTED RESULT: the scenario runs to completion.\nACTUAL RESULT: it aborted with {type(exc).__name__}: {exc}"),
            }
            # The crashed scenario must not leak its browser/agent sessions into
            # the next scenario of the batch: tear the runner down best-effort
            # (its own cleanup inside start() may already have run).
            await _shutdown_runner_best_effort(runner)

        # The runner may never have initialized its browser manager (crash during
        # start()); fall back to loop-local values so the junit entry is still built.
        browser_manager = runner.browser_manager
        try:
            result_of_tests.append(
                await build_junit_xml(
                    runner_result,
                    execution_time,
                    cost_metrics,
                    feature_name,
                    scenario,
                    feature_file_path=file_path,
                    output_file_path="",
                    proofs_path=get_global_conf().get_proof_path(getattr(browser_manager, "stake_id", None) or stake_id),
                    proofs_screenshot_path=getattr(browser_manager, "_screenshots_dir", None),
                    proofs_video_path=browser_manager.get_latest_video_path() if browser_manager else None,
                    network_logs_path=getattr(browser_manager, "request_response_log_file", None),
                    logs_path=get_global_conf().get_source_log_folder_path(stake_id),
                    planner_thoughts_path=get_global_conf().get_source_log_folder_path(stake_id) + "/agent_inner_thoughts.json",
                )
            )
        except Exception as exc:
            # A junit-generation error for one scenario (e.g. a control character
            # surviving in an LLM response) must not kill the batch with zero
            # junit output: synthesize a minimal red testcase entry so the run is
            # still recorded — and failed — in the merged report.
            logger.error("Could not build junit entry for scenario %s: %s: %s", scenario, type(exc).__name__, exc, exc_info=True)
            try:
                result_of_tests.append(await build_fallback_junit_xml(feature_name, scenario, exc))
            except Exception as fallback_exc:
                # Even the fallback can raise (e.g. ENOSPC); the batch must not
                # die with zero merged output. This scenario simply has no
                # per-file report, while the loop, the other scenarios' reports
                # and the in-memory run summary are unaffected.
                logger.error("Could not build fallback junit entry for scenario %s: %s: %s", scenario, type(fallback_exc).__name__, fallback_exc, exc_info=True)

        if cancelled_exc is not None:
            raise cancelled_exc

    final_result_file_name = f"{junit_base_path}/{feature_file_name}_result.xml"
    try:
        await JUnitXMLGenerator.merge_junit_xml(result_of_tests, final_result_file_name)
    except Exception as exc:
        # Everything downstream (html/markdown reports, testcase parsing,
        # failure bundles, the run summary) reads the merged file, so a failed
        # merge cannot be skipped: name the target file and re-raise.
        logger.error("Failed to write merged junit results to %s: %s", final_result_file_name, exc)
        raise
    logger.info(f"Results published in junitxml file: {final_result_file_name}")

    final_result_html_file_name = f"{junit_base_path}/{feature_file_name}_result.html"
    html_path: str | None = final_result_html_file_name
    try:
        await asyncio.to_thread(prepare_html, [final_result_file_name, final_result_html_file_name])
        logger.info(f"Results published in html file: {final_result_html_file_name}")
    except Exception as exc:
        # junit2html is a nice-to-have; never let it crash the run after the
        # tests themselves passed (e.g. PermissionError / ENOSPC writing the html).
        logger.warning("Could not generate HTML report for %s: %s", feature_file_name, exc)
        html_path = None

    testcases = await asyncio.to_thread(parse_junit_testcases, final_result_file_name)
    passed = sum(1 for case in testcases if case["status"] == "passed")
    failed = sum(1 for case in testcases if case["status"] == "failed")
    errors = sum(1 for case in testcases if case["status"] == "error")

    report_md_path: str | None = None
    if run_reporting_enabled():
        try:
            await asyncio.to_thread(generate_markdown_report, final_result_file_name, run_meta={"feature_file": input_gherkin_file_path})
            report_md_path = os.path.join(os.path.dirname(os.path.abspath(final_result_file_name)), REPORT_FILE_NAME)
        except Exception as exc:
            logger.warning("Could not generate markdown report for %s: %s", feature_file_name, exc)

    bundles: list[str] = []
    if failure_bundles_enabled():
        try:
            # create_failure_bundles is declared async but does only blocking I/O,
            # so drive it on a fresh loop inside the worker thread.
            bundles = await asyncio.to_thread(asyncio.run, create_failure_bundles(final_result_file_name))
        except Exception as exc:
            logger.warning("Could not create failure bundles for %s: %s", feature_file_name, exc)

    summary = {
        "total": len(testcases),
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "junit_xml": final_result_file_name,
        "html": html_path,
        "report_md": report_md_path,
        "bundles": bundles,
        "feature_file": input_gherkin_file_path,
    }

    # Run history (opt-in, ENABLE_RUN_HISTORY): persist the run and its testcases
    # into the run-history DB so trends and healing events can be tracked across
    # runs (best effort).
    if run_history_enabled():
        try:
            from testzeus_hercules.reporting.run_db import (
                record_run_summary,  # lazy: keeps the DB import out of the hot path
            )

            summary["run_id"] = await asyncio.to_thread(record_run_summary, summary)
        except Exception as exc:
            logger.warning("Could not record run history for %s: %s", feature_file_name, exc)

    # Auto-classify every failed/errored testcase (opt-in, ENABLE_FAILURE_CLASSIFICATION),
    # annotate its bundle and record a classification healing event in the run
    # history (best effort).
    if failure_classification_enabled():
        summary["classifications"] = await _classify_failed_cases(testcases, final_result_file_name, summary)

    return summary


CLASSIFICATION_CATEGORIES = ("product_bug", "test_fragility", "environment", "contract_violation", "unclassified")


async def _classify_failed_cases(testcases: list[dict], junit_xml_path: str, summary: dict) -> dict[str, int]:
    """Classify failed/errored testcases, annotate their bundles and record healing events.

    Best effort throughout: import, classification, bundle annotation and DB writes
    are individually guarded, and any failure degrades to an "unclassified" count
    instead of raising, so reporting never breaks the test run itself.
    """
    categories: dict[str, int] = {category: 0 for category in CLASSIFICATION_CATEGORIES}
    try:
        from testzeus_hercules.healing.failure_classifier import (
            classify_failure_with_context,
            format_classification_markdown,
        )
        from testzeus_hercules.reporting.run_db import get_default_history
    except Exception as exc:
        logger.warning("Could not import classification/run-history modules: %s", exc)
        return categories

    run_id = summary.get("run_id")
    junit_dir = os.path.dirname(os.path.abspath(junit_xml_path))
    # Re-derive the same collision-free bundle dir mapping create_failure_bundles
    # used (same testcase order => same mapping), so sanitized-name collisions
    # ("a/b" vs "a_b") still annotate the correct bundle.
    bundle_dir_names = resolve_bundle_dir_names([case.get("name") or "unknown_testcase" for case in testcases if case.get("status") != "passed"])
    for case in testcases:
        if case.get("status") == "passed":
            continue
        case_name = case.get("name") or "unknown_testcase"
        try:
            classification = await classify_failure_with_context(case)
        except Exception as exc:
            logger.warning("Could not classify failure for testcase %s: %s", case_name, exc)
            categories["unclassified"] += 1
            continue

        categories[classification.category] = categories.get(classification.category, 0) + 1

        # Annotate the failure bundle (when one was created) in place of the
        # "Unclassified" placeholder written by bundle.py.
        bundle_md = os.path.join(junit_dir, "bundles", bundle_dir_names[case_name], "bundle.md")
        if os.path.isfile(bundle_md):
            _write_classification_into_bundle(bundle_md, format_classification_markdown(classification))

        if run_id:
            try:
                get_default_history().record_healing_event(
                    run_id,
                    case_name,
                    kind="classification",
                    detail=f"{classification.category} ({classification.confidence:.2f}): {classification.rationale}",
                    status=classification.category,
                )
            except Exception as exc:
                logger.warning("Could not record classification healing event for testcase %s: %s", case_name, exc)

    return categories


def _write_classification_into_bundle(bundle_md: str, classification_markdown: str) -> None:
    """Write the classification section into a bundle.md, replacing the placeholder (best effort).

    Writes to a temp file and ``os.replace()``s it into place so a crash
    mid-write cannot leave a truncated bundle.md behind.
    """
    tmp_path = f"{bundle_md}.{os.getpid()}.tmp"
    try:
        with open(bundle_md, "r", encoding="utf-8") as md_file:
            content = md_file.read()
        if UNCLASSIFIED_TEXT in content:
            content = content.replace(UNCLASSIFIED_TEXT, classification_markdown)
        else:
            content = content.rstrip("\n") + "\n\n## Classification\n\n" + classification_markdown + "\n"
        with open(tmp_path, "w", encoding="utf-8") as md_file:
            md_file.write(content)
            md_file.flush()
            os.fsync(md_file.fileno())
        os.replace(tmp_path, bundle_md)
    except Exception as exc:
        logger.warning("Could not write classification into %s: %s", bundle_md, exc)
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)


def compute_exit_code(run_summaries: list[dict]) -> int:
    """Map run summaries to the strict exit code: 1 when any run failed, else 0.

    Only applied to the process exit status when ``STRICT_EXIT_CODE=true``.
    """
    for summary in run_summaries or []:
        if summary.get("failed", 0) > 0 or summary.get("errors", 0) > 0:
            return 1
    return 0


def _print_banner() -> None:
    print(r"""
 #######   //  ########
   ###    //        ##
   ###   //___     ##
   ###      //   ##
   ###     //   ##
   ###    //   #########
""")


def _apply_explicit_overrides(keys: dict) -> None:
    """Rebuild the global config so the programmatic per-run ``keys`` win over the environment.

    Guided mode and bulk mode pass the generated feature file / per-folder project
    root. With the legacy Env > Dict precedence an env value such as
    ``INPUT_GHERKIN_FILE_PATH`` would silently make every bulk folder run the same
    file, so the rebuild is done with Dict > Env scoped to this call only. The
    rebuild re-parses the CLI, so the keys are also kept above CLI flags (e.g.
    ``--project-base X --bulk`` must not reset every folder's root to X).
    """
    previous = os.environ.get("CONFIG_DICT_OVERRIDES_ENV")
    os.environ["CONFIG_DICT_OVERRIDES_ENV"] = "true"
    try:
        with explicit_config_overrides(keys):
            set_global_conf(keys, override=True)
    finally:
        if previous is None:
            os.environ.pop("CONFIG_DICT_OVERRIDES_ENV", None)
        else:
            os.environ["CONFIG_DICT_OVERRIDES_ENV"] = previous


async def a_main() -> list[dict]:
    _print_banner()

    summaries: list[dict] = []

    cfg = get_global_conf()
    guided_test = cfg.get_guided_test_description()

    if cfg.should_run_guided() or guided_test:
        if cfg.should_dry_run():
            if not guided_test:
                logger.error("--dry-run requires --test with a description.")
                raise SystemExit(1)
            feature = await generate_gherkin_from_description(guided_test)
            print_feature_block(feature)
            return summaries

        llm = get_litellm_chat_model("planner_agent")
        feature_path = await run_guided_mode(llm, test_description=guided_test)
        _apply_explicit_overrides({"INPUT_GHERKIN_FILE_PATH": feature_path})
        logger.info("Running generated test...")
        summaries.append(await sequential_process())
        return summaries

    if get_global_conf().should_execute_bulk():
        project_base = get_global_conf().get_project_source_root()
        tests_dir = os.path.join(project_base, "tests")

        if not (os.path.isdir(tests_dir) and os.listdir(tests_dir)):
            logger.error("Bulk execution requested but no tests directory found at: %s", tests_dir)
            raise SystemExit(1)

        logger.info(f"Bulk execution: Processing tests directory at {tests_dir}")
        for test_folder in os.listdir(tests_dir):
            test_dir = os.path.join(tests_dir, test_folder)
            if os.path.isdir(test_dir):
                logger.info(f"Processing test folder: {test_folder}")
                test_dir_name = os.path.basename(test_dir)
                _apply_explicit_overrides(
                    {
                        "PROJECT_SOURCE_ROOT": test_dir,
                        "INPUT_GHERKIN_FILE_PATH": os.path.join(test_dir, "input", f"{test_dir_name}.feature"),
                        "TEST_DATA_PATH": os.path.join(test_dir, "test_data"),
                    }
                )
                summaries.append(await sequential_process())
        return summaries

    logger.info("Single test execution mode")
    summaries.append(await sequential_process())
    return summaries


def _finalize_exit_code(summaries: list[dict]) -> None:
    """Log the aggregate result and, with ``STRICT_EXIT_CODE=true``, exit non-zero on failures.

    By default the process exits 0 regardless of scenario results (legacy behaviour).
    """
    totals = {"total": 0, "passed": 0, "failed": 0, "errors": 0}
    for summary in summaries:
        for key in totals:
            totals[key] += summary.get(key, 0) or 0

    logger.info(f"Test run finished: {totals['passed']} passed, {totals['failed']} failed, {totals['errors']} errors")

    if not strict_exit_code_enabled():
        return

    if compute_exit_code(summaries) == 1:
        logger.warning("STRICT_EXIT_CODE active: exiting with status 1 due to test failures/errors.")
        raise SystemExit(1)


# Strong references to tasks spawned onto an already-running loop: asyncio only
# keeps weak references to pending tasks, so an unreferenced task can be
# garbage-collected mid-run.
_background_tasks: set[asyncio.Task] = set()


def main() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        task = loop.create_task(a_main())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    else:
        summaries = asyncio.run(a_main()) or []
        _finalize_exit_code(summaries)


if __name__ == "__main__":  # pragma: no cover
    main()
