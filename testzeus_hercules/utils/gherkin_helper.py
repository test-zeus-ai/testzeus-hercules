import asyncio
import logging
import os
import re
from collections import defaultdict
from typing import Dict, List

import aiofiles
from testzeus_hercules.config import get_global_conf


async def split_feature_file(input_file: str, output_dir: str, dont_append_header: bool = False) -> List[Dict[str, str]]:
    """
    Splits a single BDD feature file into multiple feature files asynchronously.
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

    async with aiofiles.open(input_file, "r") as f:
        feature_content = await f.read()
    feature_content = re.sub(r'@\w+\s*', '', feature_content)
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

    already_visited_scenarios: Dict[str, int] = defaultdict(int)

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

        # Get the first line and truncate to 100 characters
        scenario_title = scenario.strip().split("\n", 1)[0][:100]

        # Remove all characters except alphanumeric and underscore
        clean_title = re.sub(r'[^\w]', '', scenario_title)
        
        # Enforce a stricter length limit (50 characters) to prevent OS filename length issues
        # If the title is too long, truncate it and add a hash of the original title
        if len(clean_title) > 50:
            import hashlib
            # Create a hash of the original title
            title_hash = hashlib.md5(clean_title.encode()).hexdigest()[:8]
            # Truncate the title and append the hash
            truncated_title = f"{clean_title[:42]}_{title_hash}"
        else:
            truncated_title = clean_title
            
        scenario_filename = f"{truncated_title}.feature"
        o_scenario_title = scenario_filename
        output_file = os.path.join(output_dir, scenario_filename)
        f_scenario = scenario.strip()

        for comment_line in comment_lines_li:
            f_scenario = f_scenario.replace(comment_line, "")

        if already_visited_scenarios[o_scenario_title] > 0:
            # Add a unique number to the filename while keeping it under the length limit
            scenario_filename = f"{truncated_title}_{already_visited_scenarios[o_scenario_title]}.feature"
            scenario_title = f"{scenario_title} - {already_visited_scenarios[o_scenario_title]}"
            output_file = os.path.join(output_dir, scenario_filename)
        already_visited_scenarios[o_scenario_title] += 1

        if dont_append_header and i > 0:
            file_content = f"{prev_comment_lines}\n{all_scenarios[i]}{scenario_title}{f_scenario}"
        else:
            file_content = f"{feature_header}\n\n{prev_comment_lines}\n{all_scenarios[i]}{scenario_title}{f_scenario}"

        async with aiofiles.open(output_file, "w") as f:
            await f.write(file_content)
        prev_comment_lines = comment_lines
        prev_comment_lines = comment_lines

        scenario_di = {
            "feature": feature_name,
            "scenario": scenario_title,
            "output_file": output_file,
        }
        res_opt_list.append(scenario_di)

    return res_opt_list


async def serialize_feature_file(file_path: str) -> str:
    """
    Converts a feature file to a single line string where new lines are replaced with ";next;".
    Any text starting from ";skip;" until the end of that line is excluded from the output.

    Parameters:
    file_path (str): Path to the feature file to be serialized.

    Returns:
    str: The serialized content of the feature file with skipped portions removed.
    """
    async with aiofiles.open(file_path, "r") as f:
        feature_content = await f.read()

    # Process each line to remove text from ";skip;" to the end of the line
    processed_lines = []
    for line in feature_content.split("\n"):
        skip_index = line.find(";skip;")
        if skip_index != -1:
            # Keep only the text before ";skip;"
            processed_lines.append(line[:skip_index])
        else:
            processed_lines.append(line)

    # Join the processed lines back together
    feature_content = "\n".join(processed_lines)

    # Continue with the normal processing
    while "  " in feature_content or "\n\n" in feature_content:
        feature_content = feature_content.replace("  ", " ")
        feature_content = feature_content.replace("\n\n", "\n")
    feature_content = feature_content.replace("\n", " ;next; ")
    return feature_content


async def process_feature_file(
    dont_append_header: bool = False,
) -> List[Dict[str, str]]:
    """
    Processes a Gherkin feature file by splitting it into smaller parts.

    Parameters:
        dont_append_header (bool): If True, the Feature header is only added to the first extracted scenario file.

    Returns:
        List[Dict[str, str]]: A list of dictionaries containing the split parts of the feature file.
    """
    tmp_gherkin_path = get_global_conf().get_tmp_gherkin_path()
    input_gherkin_file_path = get_global_conf().get_input_gherkin_file_path()

    return await split_feature_file(
        input_gherkin_file_path,
        tmp_gherkin_path,
        dont_append_header=dont_append_header,
    )


async def split_test(pass_background_to_all: bool = True) -> None:
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
    logger = logging.getLogger(__name__)

    list_of_feats = await process_feature_file(dont_append_header=not pass_background_to_all)
    for feat in list_of_feats:
        file_path = feat["output_file"]
        logger.info(await serialize_feature_file(file_path))


# # Example usage
# if __name__ == "__main__":
#     asyncio.run(split_test())
