#!/usr/bin/env python

# how to run
# python helper_scripts/generate_api_security_gherkin_test.py tests/test_features/ApiTesting/api_spec.yml --output=helper_scripts/output --model=gpt-4o

import os
import sys
import argparse
from typing import List
from openai import OpenAI


def read_openapi_spec(file_path: str) -> str:
    """Reads the OpenAPI specification from a file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def prepare_prompt(file_paths: List[str], topics: dict) -> str:
    """Prepares the prompt for generating security test cases."""
    prompt = (
        "Analyse the provided OpenAPI specifications to generate detailed Gherkin security test cases. "
        "Focus on the following topics for https://warangler.in:\n"
    )
    for topic, description in topics.items():
        prompt += f"- {topic}: {description}\n"

    prompt += "\nOpenAPI Specifications:\n"
    for file_path in file_paths:
        prompt += f"Specification from {file_path}:\n"
        try:
            spec_content = ""  # read_openapi_spec(file_path)
            prompt += spec_content + "\n\n"
        except Exception as e:
            prompt += f"(Error reading {file_path}: {e})\n\n"

    prompt += (
        "Your output should include Gherkin test cases that validate the security of the API. "
        "Focus on each provided topic. Ensure that the scenarios test for vulnerabilities, "
        "configuration weaknesses, and proper handling of sensitive data. Ensure that test cases "
        "are specific to the given topics, mentioning that there should not be any issues found. "
        "All test cases should mention the passed api spec path.\n"
        """NEVER PUT ### in the testcases. Always write the testcases in the Gherkin format. follow the example to generate output
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
                And ..."""
    )
    return prompt


def generate_test_cases(prompt: str, model: str) -> str:
    """Generates test cases using the OpenAI API."""
    client = OpenAI()
    completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return completion.choices[0].message.content


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
    """Main function to generate security test cases."""
    topics = {
        "cve": "Common Vulnerabilities and Exposures, a standardized list of publicly disclosed security vulnerabilities.",
        "panel": "Admin panels or dashboards, often targeted in penetration testing to exploit misconfigurations or weak authentication.",
        "wordpress": "A popular CMS frequently targeted due to its widespread use and vulnerable plugins/themes.",
        "exposure": "The accidental or intentional disclosure of sensitive information, key in security assessments.",
        "xss": "Cross-Site Scripting, a vulnerability that allows attackers to inject malicious scripts into web pages.",
        "osint": "Open Source Intelligence, gathering information from public sources for security analysis.",
        "tech": "Technology stacks or implementations, often assessed for vulnerabilities.",
        "misconfig": "Misconfigurations caused by improper setup of systems or applications, a common vulnerability.",
        "lfi": "Local File Inclusion, a vulnerability allowing attackers to include and execute server files.",
        "rce": "Remote Code Execution, enabling attackers to execute arbitrary code on a target system.",
        "edb": "Exploit-DB, a repository of publicly available exploits used by security researchers.",
        "packetstorm": "Packet Storm Security, offering tools, advisories, and exploits for security research.",
        "devops": "Development and operations pipelines, often assessed for vulnerabilities in tools and workflows.",
        "sqli": "SQL Injection, allowing attackers to manipulate SQL queries and access sensitive data.",
        "cloud": "Cloud infrastructure requiring measures to protect data from breaches and attacks.",
        "unauth": "Unauthorized access, where attackers gain system access without proper credentials.",
        "authenticated": "Authenticated testing, performed with valid credentials to assess internal security.",
        "intrusive": "Intrusive methods actively exploiting vulnerabilities to determine risk extent.",
    }

    parser = argparse.ArgumentParser(
        description="Generate security-focused Gherkin test cases for multiple OpenAPI specs."
    )
    parser.add_argument(
        "input_files",
        metavar="input_files",
        type=str,
        nargs="+",
        help="List of OpenAPI spec files (YAML or JSON).",
    )
    parser.add_argument(
        "--output",
        metavar="output",
        type=str,
        required=True,
        help="Output folder path for generated test cases.",
    )
    parser.add_argument(
        "--model",
        metavar="model",
        type=str,
        default="gpt-4o",
        help="The model to use for the OpenAI API.",
    )
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable not set.")
        sys.exit(1)

    ensure_output_folder(args.output)

    for file_path in args.input_files:
        try:
            prompt = prepare_prompt(args.input_files, topics)
            test_cases = generate_test_cases(prompt, args.model)
            features = split_features(test_cases)
            base_name = get_base_name(file_path)
            subfolder_path = create_output_subfolder(args.output, base_name)
            write_feature_files(features, subfolder_path)
        except Exception as e:
            print(f"Error generating test cases: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
