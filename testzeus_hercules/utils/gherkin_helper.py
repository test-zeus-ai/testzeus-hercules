import os
import re
from collections import defaultdict
from typing import Dict, List

from testzeus_hercules.config import get_input_gherkin_file_path, get_tmp_gherkin_path

tmp_gherkin_path = get_tmp_gherkin_path()
input_gherkin_file_path = get_input_gherkin_file_path()


def split_feature_file(input_file: str, output_dir: str) -> List[Dict[str, str]]:
    """
    Splits a single BDD feature file into multiple feature files, with each file containing a single scenario.
    The script preserves the feature-level content that should be shared across all scenario files.

    Parameters:
    input_file (str): Path to the input BDD feature file.
    output_dir (str): Path to the directory where the split feature files will be saved.

    Returns:
    list: A list of dictionaries containing feature, scenario, and output file path.
    """
    os.makedirs(output_dir, exist_ok=True)

    res_opt_list = []

    with open(input_file, "r") as f:
        feature_content = f.read()

    scenario_pattern = re.compile(r"\bScenario\b.*:")
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

    already_visited_scenarios = defaultdict(int)

    for i, scenario in enumerate(scenarios):
        comment_lines = ""
        comment_lines_li = []
        skip_line = 1
        for a_line in scenario.split("\n")[::-1]:
            line = a_line.strip()
            if line.startswith("#"):
                comment_lines = line + "\n" + comment_lines
                comment_lines_li.append(line)
            elif skip_line == 0:
                break
            else:
                skip_line -= 1

        scenario_title = scenario.strip().split("\n")[0]
        scenario_filename = f"{scenario_title.replace(' ', '_')}.feature"
        output_file = os.path.join(output_dir, scenario_filename)
        f_scenario = scenario.strip()
        for comment_line in comment_lines_li:
            f_scenario = f_scenario.replace(comment_line, "")

        if already_visited_scenarios[scenario_title] > 0:
            scenario_title = (
                f"{scenario_title} - {already_visited_scenarios[scenario_title]}"
            )
            scenario_filename = f"{scenario_title.replace(' ', '_')}_{already_visited_scenarios[scenario_title]}.feature"
            output_file = os.path.join(output_dir, scenario_filename)
        already_visited_scenarios[scenario_title] += 1

        with open(output_file, "w") as f:
            f.write(
                f"{feature_header}\n\n{prev_comment_lines}\n{all_scenarios[i]}{scenario_title}{f_scenario}"
            )
        prev_comment_lines = comment_lines

        scenario_di = {
            "feature": feature_name,
            "scenario": scenario_title,
            "output_file": output_file,
        }
        res_opt_list.append(scenario_di)

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


def process_feature_file() -> List[Dict[str, str]]:
    """
    Processes a Gherkin feature file by splitting it into smaller parts.

    Returns:
        List[Dict[str, str]]: A list of dictionaries containing the split parts of the feature file.
    """
    return split_feature_file(input_gherkin_file_path, tmp_gherkin_path)


def split_test() -> None:
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
    list_of_feats = process_feature_file()
    for feat in list_of_feats:
        file_path = feat["output_file"]
        print(serialize_feature_file(file_path))


# # Example usage
# split_test()
