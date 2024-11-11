import datetime
import os
from typing import Any, Dict, List

from testzeus_hercules.config import MODE, get_junit_xml_base_path
from testzeus_hercules.telemetry import EventData, EventType, add_event
from junitparser import Failure, JUnitXml, Property, TestCase, TestSuite
from junitparser.junitparser import SystemOut

junit_xml_base_path = get_junit_xml_base_path()


def flatten_dict(
    d: Dict[str, Any], parent_key: str = "", sep: str = "."
) -> Dict[str, Any]:
    """
    Flatten a nested dictionary.

    Args:
        d (Dict[str, Any]): The dictionary to flatten.
        parent_key (str): The base key to use for the flattened keys.
        sep (str): The separator to use between keys.

    Returns:
        Dict[str, Any]: The flattened dictionary.
    """
    items = []
    for key, value in d.items():
        new_key = f"{parent_key}{sep}{key}" if parent_key else key
        if isinstance(value, dict):
            items.extend(flatten_dict(value, new_key, sep=sep).items())
        else:
            items.append((new_key, value))
    return dict(items)


class JUnitXMLGenerator:
    def __init__(self, suite_name: str) -> None:
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
        assert_summary = json_data.get("assert_summary", "Runtime Failure")
        is_assert = json_data.get("is_assert", False)
        is_passed = json_data.get("is_passed", True)

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
                test_case.result = Failure(message=assert_summary or final_response)

        opt_list = []
        opt_list.append(final_response)

        for key, value in json_data.items():
            if key not in [
                "is_assert",
                "is_passed",
                "final_response",
                "assert_summary",
            ]:
                prop = Property(name=key, value=str(value))
                test_case.append(prop)

        res_d = flatten_dict(cost_metric)
        res = [f"{k}: {v}" for k, v in res_d.items()]
        opt_list.append(", ".join(res))

        for opt_item in opt_list:
            test_case.append(SystemOut(opt_item))

        for usage_type in [
            "usage_including_cached_inference",
            "usage_excluding_cached_inference",
        ]:
            for key, val in cost_metric.get(usage_type, {}).items():
                parent_key = f"{usage_type}.{key}"
                if isinstance(val, dict):
                    for k, v in val.items():
                        prop = Property(name=f"{parent_key}.{k}", value=str(v))
                        test_case.append(prop)
                        if k == "total_cost":
                            self.total_execution_cost += float(v)
                        elif k == "total_tokens":
                            self.total_token_used += int(v)

            total_cost = cost_metric.get(usage_type, {}).get("total_cost", 0.0)
            self.total_execution_cost += float(total_cost)
            total_tokens = cost_metric.get(usage_type, {}).get("total_tokens", 0)
            self.total_token_used += int(total_tokens)

        total_cost = cost_metric.get("usage_including_cached_inference", {}).get(
            "total_cost", 0.0
        )
        self.total_execution_cost += float(total_cost)
        gpt_data = cost_metric.get("usage_including_cached_inference", {})
        for key in gpt_data:
            if key != "total_cost":
                total_tokens = gpt_data[key].get("total_tokens", 0)
                self.total_token_used += int(total_tokens)

        self.total_time += float(execution_time)
        self.suite.add_testcase(test_case)

    def write_xml(self, output_file: str) -> None:
        """
        Write the test suite to a JUnit XML file.

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
        # print(f"Writing JUnit XML to: {output_file}")
        xml.write(output_file, pretty=True)

    @staticmethod
    def merge_junit_xml(files: List[str], output_file: str) -> None:
        """
        Merge multiple JUnit XML files into one based on testsuite name.

        Args:
            files (List[str]): List of file paths to JUnit XML files.
            output_file (str): Path to the output merged JUnit XML file.
        """
        merged_xml = JUnitXml()
        suite_dict = {}

        for file in files:
            xml = JUnitXml.fromfile(file)
            for suite in xml:
                suite_name = suite.name
                if suite_name in suite_dict:
                    existing_suite = suite_dict[suite_name]
                    for testcase in suite:
                        existing_suite.add_testcase(testcase)
                    existing_suite.time = float(existing_suite.time or 0.0) + float(
                        suite.time or 0.0
                    )

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
                            existing_suite.add_property(
                                name=prop.name, value=prop.value
                            )
                else:
                    suite_dict[suite_name] = suite

            # delete the files of individual test cases
            if os.path.exists(file) and MODE not in ["debug"]:
                os.remove(file)

        for suite in suite_dict.values():
            merged_xml.add_testsuite(suite)

        merged_xml.write(output_file)


def build_junit_xml(
    json_data: Dict[str, Any],
    execution_time: float,
    cost_metric: Dict[str, Any],
    feature: str,
    scenario: str,
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
    generator = JUnitXMLGenerator(feature)
    generator.add_test_case(scenario, feature, json_data, execution_time, cost_metric)
    feature = feature.replace(" ", "_").replace(":", "")
    scenario = scenario.replace(" ", "_").replace(":", "")
    file_path = f"{junit_xml_base_path}/{feature}_{scenario}_results.xml"
    generator.write_xml(file_path)
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

    f1_path = build_junit_xml(json_data, execution_time, cost_metric, feature, scenario)

    json_data_fail = {
        "terminate": "yes",
        "final_response": "No bikes are displayed when the Max price is set to 1600, which does not match the expected result.",
        "is_assert": True,
        "assert_summary": "EXPECTED RESULT: The number of bikes displayed should be 2. ACTUAL RESULT: The number of bikes displayed is 0.",
        "is_passed": False,
    }

    f2_path = build_junit_xml(
        json_data_fail, execution_time, cost_metric, feature + "1", scenario + "1"
    )

    JUnitXMLGenerator.merge_junit_xml(
        [f1_path, f2_path], f"{junit_xml_base_path}/final_results.xml"
    )


# # Example usage
# run_test()
