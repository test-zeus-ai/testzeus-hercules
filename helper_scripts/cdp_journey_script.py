#!/usr/bin/env python

#python vir_migration_script.py raw_jsons/test1.json --output=output_txt --model=gpt-4o


import os
import sys
import json
import argparse
from typing import List
from openai import OpenAI

###############################################################################
#                   1) Use OpenAI to Generate Gherkin
###############################################################################

def prepare_prompt(p2: str, ui_spec: str) -> str:
    """
    Combine instructions + the UI spec into the final prompt for OpenAI.
    """
    prompt = f"{p2}\n\nUI Specification:\n{ui_spec}"
    return prompt


def generate_test_cases(prompt: str, model: str) -> str:
    """
    Send the prompt to OpenAI Chat Completions and return the response text.
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
    return response.strip()


###############################################################################
#               2) Split Gherkin Response & Write .feature Files
###############################################################################

def ensure_output_folder(output_folder: str) -> None:
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)


def split_features(response_text: str) -> List[str]:
    """
    Given a large Gherkin response with multiple Feature blocks,
    split them into separate text items.
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


def clean_test_data(test_data: str) -> str:
    """
    Clean the test data by removing markdown formatting
    """
    cleaned = test_data.replace("```plaintext", "").replace("```", "").strip()
    return cleaned


def write_feature_files(features: List[str], output_folder: str) -> None:
    """
    For each feature block, create a .feature file named after the Feature: line.
    """
    for idx, feature_text in enumerate(features):
        if feature_text:
            # Attempt to glean a file name from the first line
            first_line = feature_text.split("\n")[0].strip()
            if first_line.startswith("Feature:"):
                feature_name = first_line[len("Feature:"):].strip()
                safe_file_name = feature_name.replace(" ", "_")
                file_name = f"{safe_file_name}.feature"
            else:
                file_name = f"feature_{idx+1}.feature"

            # Remove any triple-backtick formatting or "gherkin" tokens if present
            feature_text = feature_text.replace("```", "").replace("gherkin", "")

            out_path = os.path.join(output_folder, file_name)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(feature_text)
    print(f"Wrote feature file(s) to {output_folder}")


###############################################################################
#                                   MAIN
###############################################################################

def main() -> None:
    """
    Single script that:
     1) Reads a JSON file in Virtuoso-like format (journeys/cases/steps).
     2) Creates a text-based UI spec from that JSON.
     3) Counts how many scenarios are in that UI spec.
     4) Calls OpenAI to generate Gherkin testcases, requesting the same # of testcases as scenarios.
     5) Saves the resulting Gherkin features to .feature files in an output folder.
    """
    parser = argparse.ArgumentParser(description="JSON-in -> Gherkin-out script using OpenAI.")
    parser.add_argument("input_json", type=str, help="Path to the JSON file (Journey-like).")
    parser.add_argument("--output", type=str, required=True, help="Output folder for .feature files.")
    parser.add_argument("--model", type=str, default="gpt-4o", help="OpenAI model name (default=gpt-4o).")
    parser.add_argument(
        "--number_of_testcase",
        metavar="number_of_testcase",
        type=int,
        default=10,
        help="The number of test cases to generate (default: 10).",
    )
    args = parser.parse_args()
    
    number_of_testcase = args.number_of_testcase

    # 1) Read the JSON data
    if not os.path.isfile(args.input_json):
        print(f"Error: {args.input_json} does not exist.")
        sys.exit(1)
    
    journey_data = {}
    with open(args.input_json, "r", encoding="utf-8") as f:
        journey_data = json.load(f)


    # 4) Prepare the instructions for OpenAI
    prompt_template = """You are an AI agent that receives:
1) A JSON file generated by Chrome Recorder, representing an end-to-end user journey on a website.
2) A numerical value indicating how many distinct Gherkin scenarios (test cases) to generate.

### Requirements

1. **Parse** the JSON journey and identify the primary flow and any potential variations/branches.  
2. **Create** Gherkin features with:
   - A **Background** containing all common, repeated steps (e.g., launching the site, navigating, etc.).
   - **N** scenarios (where N is the number of test cases requested) to reflect different branching flows or user variations (valid vs invalid data, optional steps, alternative actions, etc.).
   - Focus on each aspect of the flow while building test cases (e.g., selection, categories, checkout, etc.).
3. In the **Gherkin steps**, **do not use programmatic locators** (IDs, XPaths, CSS selectors, etc.). Instead, use **human-readable** names or descriptions (e.g., "When I click on the 'Login' button" or "Then I should see 'Welcome' on the page").
4. **Parametrize only** the actual user inputs (e.g., text typed into fields, pincode, username, etc.) as uppercase placeholders such as `PARAM_USERNAME` or `PARAM_PINCODE`. Do **not** put these placeholders in quotes.
5. Produce a **test data file** in plain text with `key: value` pairs for each parameterized user input (e.g., `PARAM_USERNAME: myUser`).
6. **Output only**:
   - The **Gherkin** specification (with Background + N Scenarios).
   - The **test data file**.
   - Saparate gherkin and test data file with "@@@@"
7. Do **not** include any summaries, explanations, or commentary beyond the Gherkin and test data file.

### Example Input Format

{
  "title": "SampleJourney",
  "steps": [
    {
      "type": "navigate",
      "url": "https://example.com"
    },
    {
      "type": "click",
      "target": "main",
      "selectors": [
        ["#login-button"]
      ],
      "offsetY": 20,
      "offsetX": 50
    },
    {
      "type": "change",
      "value": "myUser",
      "selectors": [
        ["#username"]
      ]
    }
    ...
  ]
}

### Actual User Inputs
- JSON file
###
{journey_data}
###
- Number of test scenarios to generate: {number_of_testcase}
"""

    prompt_instructions = prompt_template.replace("{number_of_testcase}", str(number_of_testcase))
    final_prompt = prepare_prompt(prompt_instructions, json.dumps(journey_data, indent=2))

    # Ensure we have an API key set
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable not set.")
        sys.exit(1)

    # 5) Generate the Gherkin testcases
    try:
        response_text = generate_test_cases(final_prompt, args.model)
        # Split response into features and test data
        parts = response_text.split("@@@@")
        feature_text = parts[0]
        test_data = parts[1] if len(parts) > 1 else ""

        # Process features
        gherkin_features = split_features(feature_text)
        ensure_output_folder(args.output)
        write_feature_files(gherkin_features, args.output)

        # Process test data if present
        if test_data:
            cleaned_test_data = clean_test_data(test_data)
            test_data_path = os.path.join(args.output, "test_data.txt")
            with open(test_data_path, "w", encoding="utf-8") as f:
                f.write(cleaned_test_data)
            print(f"Wrote test data file to {test_data_path}")

    except Exception as e:
        print("Error during OpenAI generation:", e)
        sys.exit(1)


if __name__ == "__main__":
    main()