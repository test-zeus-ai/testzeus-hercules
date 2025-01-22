import json
import os
from typing import Optional

import yaml  # Requires PyYAML installed: pip install pyyaml
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.memory.state_handler import get_run_data, get_stored_data
from testzeus_hercules.utils.logger import logger


class StaticLTM:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        """Initialize the StaticLTM instance by loading data."""
        self.consolidated_data: str = self._load_data()

    def _load_data(self) -> str:
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

                new_read = ""
                with open(file_path, "r", encoding="utf-8") as file:
                    raw_data = file.read().strip()

                    if raw_data:
                        if filename.endswith((".yaml", ".yml")):
                            # Load YAML, then dump in a minimal style
                            try:
                                yaml_data = yaml.safe_load(raw_data)
                                # Minify by using flow style and removing extraneous spaces
                                minified_yaml = yaml.safe_dump(yaml_data, default_flow_style=True, sort_keys=True).strip()
                                new_read += f"following is test_data from {sanitized_filename}\n" + minified_yaml + "\n"
                            except Exception as e:
                                # If YAML parsing fails, just add raw data
                                logger.warning("Failed to parse YAML: %s", e)
                                new_read += f"following is test_data from {sanitized_filename}\n" + raw_data + "\n"

                        elif filename.endswith(".json"):
                            # Load JSON, then dump with minimal separators
                            try:
                                json_data = json.loads(raw_data)
                                # Minified JSON string
                                minified_json = json.dumps(json_data, separators=(",", ":"))
                                new_read += f"following is test_data from {sanitized_filename}\n" + minified_json + "\n"
                            except Exception as e:
                                # If JSON parsing fails, just add raw data
                                logger.warning("Failed to parse JSON: %s", e)
                                new_read += f"following is test_data from {sanitized_filename}\n" + raw_data + "\n"

                        else:
                            # General file types: remove redundant empty lines
                            lines = raw_data.splitlines()
                            # Keep only non-empty lines (strip each line)
                            cleaned_lines = [ln.strip() for ln in lines if ln.strip()]
                            cleaned_data = "\n".join(cleaned_lines)
                            new_read += f"following is test_data from {sanitized_filename}\n" + cleaned_data + "\n"

                    logger.info("Test data loaded from: %s", file_path)

                consolidated_data += new_read

        # Prepare a comma-separated string of selected files
        li_selected_files = ", ".join(selected_files)
        result = consolidated_data

        # Append stored data and run data
        stored_data = get_stored_data()
        run_data = get_run_data()
        if stored_data:
            result += "\nStored Data:" + stored_data
        if li_selected_files:
            result += f"\nhelper_spec_file_paths: {li_selected_files}"
        if run_data:
            result += f"\nprevious_context_data: {run_data}"

        return result

    def get_user_ltm(self) -> Optional[str]:
        """
        Get the test data stored in the test_data.txt file.

        Returns:
            Optional[str]: The test data or None if not found.
        """
        return self.consolidated_data


def get_user_ltm() -> Optional[str]:
    """
    Get the user long term memory.

    Returns:
        Optional[str]: The user long term memory or None if not found.
    """
    return StaticLTM().get_user_ltm()


# Example usage
if __name__ == "__main__":
    a = get_user_ltm()
    # logger.info(a)
