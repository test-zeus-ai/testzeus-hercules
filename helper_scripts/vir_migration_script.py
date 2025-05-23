#!/usr/bin/env python

#python vir_migration_script.py raw_jsons/test1.json --output=output_txt --model=gpt-4o


import os
import sys
import json
import argparse
from typing import List
from openai import OpenAI


###############################################################################
#                      1) Generate "UI Spec" from JSON
###############################################################################

def generate_ui_spec_from_virtuoso(virtuoso_data: dict) -> str:
    print(f"######### Tool used: generate_ui_spec_from_virtuoso ###############")
    """
    Takes a JSON (Virtuoso-like) structure and produces a text-based UI spec:
      - 'journeys' -> '=== FEATURE #x: Title ==='
      - 'cases'    -> '-> Scenario #x: Title'
      - 'steps'    -> lines with Step #, Action, Value, etc.

    This text-based spec can be fed to OpenAI to generate Gherkin tests.
    """
    lines = []
    
    features = virtuoso_data.get("journeys", [])
    for feature_idx, feature in enumerate(features, start=1):
        # Use the "title" as the feature name
        feature_title = feature.get("title", "Untitled Feature")
        
        lines.append(f"=== FEATURE #{feature_idx}: {feature_title} ===\n")
        
        scenarios = feature.get("cases", [])
        for scenario_idx, scenario_obj in enumerate(scenarios, start=1):
            scenario_title = scenario_obj.get("title", "Untitled Scenario")
            lines.append(f"  -> Scenario #{scenario_idx}: {scenario_title}\n")
            
            steps = scenario_obj.get("steps", [])
            for step_idx, step in enumerate(steps, start=1):
                action = step.get("action", "")
                value = step.get("value", "")
                expression = step.get("expression", "")
                optional_str = "(Optional)" if step.get("optional", False) else ""
                
                target = step.get("target", {})
                target_text = target.get("text", "")
                
                # Collect "GUESS" clues
                guess_clues = []
                selectors = target.get("selectors", [])
                for sel in selectors:
                    if sel.get("type") == "GUESS":
                        raw_val = sel.get("value", "")
                        guess_clues.append(raw_val)

                # Write lines
                lines.append(f"    Step {step_idx} {optional_str}\n")
                lines.append(f"      Action     : {action}\n")
                if value:
                    lines.append(f"      Value      : {value}\n")
                if expression:
                    lines.append(f"      Expression : {expression}\n")
                if target_text:
                    lines.append(f"      TargetText : {target_text}\n")
                
                if guess_clues:
                    lines.append("      Guess Clues:\n")
                    for clue in guess_clues:
                        lines.append(f"         - {clue}\n")
            
            lines.append("\n")  # blank line after scenario

        lines.append("\n")  # blank line after feature

    # Return the entire text as a single string
    return "".join(lines)


###############################################################################
#                   2) Count "-> Scenario" lines in the UI Spec
###############################################################################

def count_scenarios_in_ui_spec(ui_spec: str) -> int:
    """
    Counts how many lines start with '-> Scenario' in the UI spec text.
    """
    count = 0
    lines = ui_spec.splitlines()
    for line in lines:
        if line.strip().lower().startswith("-> scenario"):
            count += 1
    return count


###############################################################################
#                   3) Use OpenAI to Generate Gherkin
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
    return response


###############################################################################
#               4) Split Gherkin Response & Write .feature Files
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


def write_feature_files(features: List[str], output_folder: str) -> None:
    """
    For each feature block, create a .feature file named after the Feature: line.
    """
    for idx, feature_text in enumerate(features):
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
    print(f"Wrote {len(features)} feature file(s) to {output_folder}")


###############################################################################
#                                   MAIN
###############################################################################

def main():
    """
    Single script that:
     1) Reads a JSON file in Virtuoso-like format (journeys/cases/steps).
     2) Creates a text-based UI spec from that JSON.
     3) Counts how many scenarios are in that UI spec.
     4) Calls OpenAI to generate Gherkin testcases, requesting the same # of testcases as scenarios.
     5) Saves the resulting Gherkin features to .feature files in an output folder.
    """
    parser = argparse.ArgumentParser(description="JSON-in -> Gherkin-out script using OpenAI.")
    parser.add_argument("input_json", type=str, help="Path to the JSON file (Virtuoso-like).")
    parser.add_argument("--output", type=str, required=True, help="Output folder for .feature files.")
    parser.add_argument("--model", type=str, default="o1-preview", help="OpenAI model name (default=o1-preview).")
    args = parser.parse_args()

    # 1) Read the JSON data
    if not os.path.isfile(args.input_json):
        print(f"Error: {args.input_json} does not exist.")
        sys.exit(1)

    with open(args.input_json, "r", encoding="utf-8") as f:
        virtuoso_data = json.load(f)

    # 2) Convert the JSON data to a UI spec text
    ui_spec = generate_ui_spec_from_virtuoso(virtuoso_data)
    # (Optional) Print or log it
    print("===== Generated UI Spec =====")
    print(ui_spec)

    # 3) Count the number of scenarios
    scenario_count = count_scenarios_in_ui_spec(ui_spec)
    print(f"Found {scenario_count} scenario(s).")

    # 4) Prepare the instructions for OpenAI
    #    We'll use a template with {number_of_testcase} replaced by scenario_count
    prompt_template = """Analyse (thoroughly examine and break down) the given UI specification to produce detailed Gherkin test cases following the provided testing plan.
Focus on creating both positive, negative, and typical business-as-usual scenarios for each UI step or user interaction.
Make sure you validate:
    - Data correctness
    - Data types
    - Null value handling (if relevant)
    - Adherence to the specification's steps
    - drop Coverage related scenarios

Cover all testing areas: functional, positive, negative, error handling, and integration.
Be extremely direct, clear, and DETAILED in your approach. 
Your final output should be strictly the Gherkin test cases, ready for execution. 
Generate a large spread of test cases (at least {number_of_testcase}).
Never include ### in the testcases. 
Always write the testcases in standard Gherkin format. 
Follow this example style in your final output:

Example:
Feature: Some Feature details
    Background: Some common steps across scenarios
        Given ...
        When ..
        Then ....
        
    Scenario: Scenario_details
        Given ...
        When ...
        Then ...
    Scenario: Another Scenario_details
        Given ...
        When ...
        Then ...
"""

    prompt_instructions = prompt_template.replace("{number_of_testcase}", str(scenario_count))
    final_prompt = prepare_prompt(prompt_instructions, ui_spec)

    # Ensure we have an API key set
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable not set.")
        sys.exit(1)

    # 5) Generate the Gherkin testcases
    try:
        response_text = generate_test_cases(final_prompt, args.model)
    except Exception as e:
        print("Error during OpenAI generation:", e)
        sys.exit(1)

    # 6) Split the Gherkin text by each "Feature:"
    gherkin_features = split_features(response_text)

    # 7) Write them to .feature files
    ensure_output_folder(args.output)
    write_feature_files(gherkin_features, args.output)


if __name__ == "__main__":
    main()