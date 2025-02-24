import datetime
import os
import tempfile
from typing import Dict, List, Optional

from junitparser import JUnitXml, TestCase, TestSuite, Property, Properties
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.logger import logger


class JUnitXMLGenerator:
    def __init__(
        self,
        suite_name: str = "TestZeus Hercules Test Suite",
        suite_package: str = "testzeus_hercules",
    ):
        """
        Initialize a JUnit XML generator.

        Args:
            suite_name (str): The name of the test suite.
            suite_package (str): The package name for the test suite.
        """
        self.suite = TestSuite(suite_name)
        self.suite_package = (
            suite_package  # Store as attribute since TestSuite doesn't have package
        )
        self.total_execution_cost = 0.0
        self.total_token_used = 0
        self.total_time = 0.0

    def add_test_case(
        self,
        name: str,
        classname: str,
        time: float,
        execution_cost: float,
        token_used: int,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
        status: str = "passed",
        failure_message: Optional[str] = None,
        failure_type: Optional[str] = None,
        skipped_message: Optional[str] = None,
    ) -> None:
        """
        Add a test case to the suite.

        Args:
            name (str): The name of the test case.
            classname (str): The class name for the test case.
            time (float): The execution time of the test case.
            execution_cost (float): The cost of executing the test case.
            token_used (int): The number of tokens used in the test case.
            stdout (str, optional): Standard output from the test case.
            stderr (str, optional): Standard error from the test case.
            status (str): The status of the test case (passed/failed/skipped).
            failure_message (str, optional): Message describing the failure.
            failure_type (str, optional): Type of failure.
            skipped_message (str, optional): Message explaining why the test was skipped.
        """
        case = TestCase(name)
        case.classname = classname
        case.time = time

        # Add stdout/stderr as properties since TestCase doesn't have these attributes
        props = Properties()
        if stdout:
            props.append(Property("stdout", stdout))
        if stderr:
            props.append(Property("stderr", stderr))

        if status == "failed" and failure_message:
            props.append(Property("failure", failure_message))
            if failure_type:
                props.append(Property("failure_type", failure_type))
        elif status == "skipped" and skipped_message:
            props.append(Property("skipped", skipped_message))

        props.append(Property("execution_cost", str(execution_cost)))
        props.append(Property("token_used", str(token_used)))

        case.append(props)
        self.suite.add_testcase(case)
        self.total_execution_cost += execution_cost
        self.total_token_used += token_used
        self.total_time += time

    def write_xml(self, output_file: str) -> None:
        """
        Write the test suite to a JUnit XML file.

        Args:
            output_file (str): The path to the output XML file.
        """
        props = Properties()
        props.append(Property("total_execution_cost", str(self.total_execution_cost)))
        props.append(Property("total_token_used", str(self.total_token_used)))
        props.append(Property("package", self.suite_package))
        self.suite.append(props)

        self.suite.time = self.total_time
        self.suite.timestamp = datetime.datetime.now().isoformat()
        self.suite.update_statistics()

        xml = JUnitXml()
        xml.add_testsuite(self.suite)

        # Write to temp file first
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            xml.write(tmp.name)
            tmp_path = tmp.name

        # Read from temp and write to final destination
        with open(tmp_path, "r") as src, open(output_file, "w") as dest:
            content = src.read()
            dest.write(content)

        # Clean up temp file
        os.unlink(tmp_path)

    @staticmethod
    def merge_junit_xml(files: List[str], output_file: str) -> None:
        """
        Merge multiple JUnit XML files into a single file.

        Args:
            files (List[str]): List of JUnit XML files to merge.
            output_file (str): Path to the output merged XML file.
        """
        merged_xml = JUnitXml()
        suite_dict: Dict[str, TestSuite] = {}

        for file in files:
            try:
                xml = JUnitXml.fromfile(file)
                for suite in xml:
                    if suite.name not in suite_dict:
                        suite_dict[suite.name] = suite
                    else:
                        existing_suite = suite_dict[suite.name]
                        for case in suite:
                            existing_suite.add_testcase(case)
                        # Update suite properties
                        for prop in suite.properties():
                            if prop.name == "total_execution_cost":
                                existing_cost = float(
                                    next(
                                        (
                                            p.value
                                            for p in existing_suite.properties()
                                            if p.name == "total_execution_cost"
                                        ),
                                        "0",
                                    )
                                )
                                new_cost = float(prop.value)
                                prop.value = str(existing_cost + new_cost)
                            elif prop.name == "total_token_used":
                                existing_tokens = int(
                                    next(
                                        (
                                            p.value
                                            for p in existing_suite.properties()
                                            if p.name == "total_token_used"
                                        ),
                                        "0",
                                    )
                                )
                                new_tokens = int(prop.value)
                                prop.value = str(existing_tokens + new_tokens)
                            existing_suite.append(
                                Property(name=prop.name, value=prop.value)
                            )
                        # Update time
                        existing_suite.time += suite.time or 0
                        existing_suite.update_statistics()
            except Exception as e:
                logger.error(f"Error processing file {file}: {e}")
                continue

        for suite in suite_dict.values():
            merged_xml.add_testsuite(suite)

        # Write merged XML to temp file first
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            merged_xml.write(tmp.name)
            tmp_path = tmp.name

        # Read from temp and write to final destination
        with open(tmp_path, "r") as src, open(output_file, "w") as dest:
            content = src.read()
            dest.write(content)

        # Clean up temp file
        os.unlink(tmp_path)

        # Delete individual test files if not in debug mode
        if get_global_conf().get_mode() not in ["debug"]:
            for file in files:
                if os.path.exists(file):
                    os.remove(file)
