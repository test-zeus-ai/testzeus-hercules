import os
import re
from collections import defaultdict
from typing import Dict, List, DefaultDict

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.logger import logger


def split_feature_file(
    input_file: str, output_dir: str, dont_append_header: bool = False
) -> List[Dict[str, str]]:
    """
    Splits a single BDD feature file into multiple feature files.
    The script preserves the feature-level content that should be shared across all scenario files.

    Parameters:
    input_file (str): Path to the input BDD feature file.
    output_dir (str): Path to the directory where the split feature files will be saved.
    dont_append_header (bool): If True, the Feature header is only added to the first extracted scenario file.

    Returns:
    list: A list of dictionaries containing feature, scenario, and output file path.
    """
    os.makedirs(output_dir, exist_ok=True)

    res_opt_list = []

    with open(input_file, "r") as f:
        feature_content = f.read()

    scenario_pattern = re.compile(r"\b[Ss]cenario\b.*:")
    all_scenarios = scenario_pattern.findall(feature_content)
    parts = scenario_pattern.split(feature_content)

    feature_header = parts[0].strip()

    feature_name = "Feature"
    for line in feature_header.split("\n"):
        if "Feature:" in line:
            feature_name = line.split("Feature:")[1].strip()
            break

    scenarios = parts[1:]
    prev_comment_lines = ""
    already_visited_scenarios: DefaultDict[str, int] = defaultdict(int)

    for i, scenario in enumerate(scenarios):
        scenario_name = all_scenarios[i].replace("Scenario:", "").strip()
        already_visited_scenarios[scenario_name] += 1
        visit_count = already_visited_scenarios[scenario_name]

        # Extract any comment lines before the scenario
        lines = scenario.strip().split("\n")
        comment_lines = []
        scenario_lines = []
        for line in lines:
            if line.strip().startswith("#"):
                comment_lines.append(line)
            else:
                scenario_lines.append(line)

        # Update prev_comment_lines for the next iteration
        prev_comment_lines = "\n".join(comment_lines)

        # Combine the scenario content
        scenario_content = (
            "Scenario: " + scenario_name + "\n" + "\n".join(scenario_lines)
        )

        # Create output file name
        safe_scenario_name = (
            re.sub(r"[^\w\s-]", "", scenario_name).strip().replace(" ", "_")
        )
        if visit_count > 1:
            output_file = os.path.join(
                output_dir, f"{safe_scenario_name}_{visit_count}.feature"
            )
        else:
            output_file = os.path.join(output_dir, f"{safe_scenario_name}.feature")

        # Write the feature file
        with open(output_file, "w") as f:
            if i == 0 or not dont_append_header:
                f.write(feature_header + "\n\n")
            if prev_comment_lines:
                f.write(prev_comment_lines + "\n")
            f.write(scenario_content)

        res_opt_list.append(
            {
                "feature": feature_name,
                "scenario": scenario_name,
                "file_path": output_file,
            }
        )

    return res_opt_list


def serialize_feature_file(file_path: str) -> str:
    """
    Converts a feature file to a single line string where new lines are replaced with "#newline#".

    Parameters:
    file_path (str): Path to the feature file to be serialized.

    Returns:
    str: The serialized content of the feature file.
    """
    with open(file_path, "r") as f:
        feature_content = f.read()
    while "  " in feature_content:
        feature_content = feature_content.replace("  ", " ")
    feature_content = feature_content.replace("\n\n", "\n")
    feature_content = feature_content.replace("\n", " #newline# ")
    return feature_content


def process_feature_file(dont_append_header: bool = False) -> List[Dict[str, str]]:
    """
    Process the feature file specified in the global configuration.

    Parameters:
    dont_append_header (bool): If True, the Feature header is only added to the first extracted scenario file.

    Returns:
    list: A list of dictionaries containing feature, scenario, and file path information.
    """
    input_gherkin_file_path = get_global_conf().get_input_gherkin_file_path()
    output_dir = os.path.dirname(input_gherkin_file_path)
    return split_feature_file(input_gherkin_file_path, output_dir, dont_append_header)


def split_test(pass_background_to_all: bool = True) -> None:
    """
    Parses command line arguments and splits the feature file into individual scenario files.
    """
    # parser = argparse.ArgumentParser(
    #     description="Split a Gherkin feature file into individual scenario files."
    # )
    # parser.add_argument(
    #     "--feature_file_path", type=str, help="Path to the feature file to be split."
    # )
    # parser.add_argument(
    #     "--output_dir",
    #     type=str,
    #     help="Directory where the split scenario files will be saved.",
    # )
    # args = parser.parse_args()

    # feature_file_path = args.feature_file_path
    # output_dir = args.output_dir
    # list_of_feats = split_feature_file(feature_file_path, output_dir)
    list_of_feats = process_feature_file(pass_background_to_all=pass_background_to_all)
    for feat in list_of_feats:
        file_path = feat["file_path"]
        logger.info(serialize_feature_file(file_path))


# # Example usage
# split_test()
