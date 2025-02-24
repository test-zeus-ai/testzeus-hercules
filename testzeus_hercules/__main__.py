import json
import os
import sys
from typing import Any, Dict, List, Optional

from junit2htmlreport.runner import run as prepare_html
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.runner import SingleCommandInputRunner
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.gherkin_helper import (
    process_feature_file,
    serialize_feature_file,
)
from testzeus_hercules.utils.junit_helper import JUnitXMLGenerator
from testzeus_hercules.utils.logger import logger


def process_sequential() -> None:
    """
    Process feature files, run test cases, and generate JUnit XML results.

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
    feature_file_name = os.path.basename(input_gherkin_file_path)
    result_files: List[str] = []
    final_result_file = (
        f"{get_global_conf().get_junit_xml_base_path()}/final_results.xml"
    )

    for feat in list_of_feats:
        file_path = feat["file_path"]
        feature = feat["feature"]
        scenario = feat["scenario"]

        command = serialize_feature_file(file_path)
        logger.info(f"Running test case: {scenario}")

        # Create and run the test case
        runner = SingleCommandInputRunner(
            command=command,
            planner_max_chat_round=500,
            nav_max_chat_round=10,
            dont_terminate_browser_after_run=dont_close_browser,
        )
        runner.initialize()
        result = runner.process_command(command)

        # Get test results
        execution_time = runner.execution_time
        cost_metric = runner.cost_metric if hasattr(runner, "cost_metric") else {}

        # Parse JSON content from chat history
        json_data: Dict[str, Any] = {}
        if runner.result and hasattr(runner.result, "chat_history"):
            for message in runner.result.chat_history:
                try:
                    content = message.get("content", "")
                    if (
                        isinstance(content, str)
                        and content.startswith("{")
                        and content.endswith("}")
                    ):
                        json_data = json.loads(content)
                        break
                except json.JSONDecodeError:
                    continue

        logger.info(f"Test case completed: {scenario}")

        # Create JUnit XML generator
        generator = JUnitXMLGenerator()
        generator.add_test_case(
            name=scenario,
            classname=feature,
            time=execution_time,
            execution_cost=float(cost_metric.get("total_cost", 0.0)),
            token_used=int(cost_metric.get("total_tokens", 0)),
            stdout=json_data.get("final_response"),
            status="passed" if json_data.get("is_passed", True) else "failed",
            failure_message=(
                json_data.get("assert_summary")
                if not json_data.get("is_passed", True)
                else None
            ),
        )

        # Write test case results
        result_file = os.path.join(
            get_global_conf().get_junit_xml_base_path(),
            f"{feature}_{scenario}_results.xml",
        )
        generator.write_xml(result_file)
        result_files.append(result_file)

    # Merge all JUnit XML results
    JUnitXMLGenerator.merge_junit_xml(result_files, final_result_file)
    logger.info(f"Final result file: {final_result_file}")

    # Generate HTML report
    html_report_path = final_result_file.replace(".xml", ".html")
    prepare_html([final_result_file, html_report_path])
    logger.info(f"HTML report generated: {html_report_path}")


def main() -> None:
    """
    Main entry point for the application.
    """
    try:
        process_sequential()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt. Shutting down...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error in main: {e}")
        add_event(EventType.ERROR, EventData(detail=str(e)))
        sys.exit(1)


if __name__ == "__main__":
    main()
