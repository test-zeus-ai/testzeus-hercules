import os
from typing import Optional

from testzeus_hercules.config import get_test_data_path
from testzeus_hercules.core.memory.state_handler import get_run_data, get_stored_data
from testzeus_hercules.utils.logger import logger


class StaticLTM:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(StaticLTM, cls).__new__(cls)
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
        test_data_path = get_test_data_path()
        selected_files = []
        consolidated_data = ""
        for filename in os.listdir(test_data_path):
            file_path = os.path.join(test_data_path, filename)
            if os.path.isfile(file_path):
                # Check if the file is a non-text file
                if not filename.endswith((".txt", ".json", ".csv", ".rft", ".yaml", ".yml")):
                    logger.info("Skipping non-text file: %s", file_path)
                    continue
                if filename.endswith((".json", ".yaml", ".yml")):
                    selected_files.append(file_path)
                new_read = ""
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
                with open(file_path, "r", encoding="utf-8") as file:
                    data = file.read()
                    data = data.replace("  ", " ").replace("    ", " ").strip()
                    if data:
                        new_read += f"following is test_data from {sanitized_filename}\n" + data + "\n"
                    logger.info("Test data loaded from: %s", file_path)
                consolidated_data += new_read
        li_selected_files = ", ".join(selected_files)
        return consolidated_data + "\nStored Data:" + get_stored_data() + f"\nhelper_spec_file_paths: {li_selected_files} + \nprevious_context_data: {get_run_data()}"

    def get_user_ltm(self) -> Optional[str]:
        """
        Get the test data stored in the test_data.txt file.

        Returns:
            Optional[str]: The test data stored in the test_data.txt file or None if not found.
        """
        return self.consolidated_data


def get_user_ltm() -> Optional[str]:
    """
    Get the user long term memory.

    Returns:
        Optional[str]: The user long term memory or None if not found.
    """
    return StaticLTM().get_user_ltm()


a = get_user_ltm()
# print(a)
