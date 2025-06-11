import asyncio
import json
import os
import time
import concurrent.futures
from typing import List, Dict, Any

import aiofiles
from junit2htmlreport.runner import run as prepare_html
from testzeus_hercules.config import get_global_conf, set_global_conf
from testzeus_hercules.core.runner import SingleCommandInputRunner
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.gherkin_helper import (
    process_feature_file,
    serialize_feature_file,
)
from testzeus_hercules.utils.junit_helper import JUnitXMLGenerator, build_junit_xml
from testzeus_hercules.utils.logger import logger


async def sequential_process(feature_files: List[Dict[str, str]] | None = None) -> List[Dict[str, Any]]:
    """
    sequential_process function to process feature files, run test cases, and generate JUnit XML results.

    This function performs the following steps:
    1. Processes the feature file to get a list of features.
    2. Retrieves the input Gherkin file path and extracts the feature file name.
    3. Initializes an empty list to store test results.
    4. Constructs the final result file name for the JUnit XML output.
    5. Iterates over each feature in the list of features:
        a. Extracts the file path, feature name, and scenario ID.
        b. Serializes the feature file into a command.
        c. Logs the start of the test case execution.
        d. Runs the test case using SingleCommandInputRunner.
        e. Collects the test result, execution time, and cost metrics.
        f. Parses the JSON content from the runner's chat history.
        g. Logs the completion of the test case.
        h. Builds the JUnit XML for the test case and appends it to the results list.
    6. Merges all JUnit XML results into a single file.
    7. Logs the location of the final result file.
    """
    dont_close_browser = get_global_conf().get_dont_close_browser()
    
    if feature_files is None:
        list_of_feats = await process_feature_file(dont_append_header=dont_close_browser)
    else:
        list_of_feats = feature_files
    
    input_gherkin_file_path = get_global_conf().get_input_gherkin_file_path()
    feature_file_name = os.path.basename(input_gherkin_file_path)

    result_of_tests = []

    add_event(EventType.RUN, EventData(detail="Total Runs: " + str(len(list_of_feats))))
    
    for feat in list_of_feats:
        file_path = feat["output_file"]
        feature_name = feat["feature"]
        scenario = feat["scenario"]
        # sanatise stake_id
        stake_id = (
            scenario.replace(" ", "_")
            .replace(":", "_")
            .replace("/", "_")
            .replace("\\", "_")
            .replace(".", "_")
        )

        get_global_conf().set_default_test_id(stake_id)

        cmd = await serialize_feature_file(file_path)

        logger.info(f"Running testcase: {stake_id}")

        runner = SingleCommandInputRunner(
            stake_id=stake_id,
            command=cmd,
            dont_terminate_browser_after_run=dont_close_browser,
        )
        await runner.start()

        runner_result = {}
        cost_metrics = {}

        if get_global_conf().get_token_verbose() and runner.simple_hercules and runner.simple_hercules.agents_map:
            for ag_name, agent in runner.simple_hercules.agents_map.items():
                if agent and agent.client and agent.client.total_usage_summary:
                    for key, value in agent.client.total_usage_summary.items():
                        if key == "total_cost":
                            cost_metrics["total_cost"] = (
                                cost_metrics.get("total_cost", 0) + value
                            )
                        elif isinstance(value, dict):
                            if ag_name not in cost_metrics:
                                cost_metrics[ag_name] = {}
                            if key not in cost_metrics[ag_name]:
                                cost_metrics[ag_name][key] = {
                                    "cost": 0,
                                    "prompt_tokens": 0,
                                    "completion_tokens": 0,
                                    "total_tokens": 0,
                                }

                            cost_metrics[ag_name][key]["cost"] += value.get("cost", 0)
                            cost_metrics[ag_name][key]["prompt_tokens"] += value.get(
                                "prompt_tokens", 0
                            )
                            cost_metrics[ag_name][key][
                                "completion_tokens"
                            ] += value.get("completion_tokens", 0)
                            cost_metrics[ag_name][key]["total_tokens"] += value.get(
                                "total_tokens", 0
                            )
                        else:
                            cost_metrics[ag_name][key] = (
                                cost_metrics.get(key, 0) + value
                            )

        execution_time = runner.execution_time
        if runner.result and runner.result.chat_history:
            s_rr = runner.result.chat_history[-1]["content"]
            json_content = s_rr.replace("```json\n", "").replace("\n```", "").strip()
            try:
                runner_result = json.loads(json_content)
            except json.JSONDecodeError:
                logger.error(f"Failed to decode JSON: {json_content}")

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
                proofs_path=get_global_conf().get_proof_path(
                    runner.device_manager.stake_id if runner.device_manager else stake_id
                ),
                proofs_screenshot_path=runner.device_manager._screenshots_dir if runner.device_manager and runner.device_manager._screenshots_dir else "",
                proofs_video_path=runner.device_manager.get_latest_video_path() if runner.device_manager and hasattr(runner.device_manager, 'get_latest_video_path') else "",
                network_logs_path=runner.device_manager.request_response_log_file if runner.device_manager and runner.device_manager.request_response_log_file else "",
                logs_path=get_global_conf().get_source_log_folder_path(stake_id),
                planner_thoughts_path=get_global_conf().get_source_log_folder_path(
                    stake_id
                )
                + "/chat_messages.json",
            )
        )

    return result_of_tests


async def generate_junit_xml_report(result_of_tests: List[Dict[str, Any]]) -> None:
    """Generate JUnit XML and HTML reports from test results"""
    input_gherkin_file_path = get_global_conf().get_input_gherkin_file_path()
    feature_file_name = os.path.basename(input_gherkin_file_path)
    
    final_result_file_name = (
        f"{get_global_conf().get_junit_xml_base_path()}/{feature_file_name}_result.xml"
    )

    junit_xml_files = [test_result for test_result in result_of_tests if isinstance(test_result, str)]
    await JUnitXMLGenerator.merge_junit_xml(junit_xml_files, final_result_file_name)
    logger.info(f"Results published in junitxml file: {final_result_file_name}")

    final_result_html_file_name = (
        f"{get_global_conf().get_junit_xml_base_path()}/{feature_file_name}_result.html"
    )
    prepare_html([final_result_file_name, final_result_html_file_name])
    logger.info(f"Results published in html file: {final_result_html_file_name}")


async def process_test_directory(test_dir: str) -> None:
    """
    Process a single test directory by updating config paths and running sequential_process

    Args:
        test_dir (str): Path to the test directory to process
    """
    # Update config paths for this test directory
    test_dir_name = os.path.basename(test_dir)
    test_config = {
        "PROJECT_SOURCE_ROOT": test_dir,
        "INPUT_GHERKIN_FILE_PATH": os.path.join(
            test_dir, "input", f"{test_dir_name}.feature"
        ),
        "TEST_DATA_PATH": os.path.join(test_dir, "test_data"),
    }

    # Update the singleton config
    set_global_conf(test_config, override=True)

    logger.info(f"Processing test directory: {test_dir}")
    result_of_tests = await sequential_process()
    await generate_junit_xml_report(result_of_tests)


async def parallel_process(
    feature_files: List[Dict[str, str]], 
    max_workers: int = 3,
    dont_close_browser: bool = False
) -> List[Dict[str, Any]]:
    """Process feature files in parallel with limited concurrency"""
    result_of_tests = []
    
    async def process_single_test(feature_dict: Dict[str, str]) -> Dict[str, Any]:
        file_path = feature_dict["output_file"]
        feature_name = feature_dict["feature"]
        scenario = feature_dict["scenario"]
        
        stake_id = (
            scenario.replace(" ", "_")
            .replace(":", "_")
            .replace("/", "_")
            .replace("\\", "_")
            .replace(".", "_")
        )
        
        get_global_conf().set_default_test_id(stake_id)
        cmd = await serialize_feature_file(file_path)
        
        logger.info(f"Running testcase: {stake_id}")
        runner = SingleCommandInputRunner(
            stake_id=stake_id,
            command=cmd,
            dont_terminate_browser_after_run=dont_close_browser,
        )
        
        await runner.start()
        
        execution_time = runner.execution_time
        runner_result = {}
        cost_metrics = {}
        
        if runner.result and runner.result.chat_history:
            s_rr = runner.result.chat_history[-1]["content"]
            json_content = s_rr.replace("```json\n", "").replace("\n```", "").strip()
            try:
                runner_result = json.loads(json_content)
            except json.JSONDecodeError:
                logger.error(f"Failed to decode JSON: {json_content}")
        
        return await build_junit_xml(
            runner_result,
            execution_time,
            cost_metrics,
            feature_name,
            scenario,
            feature_file_path=file_path,
            output_file_path="",
            proofs_path=get_global_conf().get_proof_path(
                runner.device_manager.stake_id if runner.device_manager else stake_id
            ),
            proofs_screenshot_path=runner.device_manager._screenshots_dir if runner.device_manager and runner.device_manager._screenshots_dir else "",
            proofs_video_path=runner.device_manager.get_latest_video_path() if runner.device_manager and hasattr(runner.device_manager, 'get_latest_video_path') else "",
            network_logs_path=runner.device_manager.request_response_log_file if runner.device_manager and runner.device_manager.request_response_log_file else "",
            logs_path=get_global_conf().get_source_log_folder_path(stake_id),
            planner_thoughts_path=get_global_conf().get_source_log_folder_path(stake_id) + "/chat_messages.json",
        )
    
    semaphore = asyncio.Semaphore(max_workers)
    
    async def process_with_semaphore(feature_dict: Dict[str, str]) -> Dict[str, Any]:
        async with semaphore:
            return await process_single_test(feature_dict)
    
    tasks = [process_with_semaphore(feature_dict) for feature_dict in feature_files]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Test {i} failed with exception: {result}")
            feature_dict = feature_files[i]
            failure_result = await build_junit_xml(
                {"is_passed": False, "final_response": f"Exception: {result}"},
                0,
                {},
                feature_dict["feature"],
                feature_dict["scenario"],
                feature_file_path=feature_dict["output_file"],
                output_file_path="",
                proofs_path="",
                proofs_screenshot_path="",
                proofs_video_path="",
                network_logs_path="",
                logs_path="",
                planner_thoughts_path="",
            )
            result_of_tests.append(failure_result)
        else:
            result_of_tests.append(result)
    
    return result_of_tests


async def a_main() -> None:
    """Main execution function with performance optimizations"""
    
    parallel_mode = os.getenv("PARALLEL_EXECUTION", "false").lower() == "true"
    max_workers = int(os.getenv("MAX_PARALLEL_WORKERS", "3"))

    def is_width_gt_120() -> bool:
        try:
            columns = os.get_terminal_size().columns
        except OSError:
            columns = 120
        return columns >= 120

    if is_width_gt_120():
        print(
            """
                                                 /$$      /$$/$$                 /$$
                                                | $$  /$ | $| $$                | $$
                                                | $$ /$$$| $| $$$$$$$  /$$$$$$ /$$$$$$
                                                | $$/$$ $$ $| $$__  $$|____  $|_  $$_/
                 ==+=+####                      | $$$$_  $$$| $$  \ $$ /$$$$$$$ | $$
                +=+ ##  ##                      | $$$/ \  $$| $$  | $$/$$__  $$ | $$ /$$
                #*  %*+ ###                     | $$/   \  $| $$  | $|  $$$$$$$ |  $$$$/
            #----=+**##***#**##%%               |__/     \__|__/  |__/\_______/  \___/
            #===============------*=*
            *=====+%#%+===========*==#
            *===##%###%###*=======*=+-=
            +==##=*#%@%%%##%======+=*=--+         /$$
          ##==+%-+%@@#*%%%%%#=======*===-+       |__/
         *+*==*#+#@@%---@%%##====+==*=*****#      /$$ /$$$$$$$             /$$$$$$/$$$$ /$$   /$$
          +*==+%=*%@@*=%%%%##====+==+*===*##     | $$/$$_____/            | $$_  $$_  $| $$  | $$
           #===#####%@%##%#%=====+=+=**+*###     | $|  $$$$$$             | $$ \ $$ \ $| $$  | $$
           *====+#%%##%#%%#======+=+==#**#%%     | $$\____  $$            | $$ | $$ | $| $$  | $$
           +=========++==========+=+==#**##%     | $$/$$$$$$$/            | $$ | $$ | $|  $$$$$$$
           +=====================+=+==***##%     |__|_______/             |__/ |__/ |__/\____  $$
           %#++++++++***+===++==+==+==***#%%                                           /$$  | $$
            %**%%*+***************#+==+***%%                                          |  $$$$$$/
            %#*%#% %**********###*++++=#**%%                                          \______/
            ###%%%#%**********######++*%**#%@
           %++=+###@**********####%   #+++*##
            #***##% #*********####    #+++#%#     /$$$$$$ /$$   /$$ /$$$$$$  /$$$$$$  /$$$$$$  /$$$$$$$ /$$$$$$
            %#**%%  #*********####    %#*#@%%    /$$__  $| $$  | $$/$$__  $$/$$__  $$/$$__  $$/$$_____//$$__  $$
            %#**%%    @%%%%%%%%%%%    %**#%%    | $$  \ $| $$  | $| $$  \__| $$  \ $| $$  \ $|  $$$$$$| $$$$$$$$
            %**#%      %####%##%      #**##%    | $$  | $| $$  | $| $$     | $$  | $| $$  | $$\____  $| $$_____/
            %**%%      %####%##%      #**##%    | $$$$$$$|  $$$$$$| $$     | $$$$$$$|  $$$$$$//$$$$$$$|  $$$$$$$
            #*#%%      #####%##%      #**%%     | $$____/ \______/|__/     | $$____/ \______/|_______/ \_______/
            #*##%       ####%##@      #**%%     | $$                       | $$
            #*#%@       ####%##@      #*#%@     | $$                       | $$
           %#*%%        %###%%%%      #*%%      |__/                       |__/
           +##%@        %###%%%       ###*#      /$$$$
           +*+*         %###%#%       @%*+#     /$$  $$
           **#+**#% @   %###%#%    %%%%%*+#     |__/\ $$
                 @@@@@@@%###%#%   #*++++#           /$$/
               @@@@@@@@@@%###%#%                   /$$/
             @@@@@@@@@@%+=####%##==++*%@@%        |__/
         @@@@@@@@@@@*=====++*%*=====%@@@@@@@@     /$$
      @@@@@@@@@@@*===============*@@@@@@@@@@@    |__/
     @@@@@@@@@*================%@@@@@@@@@+%@@
     @@@@@@@@+=++*+=========*%@@@@@@@@%%%%@@@
     @@@@@@@%**+=====++*++%@@@@@@@@@@*%@@@@
     @@@@@@@%###****++==+@@@@@@@@@@%%%@@@       /$$$$$$$                  /$$$$$$  /$$$$$$  /$$
         @@@ %%%#####**@@@@@@@@%*#@@@@         | $$__  $$                /$$__  $$/$$__  $|  $$
                   %%%#@@@@@@@%%%%@@           | $$  \ $$ /$$$$$$       | $$  \ $| $$  \ $|  $$
                       @@@@@@@@@@@             | $$  | $$/$$__  $$      | $$  | $| $$$$$$$|  $$
                        @@@@@@@                | $$  | $| $$  \ $$      | $$  | $| $$__  $| __/
                                               | $$  | $| $$  | $$      | $$/$$ $| $$  | $$
                                               | $$$$$$$|  $$$$$$/      |  $$$$$$| $$  | $$ /$$
                                               |_______/ \______/        \____ $$|__/  |__| __/
                                                                                \__/
            """
        )

    if get_global_conf().should_execute_bulk():
        project_base = get_global_conf().get_project_source_root()
        tests_dir = os.path.join(project_base, "tests")

        if os.path.isdir(tests_dir) and os.listdir(tests_dir):
            logger.info(f"Bulk execution: Processing tests directory at {tests_dir}")

            for test_folder in os.listdir(tests_dir):
                test_dir = os.path.join(tests_dir, test_folder)
                if os.path.isdir(test_dir):
                    logger.info(f"Processing test folder: {test_folder}")
                    await process_test_directory(test_dir)
        else:
            logger.error(
                "Bulk execution requested but no tests directory found at: %s",
                tests_dir,
            )
            exit(1)
    else:
        logger.info("Single test execution mode")
        feature_files = await process_feature_file()
        
        if parallel_mode:
            logger.info(f"Running {len(feature_files)} tests in parallel with {max_workers} workers")
            result_of_tests = await parallel_process(feature_files, max_workers)
        else:
            logger.info(f"Running {len(feature_files)} tests sequentially")
            result_of_tests = await sequential_process(feature_files)
        
        await generate_junit_xml_report(result_of_tests)


def main() -> None:
    asyncio.run(a_main())


if __name__ == "__main__":  # pragma: no cover
    main()
