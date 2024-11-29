from typing import Dict, List

import pandas as pd


def parse_test_cases(file_path: str, sheet_name: str = "Sample Test Case") -> List[Dict[str, str]]:
    """
    Parses test cases from an Excel file following the specific template.

    Parameters:
    file_path (str): Path to the Excel file.
    sheet_name (str): Sheet name containing the test cases (default: "Sample Test Case").

    Returns:
    List[Dict[str, str]]: A list of dictionaries, each representing a test case.
    """

    # Load the Excel file and read the relevant sheet
    df = pd.read_excel(file_path, sheet_name=sheet_name, skiprows=9)  # Skip metadata rows
    import ipdb

    ipdb.set_trace()

    # drop column 1
    df = df.drop(df.columns[0], axis=1)

    # Rename columns to standard names for easier processing
    df.columns = [
        "TEST_CASE_ID",
        "TEST_SCENARIO",
        "TEST_CASE",
        "PRE_CONDITION",
        "TEST_STEPS",
        "TEST_DATA",
        "EXPECTED_RESULT",
        "POST_CONDITION",
        "ACTUAL_RESULT",
        "STATUS",
    ]

    # Drop empty rows and reset the index
    df = df.dropna(how="all").reset_index(drop=True)

    # Convert each row into a dictionary representing a test case
    test_cases = df.to_dict(orient="records")
    return test_cases


def serialize_test_case(test_case: Dict[str, str]) -> str:
    """
    Serializes a test case dictionary into a single line string with fields joined.

    Parameters:
    test_case (Dict[str, str]): A dictionary representing a test case.

    Returns:
    str: A serialized string representation of the test case.
    """
    serialized_parts = [
        f"TEST CASE ID: {test_case.get('TEST_CASE_ID', '')}",
        f"TEST SCENARIO: {test_case.get('TEST_SCENARIO', '')}",
        f"PRE-CONDITION: {test_case.get('PRE_CONDITION', '')}",
        f"TEST STEPS: {test_case.get('TEST_STEPS', '')}",
        f"TEST DATA: {test_case.get('TEST_DATA', '')}",
        f"EXPECTED RESULT: {test_case.get('EXPECTED_RESULT', '')}",
        f"POST-CONDITION: {test_case.get('POST_CONDITION', '')}",
        f"ACTUAL RESULT: {test_case.get('ACTUAL_RESULT', '')}",
        f"STATUS: {test_case.get('STATUS', '')}",
    ]
    return " #newline# ".join(serialized_parts)


def process_and_serialize(file_path: str, sheet_name: str = "Sample Test Case") -> List[str]:
    """
    Processes an Excel file and serializes all test cases into line-by-line joined strings.

    Parameters:
    file_path (str): Path to the Excel file.
    sheet_name (str): Sheet name containing the test cases (default: "Sample Test Case").

    Returns:
    List[str]: A list of serialized test case strings.
    """
    test_cases = parse_test_cases(file_path, sheet_name)
    return [serialize_test_case(tc) for tc in test_cases]


# Example usage:
file_path = "/Users/shriyanshagnihotri/Downloads/test-case-template-03.xlsx"
serialized_test_cases = process_and_serialize(file_path)
serialized_test_cases[:2]  # Display the first two serialized test cases
