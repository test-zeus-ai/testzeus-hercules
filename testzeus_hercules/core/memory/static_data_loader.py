import json
import os
from typing import List, Tuple

import yaml
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.logger import logger


def load_data() -> str:
    """
    Load data from test data files.

    Returns:
        str: Consolidated data from all test data files.
    """
    test_data_path = get_global_conf().get_test_data_path()
    selected_files = []
    consolidated_data = ""

    for filename in os.listdir(test_data_path):
        file_path = os.path.join(test_data_path, filename)
        if os.path.isfile(file_path):
            # Skip non-text files
            if not filename.endswith((".txt", ".json", ".csv", ".rft", ".yaml", ".yml")):
                logger.info("Skipping non-text file: %s", file_path)
                continue

            # Keep track of .json/.yaml/.yml files
            if filename.endswith((".json", ".yaml", ".yml")):
                selected_files.append(file_path)

            # Sanitize filename for use in the data block
            sanitized_filename = (
                filename.replace(".", "_")
                .replace(" ", "_")
                .replace("-", "_")
                .replace(":", "_")
                .replace("/", "_")
                .replace("\\", "_")
                .replace("(", "_")
                .replace(")", "_")
                .replace("[", "_")
                .replace("]", "_")
                .upper()
            )

            new_read = read_and_process_file(file_path, sanitized_filename)
            consolidated_data += new_read

    # Prepare a comma-separated string of selected files
    li_selected_files = ", ".join(selected_files)
    result = consolidated_data

    if li_selected_files:
        result += f"\nhelper_spec_file_paths: {li_selected_files}"

    return result


def list_load_data() -> List[Tuple[str, str]]:
    """
    Load data from test data files and return a list of tuples where the first element is the sanitized filename and the second element is the data.

    Returns:
        List[Tuple[str, str]]: A list of tuples containing the sanitized filename and the data from all test data files.
    """
    result: List[Tuple[str, str]] = []
    test_data_dir = get_global_conf().get_test_data_path()

    if test_data_dir and os.path.exists(test_data_dir):
        for root, _, files in os.walk(test_data_dir):
            new_read: List[str] = []

            for file in files:
                file_path = os.path.join(root, file)

                # Get relative path for sanitized filename
                rel_path = os.path.relpath(file_path, test_data_dir)
                sanitized_filename = rel_path.replace("\\", "/")

                new_read = read_and_process_file(file_path, sanitized_filename)
                result.append((rel_path, new_read))

    return result


def read_and_process_file(file_path: str, sanitized_filename: str) -> str:
    new_read = ""
    with open(file_path, "r", encoding="utf-8") as file:
        raw_data = file.read().strip()
        if raw_data:
            if file_path.endswith((".yaml", ".yml")):
                # Load YAML, then dump in a minimal style
                try:
                    yaml_data = yaml.safe_load(raw_data)
                    minified_yaml = yaml.safe_dump(yaml_data, default_flow_style=True, sort_keys=True).strip()
                    new_read += f"\nfollowing is test_data from {sanitized_filename}\n" + minified_yaml + "\n"
                except Exception as e:
                    logger.warning("Failed to parse YAML: %s", e)
                    new_read += f"\nfollowing is test_data from {sanitized_filename}\n" + raw_data + "\n"
            elif file_path.endswith(".json"):
                # Load JSON, then dump with minimal separators
                try:
                    json_data = json.loads(raw_data)
                    minified_json = json.dumps(json_data, separators=(",", ":"))
                    new_read += f"\nfollowing is test_data from {sanitized_filename}\n" + minified_json + "\n"
                except Exception as e:
                    logger.warning("Failed to parse JSON: %s", e)
                    new_read += f"\nfollowing is test_data from {sanitized_filename}\n" + raw_data + "\n"
            else:
                # General file types: remove redundant empty lines
                lines = raw_data.splitlines()
                cleaned_lines = [ln.strip() for ln in lines if ln.strip()]
                cleaned_data = "\n".join(cleaned_lines)
                new_read += f"\nfollowing is test_data from {sanitized_filename}\n" + cleaned_data + "\n"
        logger.info("Test data loaded from: %s", file_path)
    return new_read


def get_test_data_file_paths() -> List[str]:
    """
    Get a list of absolute paths of all test data files.

    Returns:
        List[str]: A list of absolute file paths for all test data files.
    """
    test_data_path = get_global_conf().get_test_data_path()
    file_paths = []

    if os.path.exists(test_data_path):
        for filename in os.listdir(test_data_path):
            file_path = os.path.join(test_data_path, filename)
            if os.path.isfile(file_path) and filename.endswith((".txt", ".json", ".csv", ".rft", ".yaml", ".yml")):
                file_paths.append(file_path)

    return file_paths
