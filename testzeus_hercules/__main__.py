import asyncio
import json
import os

from junit2htmlreport.runner import run as prepare_html
from testzeus_hercules.config import get_global_conf, set_global_conf
from testzeus_hercules.core.runner import SingleCommandInputRunner
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.gherkin_helper import process_feature_file, serialize_feature_file
from testzeus_hercules.utils.junit_helper import JUnitXMLGenerator, build_junit_xml
from testzeus_hercules.utils.gherkin_generator import generate_gherkin_from_description, print_feature_block
from testzeus_hercules.utils.litellm_helper import get_litellm_chat_model
from testzeus_hercules.utils.llm_helper import parse_agent_response
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.test_builder import run_guided_mode


async def sequential_process() -> None:
    dont_close_browser = get_global_conf().get_dont_close_browser()
    list_of_feats = await process_feature_file(dont_append_header=dont_close_browser)
    input_gherkin_file_path = get_global_conf().get_input_gherkin_file_path()
    feature_file_name = os.path.basename(input_gherkin_file_path)

    result_of_tests = []
    add_event(EventType.RUN, EventData(detail="Total Runs: " + str(len(list_of_feats))))

    if not list_of_feats:
        logger.error(
            "No scenarios found in feature file: %s. "
            "Ensure the file contains at least one 'Scenario:' block.",
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
        await runner.start()

        runner_result = {}
        cost_metrics = {}

        if get_global_conf().get_token_verbose():
            for ag_name, agent in runner.simple_hercules.agents_map.items():
                client = getattr(getattr(agent, "llm", None), "client", None) or getattr(agent, "client", None)
                usage = getattr(client, "total_usage_summary", None) if client else None
                if usage and ag_name in usage:
                    for key, value in usage.items():
                        if key == "total_cost":
                            cost_metrics["total_cost"] = cost_metrics.get("total_cost", 0) + value
                        elif isinstance(value, dict):
                            cost_metrics.setdefault(ag_name, {}).setdefault(key, {
                                "cost": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                            })
                            cost_metrics[ag_name][key]["cost"] += value.get("cost", 0)
                            cost_metrics[ag_name][key]["prompt_tokens"] += value.get("prompt_tokens", 0)
                            cost_metrics[ag_name][key]["completion_tokens"] += value.get("completion_tokens", 0)
                            cost_metrics[ag_name][key]["total_tokens"] += value.get("total_tokens", 0)
                        else:
                            cost_metrics[ag_name][key] = cost_metrics.get(key, 0) + value

        execution_time = runner.execution_time

        if runner.result:
            summary = runner.result.summary
            if summary:
                runner_result = parse_agent_response(summary)
            elif getattr(runner.result, "terminate", "no") == "yes":
                runner_result = {"terminate": "yes", "is_passed": False, "final_response": "Test ended without planner output."}

            plan = runner_result.get("plan", "")
            if plan and runner_result.get("terminate") == "no":
                lines = plan.split("\n") if isinstance(plan, str) else []
                plan_steps = [line.strip() for line in lines if line.strip() and any(c.isdigit() for c in line[:3])]
                completed_steps = [step for step in plan_steps if "(Completed)" in step]
                if plan_steps and len(completed_steps) == len(plan_steps):
                    logger.info(f"All {len(plan_steps)} plan steps completed. Auto-terminating test.")
                    runner_result["terminate"] = "yes"
                    runner_result["is_passed"] = True
                    if not runner_result.get("final_response"):
                        runner_result["final_response"] = (
                            f"Test completed successfully. All {len(plan_steps)} steps executed."
                        )
            if summary and not runner_result:
                logger.warning("Could not parse planner result from test output; marking as incomplete.")

        logger.info(f"Run completed for testcase: {scenario}")
        if cost_metrics:
            logger.info(f"Test run cost is : {cost_metrics}")

        result_of_tests.append(
            await build_junit_xml(
                runner_result,
                execution_time,
                cost_metrics,
                feature_name,
                scenario,
                feature_file_path=file_path,
                output_file_path="",
                proofs_path=get_global_conf().get_proof_path(runner.browser_manager.stake_id),
                proofs_screenshot_path=runner.browser_manager._screenshots_dir,
                proofs_video_path=runner.browser_manager.get_latest_video_path(),
                network_logs_path=runner.browser_manager.request_response_log_file,
                logs_path=get_global_conf().get_source_log_folder_path(stake_id),
                planner_thoughts_path=get_global_conf().get_source_log_folder_path(stake_id) + "/chat_messages.json",
            )
        )

    final_result_file_name = f"{get_global_conf().get_junit_xml_base_path()}/{feature_file_name}_result.xml"
    await JUnitXMLGenerator.merge_junit_xml(result_of_tests, final_result_file_name)
    logger.info(f"Results published in junitxml file: {final_result_file_name}")

    final_result_html_file_name = f"{get_global_conf().get_junit_xml_base_path()}/{feature_file_name}_result.html"
    prepare_html([final_result_file_name, final_result_html_file_name])
    logger.info(f"Results published in html file: {final_result_html_file_name}")


def _print_banner() -> None:
    print(r"""
 #######   //  ########
   ###    //        ##
   ###   //___     ##
   ###      //   ##
   ###     //   ##
   ###    //   #########
""")


async def a_main() -> None:
    _print_banner()

    cfg = get_global_conf()
    guided_test = cfg.get_guided_test_description()

    if cfg.should_run_guided() or guided_test:
        if cfg.should_dry_run():
            if not guided_test:
                logger.error("--dry-run requires --test with a description.")
                raise SystemExit(1)
            feature = await generate_gherkin_from_description(guided_test)
            print_feature_block(feature)
            return

        llm = get_litellm_chat_model("planner_agent")
        feature_path = await run_guided_mode(llm, test_description=guided_test)
        set_global_conf({"INPUT_GHERKIN_FILE_PATH": feature_path}, override=True)
        logger.info("Running generated test...")
        await sequential_process()
        return

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
                set_global_conf(
                    {
                        "PROJECT_SOURCE_ROOT": test_dir,
                        "INPUT_GHERKIN_FILE_PATH": os.path.join(test_dir, "input", f"{test_dir_name}.feature"),
                        "TEST_DATA_PATH": os.path.join(test_dir, "test_data"),
                    },
                    override=True,
                )
                await sequential_process()
        return

    logger.info("Single test execution mode")
    await sequential_process()


def main() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        loop.create_task(a_main())
    else:
        asyncio.run(a_main())


if __name__ == "__main__":  # pragma: no cover
    main()
