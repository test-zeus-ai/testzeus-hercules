import asyncio
import json
import os

from testzeus_hercules.config import (
    get_input_gherkin_file_path,
    get_junit_xml_base_path,
    set_default_test_id,
)
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
    list_of_feats = process_feature_file()
    input_gherkin_file_path = get_input_gherkin_file_path()
    # get name of the feature file using os package
    feature_file_name = os.path.basename(input_gherkin_file_path)

    result_of_tests = []
    final_result_file_name = (
        f"{get_junit_xml_base_path()}/{feature_file_name}_result.xml"
    )
    add_event(EventType.RUN, EventData(detail="Total Runs: " + str(len(list_of_feats))))
    for feat in list_of_feats:
        file_path = feat["output_file"]
        feature_name = feat["feature"]
        scenario = feat["scenario"]
        # sanatise stake_id
        stake_id = scenario.replace(" ", "_").replace(":", "_")

        # TODO: remove the following set default hack later.
        set_default_test_id(stake_id)

        cmd = serialize_feature_file(file_path)

        logger.info(f"Running testcase: {stake_id}")
        logger.info(f"testcase details: {cmd}")
        runner = SingleCommandInputRunner(
            stake_id=stake_id,
            command=cmd,
        )
        asyncio.run(runner.start())

        runner_result = {}
        cost_metrics = runner.result.cost or {}
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
        result_of_tests.append(
            build_junit_xml(
                runner_result, execution_time, cost_metrics, feature_name, scenario
            )
        )
    JUnitXMLGenerator.merge_junit_xml(result_of_tests, final_result_file_name)
    logger.info(f"Results published on file: {final_result_file_name}")


def main() -> None:
    """
    Main function to run the sequential_process function.
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
    sequential_process()


if __name__ == "__main__":  # pragma: no cover
    main()
