import datetime
import os
import tempfile
from typing import Any, Dict, List

import aiofiles
from junitparser import Failure, JUnitXml, Property, TestCase, TestSuite
from junitparser.junitparser import Properties, SystemOut
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.telemetry import EventData, EventType, add_event


def flatten_dict(d: Dict[str, Any], parent_key: str = "", sep: str = ".") -> Dict[str, Any]:
    """
    Flatten a nested dictionary.

    Args:
        d (Dict[str, Any]): The dictionary to flatten.
        parent_key (str): The base key to use for the flattened keys.
        sep (str): The separator to use between keys.

    Returns:
        Dict[str, Any]: The flattened dictionary.
    """
    items: List[tuple[str, Any]] = []
    for key, value in d.items():
        new_key = f"{parent_key}{sep}{key}" if parent_key else key
        if isinstance(value, dict):
            items.extend(flatten_dict(value, new_key, sep=sep).items())
        else:
            items.append((new_key, value))
    return dict(items)


class JUnitXMLGenerator:
    def __init__(
        self,
        suite_name: str,
        feature_file_path: str,
        output_file_path: str,
        proofs_path: str,
        proofs_video_path: str,
        proofs_screenshot_path: str,
        network_logs_path: str,
        logs_path: str,
        planner_thoughts_path: str,
    ) -> None:
        """
        Initialize the JUnitXMLGenerator.

        Args:
            suite_name (str): The name of the test suite.
        """
        self.suite_name = suite_name
        self.suite = TestSuite(suite_name)
        self.total_execution_cost = 0.0
        self.total_token_used = 0
        self.total_time = 0.0
        self.feature_file_path = feature_file_path
        self.output_file_path = output_file_path
        self.proofs_path = proofs_path
        self.proofs_video_path = proofs_video_path
        self.proofs_screenshot_path = proofs_screenshot_path
        self.logs_path = logs_path
        self.planner_thoughts_path = planner_thoughts_path
        self.network_logs_path = network_logs_path

    def add_test_case(
        self,
        scenario: str,
        feature: str,
        json_data: Dict[str, Any],
        execution_time: float,
        cost_metric: Dict[str, Any],
    ) -> None:
        """
        Add a test case to the test suite.

        Args:
            scenario (str): The scenario name.
            feature (str): The feature name.
            json_data (Dict[str, Any]): The JSON data containing test details.
            execution_time (float): The execution time of the test case.
            cost_metric (Dict[str, Any]): The cost metrics associated with the test case.
        """
        test_case = TestCase(name=scenario, classname=feature, time=execution_time)

        final_response = json_data.get("final_response")
        terminate = json_data.get("terminate", "yes")
        assert_summary = json_data.get("assert_summary", "Runtime Failure")
        is_assert = json_data.get("is_assert", False)
        is_passed = json_data.get("is_passed", False)

        if is_assert:
            add_event(
                EventType.ASSERT,
                EventData(detail=f"Assertion with result: {is_passed}"),
            )
            if not is_passed:
                test_case.result = Failure(message=assert_summary)
            else:
                test_case.system_out = assert_summary
        else:
            if not is_passed:
                test_case.result = Failure(message=str(assert_summary or final_response))

        opt_list = []
        test_props = Properties()
        test_case.append(test_props)
        test_props.add_property(Property(name="Terminate", value=str(terminate)))
        test_props.add_property(Property(name="Feature File", value=str(self.feature_file_path)))
        test_props.add_property(Property(name="Output File", value=str(self.output_file_path)))
        test_props.add_property(Property(name="Proofs Video", value=str(self.proofs_video_path)))
        test_props.add_property(
            Property(
                name="Proofs Base Folder, includes screenshots, recording, netwrok logs, api logs, sec logs, accessibility logs",
                value=str(self.proofs_path),
            )
        )
        test_props.add_property(Property(name="Proofs Screenshot", value=str(self.proofs_screenshot_path)))
        test_props.add_property(Property(name="Network Logs", value=str(self.network_logs_path)))
        test_props.add_property(Property(name="Agents Internal Logs", value=str(self.logs_path)))
        test_props.add_property(Property(name="Planner Thoughts", value=str(self.planner_thoughts_path)))
        opt_list.append(f"Final Response: {final_response}")

        for key, value in json_data.items():
            if key not in [
                "is_assert",
                "is_passed",
                "final_response",
                "assert_summary",
            ]:
                prop = Property(name=key, value=str(value))
                test_props.add_property(prop)

        res_d = flatten_dict(cost_metric)
        res = [f"{k}: {v}" for k, v in res_d.items()]
        opt_list.append(", ".join(res))

        for opt_item in opt_list:
            test_case.append(SystemOut(opt_item))

        for agent, metrics in cost_metric.items():
            if agent == "total_cost":
                self.total_execution_cost += float(metrics)
                continue
            for model, data in metrics.items():
                parent_key = f"{agent}.{model}"
                for k, v in data.items():
                    prop = Property(name=f"{parent_key}.{k}", value=str(v))
                    test_props.add_property(prop)
                    if k == "total_tokens":
                        self.total_token_used += int(v)

        self.total_time += float(execution_time)
        self.suite.add_testcase(test_case)

    async def write_xml(self, output_file: str) -> None:
        """
        Write the test suite to a JUnit XML file asynchronously.

        Args:
            output_file (str): The path to the output XML file.
        """
        self.suite.add_property("total_execution_cost", str(self.total_execution_cost))
        self.suite.add_property("total_token_used", str(self.total_token_used))
        self.suite.time = self.total_time
        self.suite.timestamp = datetime.datetime.now().isoformat()
        self.suite.update_statistics()

        xml = JUnitXml()
        xml.add_testsuite(self.suite)

        # Write to temp file first
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            xml.write(tmp.name)
            tmp_path = tmp.name

        # Read from temp and write to final destination asynchronously
        async with aiofiles.open(tmp_path, "r") as src, aiofiles.open(output_file, "w") as dest:
            content = await src.read()
            await dest.write(content)

        # Clean up temp file
        os.unlink(tmp_path)

    @staticmethod
    async def merge_junit_xml(files: List[str], output_file: str) -> None:
        """
        Merge multiple JUnit XML files into one asynchronously.

        Args:
            files (List[str]): List of file paths to JUnit XML files.
            output_file (str): Path to the output merged JUnit XML file.
        """
        merged_xml = JUnitXml()
        suite_dict: Dict[str, TestSuite] = {}

        for file in files:
            xml = JUnitXml.fromfile(file)
            for suite in xml:
                suite_name = suite.name
                if suite_name in suite_dict:
                    existing_suite = suite_dict[suite_name]
                    for testcase in suite:
                        existing_suite.add_testcase(testcase)
                    existing_suite.time = float(existing_suite.time or 0.0) + float(suite.time or 0.0)

                    for prop in suite.properties():
                        is_existing_prop = False
                        for existing_prop in existing_suite.properties():
                            if existing_prop.name.strip() == prop.name.strip():
                                try:
                                    existing_value = float(existing_prop.value)
                                    new_value = float(prop.value)
                                    existing_prop.value = existing_value + new_value
                                except ValueError:
                                    pass
                                is_existing_prop = True

                        existing_suite.update_statistics()

                        if not is_existing_prop:
                            existing_suite.add_property(name=prop.name, value=prop.value)
                else:
                    suite_dict[suite_name] = suite

            # delete the files of individual test cases
            if os.path.exists(file) and get_global_conf().get_mode() not in ["debug"]:
                os.remove(file)

        for suite in suite_dict.values():
            merged_xml.add_testsuite(suite)

        # Write merged XML to temp file first
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            merged_xml.write(tmp.name)
            tmp_path = tmp.name

        # Read from temp and write to final destination asynchronously
        async with aiofiles.open(tmp_path, "r") as src, aiofiles.open(output_file, "w") as dest:
            content = await src.read()
            await dest.write(content)

        # Clean up temp file
        os.unlink(tmp_path)

        # Delete individual test files if not in debug mode
        if get_global_conf().get_mode() not in ["debug"]:
            for file in files:
                if os.path.exists(file):
                    os.remove(file)


async def build_junit_xml(
    json_data: Dict[str, Any],
    execution_time: float,
    cost_metric: Dict[str, Any],
    feature: str,
    scenario: str,
    feature_file_path: str = None,
    output_file_path: str = None,
    proofs_path: str = None,
    proofs_video_path: str = None,
    proofs_screenshot_path: str = None,
    logs_path: str = None,
    network_logs_path: str = None,
    planner_thoughts_path: str = None,
) -> str:
    """
    Build a JUnit XML file from test data.

    Args:
        json_data (Dict[str, Any]): The JSON data containing test details.
        execution_time (float): The execution time of the test case.
        cost_metric (Dict[str, Any]): The cost metrics associated with the test case.
        feature (str): The feature name.
        scenario (str): The scenario name.

    Returns:
        str: The path to the generated JUnit XML file.
    """
    feature_r = feature.replace(" ", "_").replace(":", "")
    scenario_r = scenario.replace(" ", "_").replace(":", "")

    file_path = f"{get_global_conf().get_junit_xml_base_path()}/{feature_r}_{scenario_r}_results.xml"

    generator = JUnitXMLGenerator(
        feature,
        feature_file_path,
        file_path or output_file_path,
        proofs_path,
        proofs_video_path,
        proofs_screenshot_path,
        network_logs_path,
        logs_path,
        planner_thoughts_path,
    )
    generator.add_test_case(scenario, feature, json_data, execution_time, cost_metric)
    await generator.write_xml(file_path)
    return file_path


def run_test() -> None:
    """
    Run a test and generate JUnit XML files.
    """
    json_data = {
        "terminate": "yes",
        "final_response": "The number of bikes displayed when the Max price is set to 1600 is 2, which matches the expected result.",
        "is_assert": False,
    }

    execution_time = 30.04
    cost_metric = {
        "usage_including_cached_inference": {
            "total_cost": 0.12923500000000002,
            "gpt-4o-2024-08-06": {
                "cost": 0.12923500000000002,
                "prompt_tokens": 45362,
                "completion_tokens": 1583,
                "total_tokens": 46945,
            },
        },
        "usage_excluding_cached_inference": {
            "total_cost": 0.11612000000000003,
            "gpt-4o-2024-08-06": {
                "cost": 0.11612000000000003,
                "prompt_tokens": 41220,
                "completion_tokens": 1307,
                "total_tokens": 42527,
            },
        },
    }

    feature = "Feature: Filter Bikes by Max Price"
    scenario = "Scenario: User filters bikes with Max Price of 1600"

    f1_path = build_junit_xml(
        json_data,
        execution_time,
        cost_metric,
        feature,
        scenario,
    )

    json_data_fail = {
        "terminate": "yes",
        "final_response": "No bikes are displayed when the Max price is set to 1600, which does not match the expected result.",
        "is_assert": True,
    }
    f2_path = build_junit_xml(
        json_data_fail,
        execution_time,
        cost_metric,
        feature + "1",
        scenario + "1",
    )

    f2_path = build_junit_xml(json_data_fail, execution_time, cost_metric, feature + "1", scenario + "1")

    JUnitXMLGenerator.merge_junit_xml(
        [f1_path, f2_path],
        f"{get_global_conf().get_junit_xml_base_path()}/final_results.xml",
    )


# # Example usage
# run_test()
