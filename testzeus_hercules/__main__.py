import asyncio
import json
import os
import shutil
import ast
import sqlite3
import re

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
from testzeus_hercules.utils.html_helper import generate_html_report
from testzeus_hercules.utils.logger import logger
from datetime import datetime
from testzeus_hercules.config import set_global_conf, Browserconfig

from testzeus_hercules.utils.automation.init_db import init_db
from testzeus_hercules.utils.automation.tool_code_map import get_tool_code, TOOLS_LIBRARY

async def sequential_process() -> None:
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
    list_of_feats = await process_feature_file(dont_append_header=dont_close_browser)
    input_gherkin_file_path = get_global_conf().get_input_gherkin_file_path()
    # get name of the feature file using os package
    feature_file_name = os.path.basename(input_gherkin_file_path)

    result_of_tests = []
    final_script = ""
    all_imports = set()
    add_event(EventType.RUN, EventData(detail="Total Runs: " + str(len(list_of_feats))))
    
    # Create a main script that will contain all scenarios
    main_script = """
async def run_all_scenarios():
"""
    scenarios = {}
    for feat in list_of_feats:
        file_path = feat["output_file"]
        feature_name = feat["feature"]
        scenario = feat["scenario"]
        # sanatise stake_id
        stake_id = re.sub(r'[^\w]', '', scenario)
        
        # Create a code generation dir named with the feature and current timestamp
        folder_dir = os.path.join(get_global_conf().get_output_code_dir(), stake_id)
        os.makedirs(folder_dir, exist_ok=True)
        if not get_global_conf().get_current_script_dir():
            set_global_conf({"CURRENT_GENERATED_SCRIPT_DIR": folder_dir})
        set_global_conf({"STAKED_ID": stake_id})

        # TODO: remove the following set default hack later.
        get_global_conf().set_default_test_id(stake_id)

        cmd = await serialize_feature_file(file_path)

        logger.info(f"Running testcase: {stake_id}")
        logger.info(f"testcase details: {cmd}")

        runner = SingleCommandInputRunner(
            stake_id=stake_id,
            command=cmd,
            dont_terminate_browser_after_run=dont_close_browser,
        )
        browserc_onfig = Browserconfig()
        await runner.start(browserc_onfig)

        runner_result = {}
        cost_metrics = {}

        if get_global_conf().get_token_verbose():
            # Parse usage and sum across all agents based on keys
            for ag_name, agent in runner.simple_hercules.agents_map.items():
                if agent and agent.client and agent.client.total_usage_summary:
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

        if runner.result and runner.result.summary:
            s_rr = runner.result.summary
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
                proofs_path=get_global_conf().get_proof_path(runner.browser_manager.stake_id),
                proofs_screenshot_path=runner.browser_manager._screenshots_dir,
                proofs_video_path=runner.browser_manager.get_latest_video_path(),
                network_logs_path=runner.browser_manager.request_response_log_file,
                logs_path=get_global_conf().get_source_log_folder_path(stake_id),
                planner_thoughts_path=get_global_conf().get_source_log_folder_path(stake_id) + "/chat_messages.json",
            )
        )
        sanitized_name = stake_id.replace("'", "").replace('"', "").replace(" ", "_")
        script, imp_tools = await generate_scenario_script(sanitized_name)
        final_script += f'{script} \n\n'
        all_imports.update(imp_tools)
        
        # Add the scenario to the main script with sanitized name
        scenarios[sanitized_name] = sanitized_name
        
        # await db_cleanup()

    # Complete the main script
    main_script += f'''
    scenarios = {scenarios}
    # Run all scenarios
    for scenario_name, scenario_func in scenarios.items():
        try:
            logger.info(f"Running scenario: {{scenario_name}}")
            await scenario_func()
        except Exception as e:
            logger.error(f"Error running scenario {{scenario_name}}: {{e}}")
            continue

if __name__ == "__main__":
    asyncio.run(run_all_scenarios())
'''

    # Write the final combined script
    final_script_path = os.path.join(get_global_conf().get_current_script_dir(), "combined_script.py")
    with open(final_script_path, "w") as f:
        # Write imports first, properly organized
        f.write("""import asyncio
import json
import base64
import time
import re
import httpx
from typing import Any, List, Dict
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import Page, ElementHandle
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.sql import text
import playwright_recaptcha

from browser_manager import PlaywrightTest
from all_tools import openurl, click
import logging

# Configure logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Create file handler
file_handler = logging.FileHandler('automation.log')
file_handler.setLevel(logging.INFO)

# Create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)

# Add handlers to logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

""")
        # Write all individual scenario scripts
        f.write(final_script + "\n\n")
        # Write the main script
        f.write(main_script)
    logger.info(f"Generated combined script saved to: {final_script_path}")

    final_result_file_name = f"{get_global_conf().get_junit_xml_base_path()}/{feature_file_name}_result.xml"

    await JUnitXMLGenerator.merge_junit_xml(result_of_tests, final_result_file_name)
    logger.info(f"Results published in junitxml file: {final_result_file_name}")

    # building html from junitxml
    final_result_html_file_name = f"{get_global_conf().get_junit_xml_base_path()}/{feature_file_name}_result.html"

    await generate_html_report(final_result_file_name, final_result_html_file_name)
    logger.info(f"Results published in html file: {final_result_html_file_name}")
    # source_folder = get_global_conf().get_current_script_dir()
    await gen_browser_managment_script()
    await generate_all_tools_script()
    
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
        "INPUT_GHERKIN_FILE_PATH": os.path.join(test_dir, "input", f"{test_dir_name}.feature"),
        "TEST_DATA_PATH": os.path.join(test_dir, "test_data"),
    }

    # Update the singleton config
    set_global_conf(test_config, override=True)
    logger.info(f"Processing test directory: {test_dir}")
    await sequential_process()


async def a_main() -> None:
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
                    await process_test_directory(test_dir)
        else:
            logger.error(
                "Bulk execution requested but no tests directory found at: %s",
                tests_dir,
            )
            exit(1)
    else:
        # Single test case execution
        logger.info("Single test execution mode")
        project_base = get_global_conf().get_project_source_root()
        gen_code_dir = os.path.join(project_base, "output", "generated_code")
        if not os.path.isdir(gen_code_dir):
            os.makedirs(gen_code_dir)
            set_global_conf({"OUTPUT_CODE_DIR": gen_code_dir})

        await sequential_process()



# BROWSER MANAGEMENT SCRIPT
async def gen_browser_managment_script():
    """
    Saves the current script to a directory.
    """
    gen_code_dir = os.path.join(get_global_conf().get_current_script_dir(), "browser_manager.py")
    automation_template = ""
    with open("testzeus_hercules/utils/automation/automation_script_template.py", "r") as f:
        automation_template = f.read()
    with open(gen_code_dir, "w") as f:
        f.write(automation_template)

# SCNARIO SCRIPT
async def generate_scenario_script(scenario_id):
    calling_tool_map, tool_imports = extract_tools_from_db()
    
    # Initialize script variable
    script = f"""
async def {scenario_id}():
    # Initialize the browser manager
    browser_manager = PlaywrightTest()
    await browser_manager.async_initialize()
    
"""
    
    # Add tool execution calls
    for tool_call in calling_tool_map:
        print(f'Tool Called {tool_call}')
        # Extract the tool name and arguments
        tool_name = tool_call.split('(')[0].strip()    
        # Add logging and execution for each tool
        script += f"""    logger.info("Executing {tool_name}")
    try:
        result = await {tool_call}
        logger.info(f"{tool_name} result: %s", result)
    except Exception as e:
        logger.error(f"Error executing {tool_name}: %s", str(e))
        raise
        
"""
    
    return script, tool_imports

async def generate_all_tools_script():
    """
    Generates all_tools.py file containing all tools from tool_code_map.py.
    The generated file will contain all the tool functions in a single file.
    """
    # Common imports needed by most tools
    common_imports = """
import asyncio
import traceback
import inspect
import json
import base64
import time
import re
import httpx
from typing import Any, List, Dict
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import Page, ElementHandle
from sqlalchemy.ext.asyncio import AsyncEngine

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
import playwright_recaptcha
from sqlalchemy.sql import text
from sqlalchemy.exc import SQLAlchemyError

"""
    
    # Generate the all_tools.py content
    tools_content = common_imports + "\n\n"
    
    # Add all tools from the TOOLS_LIBRARY
    for tool_name, tool_code in TOOLS_LIBRARY.items():
        tools_content += f"# Tool: {tool_name}\n"
        tools_content += tool_code + "\n\n"
    
    # Save the generated script to a file
    script_path = os.path.join(get_global_conf().get_current_script_dir(), "all_tools.py")
    async with aiofiles.open(script_path, "w") as f:
        await f.write(tools_content)
    logger.info(f"Generated all_tools.py script saved to: {script_path}")
    
def extract_tools_from_db():
    """Extract tools and their parameters from the automation_sequence.db file."""
    # Connect to SQLite database
    conn = sqlite3.connect('automation_sequence.db')
    cursor = conn.cursor()
    
    # Get all rows from automation_sequence table
    cursor.execute("SELECT id, name, parameters, imports FROM automation_sequence")
    rows = cursor.fetchall()
    
    # Dictionary to store tools by their type
    calling_tool_map = []
    tool_imports = set()
    for row in rows:
        print()
        tool_id, tool_name, parameters_str, imports_str = row
        print(f'Parameters {parameters_str}')
        # Extract the actual tool name from the name field
        # The database stores just 'openurl', 'click', etc.
        try:
            # Try to parse the parameters
            parameters = ast.literal_eval(parameters_str)
            # Convert all parameters to strings and handle different types appropriately
            str_params = []
            for param in parameters:
                if isinstance(param, str):
                    str_params.append(f"'{param}'")
                else:
                    str_params.append(str(param))
            joined_params = ",".join(str_params)
            print(f'Joined Parameters {joined_params}')
            imports = ast.literal_eval(imports_str)
            calling_tool_map.append(f'{tool_name}(browser_manager, {joined_params})')
            tool_imports.update(imports)

        except (SyntaxError, ValueError) as e:
            print(f"Error parsing parameters for tool {tool_id}: {e}")
            continue
    conn.close()
    return calling_tool_map, tool_imports

##Downloading Generated Automation Script into Download Folder
async def get_downloads_folder():
    """Get the path to the user's Downloads folder in a cross-platform way."""
    if os.name == 'nt':  # Windows
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                          r'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders') as key:
            return winreg.QueryValueEx(key, '{374DE290-123F-4565-9164-39C4925E467B}')[0]
    else:  # macOS and Linux - most modern systems
        return os.path.join(os.path.expanduser('~'), 'Downloads')

async def generate_source_code(source_folder):
    # Validate source folder
    if not os.path.isdir(source_folder):
        raise ValueError(f"Source folder does not exist: {source_folder}")
    
    # Get Downloads folder path
    downloads_folder = await get_downloads_folder()
    
    # Get the folder name
    folder_name = os.path.basename(os.path.normpath(source_folder))
    
    # Add timestamp to make the folder name unique
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_folder_name = f"{folder_name}_backup_{timestamp}"
    destination_path = os.path.join(downloads_folder, dest_folder_name)
    
    # Copy the folder
    try:
        shutil.copytree(source_folder, destination_path)
        print(f"Successfully saved folder to Downloads: {destination_path}")
        return destination_path
    except Exception as e:
        print(f"Error saving folder: {e}")
        raise

async def db_cleanup():
    # Clean automation_sequence.db after each iteration
    try:
        conn = sqlite3.connect('automation_sequence.db')
        cursor = conn.cursor()
        cursor.execute("DELETE FROM automation_sequence")
        conn.commit()
        conn.close()
        logger.info("Cleaned automation_sequence.db for next iteration")
    except Exception as e:
        logger.error(f"Error cleaning automation_sequence.db: {e}")

def main() -> None:
    init_db()
    asyncio.run(a_main())


if __name__ == "__main__":  # pragma: no cover
    main()




