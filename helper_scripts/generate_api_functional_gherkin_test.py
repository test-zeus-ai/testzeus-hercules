#!/usr/bin/env python

# how to run
# python helper_scripts/generate_api_functional_gherkin_test.py tests/test_features/ApiTesting/api_spec.yml --output=helper_scripts/output --model=gpt-4o --number_of_testcase=50

import os
import sys
import argparse
from openai import OpenAI
from typing import List


def read_openapi_spec(file_path: str) -> str:
    """
    Reads the OpenAPI specification from a file.

    Args:
        file_path (str): The path to the OpenAPI spec file.

    Returns:
        str: The content of the OpenAPI spec file.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    return content


def prepare_prompt(p2: str, openapi_spec: str) -> str:
    """
    Prepares the prompt for the OpenAI API.

    Args:
        p2 (str): The initial part of the prompt.
        openapi_spec (str): The OpenAPI specification.

    Returns:
        str: The complete prompt.
    """
    prompt = f"{p2}\n\nOpenAPI Specification:\n{openapi_spec}"
    return prompt


def generate_test_cases(prompt: str, model: str) -> str:
    """
    Generates test cases using the OpenAI API.

    Args:
        prompt (str): The prompt to send to the OpenAI API.
        model (str): The model to use for the OpenAI API.

    Returns:
        str: The generated test cases.
    """
    client = OpenAI()
    if "o1" in model:
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
    else:
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )

    response = completion.choices[0].message.content
    print(f"Response from OpenAI API: {response}")
    return response


def ensure_output_folder(output_folder: str) -> None:
    """
    Ensures that the output folder exists.

    Args:
        output_folder (str): The path to the output folder.
    """
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)


def split_features(response_text: str) -> List[str]:
    """
    Splits the response text into individual features.

    Args:
        response_text (str): The response text containing multiple features.

    Returns:
        List[str]: A list of individual features.
    """
    features = []
    current_feature = ""
    lines = response_text.split("\n")
    for line in lines:
        if line.strip().startswith("Feature:"):
            if current_feature:
                features.append(current_feature.strip())
                current_feature = ""
        current_feature += line + "\n"
    if current_feature.strip():
        features.append(current_feature.strip())
    return features


def get_base_name(file_path: str) -> str:
    """
    Gets the base name of a file without the extension.

    Args:
        file_path (str): The path to the file.

    Returns:
        str: The base name of the file.
    """
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    return base_name


def create_output_subfolder(output_folder: str, base_name: str) -> str:
    """
    Creates a subfolder in the output folder.

    Args:
        output_folder (str): The path to the output folder.
        base_name (str): The base name for the subfolder.

    Returns:
        str: The path to the created subfolder.
    """
    subfolder_path = os.path.join(output_folder, base_name)
    if not os.path.exists(subfolder_path):
        os.makedirs(subfolder_path)
    return subfolder_path


def write_feature_files(features: List[str], subfolder_path: str) -> None:
    """
    Writes the feature files to the subfolder.

    Args:
        features (List[str]): A list of features to write.
        subfolder_path (str): The path to the subfolder.
    """
    for idx, feature in enumerate(features):
        # Extract the feature name if possible
        first_line = feature.split("\n")[0]
        if first_line.startswith("Feature:"):
            feature_name = first_line[len("Feature:") :].strip().replace(" ", "_")
            file_name = f"{feature_name}.feature"
        else:
            file_name = f"feature_{idx+1}.feature"
        file_path = os.path.join(subfolder_path, file_name)
        with open(file_path, "w", encoding="utf-8") as f:
            feature = feature.replace("```", "").replace("gherkin", "")
            f.write(feature)


def main() -> None:
    """
    The main function to generate Gherkin test cases from OpenAPI spec files.
    """
    p2 = """Analyse (thoroughly examine and break down) the given API specification to produce detailed Gherkin test cases following the provided testing plan. Focus on creating positive, negative, and business-as-usual scenarios. Ensure your test cases validate data correctness, data types, null value handling, and adherence to the API specification. Cover all testing areas: functional, postive, negative, error handling, and integration. Be extremely direct, clear, DETAILED and corrective in your approach. Your final output should be the strict detailed Gherkin test cases, ready for execution. generate a big spread of testcases at least {number_of_testcase}. for each combination of datatypes, fields and null values generate the testcases
    NEVER PUT ### in the testcases. Always write the testcases in the Gherkin format. follow the example to generate output
    ONLY RETURN THE GHERKIN FILES, NO HEADING OR EXPLINATION NEEDED.
    example output:
    
    Feature: feature details 1
        Scenario: Scenario_details
            Given ...
            When ...
            Then ...
        Scenario: Scenario_details
            Given ...
            When ...
            Then ...
    Feature: feature details 2
        Scenario: Scenario_details
            Given ...
            When ...
            Then ...
    Feature: feature details 3
        Scenario: Scenario_details
            Given ...
            When ...
            And ...
            Then ...
            And ...
    """

    parser = argparse.ArgumentParser(
        description="Generate Gherkin test cases from OpenAPI spec files."
    )
    parser.add_argument(
        "input_files",
        metavar="input_files",
        type=str,
        nargs="+",
        help="One or more OpenAPI spec files (YAML or JSON).",
    )
    parser.add_argument(
        "--output",
        metavar="output",
        type=str,
        required=True,
        help="Output folder path where feature files will be generated.",
    )
    parser.add_argument(
        "--model",
        metavar="model",
        type=str,
        default="o1-preview",
        help="The model to use for the OpenAI API (default: o1-preview).",
    )
    parser.add_argument(
        "--number_of_testcase",
        metavar="number_of_testcase",
        type=int,
        default=100,
        help="The number of test cases to generate (default: 100).",
    )
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable not set.")
        sys.exit(1)

    ensure_output_folder(args.output)

    for file_path in args.input_files:
        print(f"Processing file: {file_path}")
        openapi_spec = read_openapi_spec(file_path)
        p2 = p2.replace("{number_of_testcase}", str(args.number_of_testcase))
        prompt = prepare_prompt(p2, openapi_spec)
        try:
            test_cases = generate_test_cases(prompt, args.model)
        except Exception as e:
            print(f"Error generating test cases for {file_path}: {e}")
            continue
        features = split_features(test_cases)
        base_name = get_base_name(file_path)
        subfolder_path = create_output_subfolder(args.output, base_name)
        write_feature_files(features, subfolder_path)
        print(f"Generated {len(features)} feature files in {subfolder_path}")


if __name__ == "__main__":
    main()
