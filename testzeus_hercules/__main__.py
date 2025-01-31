import asyncio
import json
import os

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


def sequential_process() -> None:
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
    list_of_feats = process_feature_file(dont_append_header=dont_close_browser)
    input_gherkin_file_path = get_global_conf().get_input_gherkin_file_path()
    # get name of the feature file using os package
    feature_file_name = os.path.basename(input_gherkin_file_path)

    result_of_tests = []

    add_event(EventType.RUN, EventData(detail="Total Runs: " + str(len(list_of_feats))))
    for feat in list_of_feats:
        file_path = feat["output_file"]
        feature_name = feat["feature"]
        scenario = feat["scenario"]
        # sanatise stake_id
        stake_id = scenario.replace(" ", "_").replace(":", "_").replace("/", "_").replace("\\", "_").replace(".", "_")

        # TODO: remove the following set default hack later.
        get_global_conf().set_default_test_id(stake_id)

        cmd = serialize_feature_file(file_path)

        logger.info(f"Running testcase: {stake_id}")
        logger.info(f"testcase details: {cmd}")
        runner = SingleCommandInputRunner(
            stake_id=stake_id,
            command=cmd,
            dont_terminate_browser_after_run=dont_close_browser,
        )
        asyncio.run(runner.start())

        runner_result = {}
        cost_metrics = {}

        if get_global_conf().get_token_verbose():
            # Parse usage and sum across all agents based on keys
            for ag_name, agent in runner.simple_hercules.agents_map.items():
                if agent.client and agent.client.total_usage_summary:
                    for key, value in agent.client.total_usage_summary.items():
                        if key == "total_cost":
                            # Sum total_cost across agents
                            cost_metrics["total_cost"] = cost_metrics.get("total_cost", 0) + value
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
                            cost_metrics[ag_name][key]["prompt_tokens"] += value.get("prompt_tokens", 0)
                            cost_metrics[ag_name][key]["completion_tokens"] += value.get("completion_tokens", 0)
                            cost_metrics[ag_name][key]["total_tokens"] += value.get("total_tokens", 0)
                        else:
                            # For unexpected keys, just add them as-is
                            cost_metrics[ag_name][key] = cost_metrics.get(key, 0) + value

        execution_time = runner.execution_time
        if runner.result and runner.result.chat_history:
            s_rr = runner.result.chat_history[-1]["content"]
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
            build_junit_xml(
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
    JUnitXMLGenerator.merge_junit_xml(result_of_tests, final_result_file_name)
    logger.info(f"Results published in junitxml file: {final_result_file_name}")

    # building html from junitxml
    final_result_html_file_name = f"{get_global_conf().get_junit_xml_base_path()}/{feature_file_name}_result.html"
    prepare_html([final_result_file_name, final_result_html_file_name])
    logger.info(f"Results published in html file: {final_result_html_file_name}")


def process_test_directory(test_dir: str) -> None:
    """
    Process a single test directory by updating config paths and running sequential_process

    Args:
        test_dir (str): Path to the test directory to process
    """
    # Update config paths for this test directory
    test_dir_name = os.path.basename(test_dir)
    test_config = {
        "PROJECT_SOURCE_ROOT": test_dir,
        "INPUT_GHERKIN_FILE_PATH": os.path.join(test_dir, "input", f"{test_dir_name}.feature"),
        "TEST_DATA_PATH": os.path.join(test_dir, "test_data"),
    }

    # Update the singleton config
    set_global_conf(test_config, override=True)

    logger.info(f"Processing test directory: {test_dir}")
    sequential_process()


def main() -> None:
    """
    Main function that checks for bulk execution flag and runs tests accordingly
    """

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
            %#*%#% %**********###*++++=#**%%@                                          \______/
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

    # Check bulk execution flag instead of directory existence
    if get_global_conf().should_execute_bulk():
        project_base = get_global_conf().get_project_source_root()
        tests_dir = os.path.join(project_base, "tests")

        if os.path.isdir(tests_dir) and os.listdir(tests_dir):
            logger.info(f"Bulk execution: Processing tests directory at {tests_dir}")

            for test_folder in os.listdir(tests_dir):
                test_dir = os.path.join(tests_dir, test_folder)
                if os.path.isdir(test_dir):
                    logger.info(f"Processing test folder: {test_folder}")
                    process_test_directory(test_dir)
        else:
            logger.error("Bulk execution requested but no tests directory found at: %s", tests_dir)
            exit(1)
    else:
        # Single test case execution
        logger.info("Single test execution mode")
        sequential_process()


if __name__ == "__main__":  # pragma: no cover
    main()
