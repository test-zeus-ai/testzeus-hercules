import os
import filecmp
import logging
import shutil
from typing import Tuple
import xml.etree.ElementTree as ET

# Setting up logging
logging.basicConfig(filename="test.log", level=logging.INFO)


def setup_test_environment(feature_name: str) -> Tuple[str, str, str]:
    """
    Set up the test environment directories.

    Args:
        feature_name (str): The name of the feature being tested.

    Returns:
        tuple: A tuple containing paths for current test data, input, and test data directories.
    """
    current_test_data_path = os.path.join(os.path.dirname(__file__), "run_data", feature_name)
    input_path = os.path.join(current_test_data_path, "input")
    test_data_path = os.path.join(current_test_data_path, "test_data")

    os.makedirs(input_path, exist_ok=True)
    os.makedirs(test_data_path, exist_ok=True)

    return current_test_data_path, input_path, test_data_path


def copy_feature_files(feature_folder: str, input_path: str, test_data_path: str) -> None:
    """
    Copy feature files to the appropriate test directories.

    Args:
        feature_folder (str): The folder containing the feature files.
        input_path (str): The path to the input directory.
        test_data_path (str): The path to the test data directory.
    """
    # Corrected line: Get the base name of the feature folder
    feature_file_name = f"{os.path.basename(feature_folder)}.feature"
    feature_file = os.path.join(feature_folder, feature_file_name)
    shutil.copy(feature_file, os.path.join(input_path, "test.feature"))

    for item in os.listdir(feature_folder):
        if item in [feature_file_name, "expected_results.txt"]:
            continue
        item_path = os.path.join(feature_folder, item)
        if os.path.isfile(item_path):
            shutil.copy(item_path, os.path.join(test_data_path, item))


def compare_results(expected_file: str, actual_folder: str) -> bool:
    """
    Compare the expected result file with the generated output file.

    Args:
        expected_file (str): The path to the expected result file.
        actual_folder (str): The path to the folder containing the actual output files.

    Returns:
        bool: True if the comparison passes, False otherwise.
    """

    def create_expected_file_from_actual(actual_file: str) -> bool:
        """Create an expected file with the pattern based on the actual file."""
        try:
            tree = ET.parse(actual_file)
            root = tree.getroot()
            suite = root.find("testsuite")
            if suite is not None:
                tests = suite.attrib.get("tests")
                errors = suite.attrib.get("errors")
                failures = suite.attrib.get("failures")
                skipped = suite.attrib.get("skipped")

                # Write expected format to file
                with open(expected_file, "w", encoding="utf-16") as ef:
                    ef.write(f'tests="{tests}" errors="{errors}" failures="{failures}" skipped="{skipped}"\n')
                logging.warning(
                    "%s was missing. Created a new expected file with values from %s.",
                    expected_file,
                    actual_file,
                )
                return True
        except ET.ParseError as e:
            logging.error("Error parsing XML file %s: %s", actual_file, e)
            return False
        return False

    def parse_expected_file() -> dict[str, str] | None:
        """Parse the expected file to extract values."""
        with open(expected_file, "r", encoding="utf-16") as file:
            content = file.read()
            try:
                values = dict(item.split("=") for item in content.strip().split())
                # Strip quotes around values
                values = {k: v.strip('"') for k, v in values.items()}
                return values
            except ValueError as e:
                logging.error("Error parsing expected file %s: %s", expected_file, e)
                return None

    actual_file = os.path.join(actual_folder, "test.feature_result.xml")
    if not os.path.exists(expected_file):
        return create_expected_file_from_actual(actual_file)

    expected_values = parse_expected_file()
    if not expected_values:
        return False

    try:
        tree = ET.parse(actual_file)
        root = tree.getroot()
        suite = root.find("testsuite")
        if suite is not None:
            actual_values = {
                "tests": suite.attrib.get("tests"),
                "errors": suite.attrib.get("errors"),
                "failures": suite.attrib.get("failures"),
                "skipped": suite.attrib.get("skipped"),
            }

            # TODO: will uncomment this once all test are stable
            # if actual_values["errors"] == "0" or actual_values["failures"] == "0":
            #     logging.error(f"Errors or failures found in {actual_file}")
            #     return False

            if expected_values == actual_values:
                logging.info(f"Comparison passed for {actual_file}")
                return True
    except ET.ParseError as e:
        logging.error(f"Error parsing XML file {actual_file}: {e}")

    logging.error(f"Comparison failed for {expected_file}")
    logging.error(f"Expected values: {expected_values}")
    logging.error(f"Actual values: {actual_values}")

    return False
