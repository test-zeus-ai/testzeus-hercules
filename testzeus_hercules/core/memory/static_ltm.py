import json
import os
from typing import Optional

import yaml  # Requires PyYAML installed: pip install pyyaml
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.memory.state_handler import get_run_data, get_stored_data
from testzeus_hercules.core.memory.static_data_loader import load_data
from testzeus_hercules.utils.logger import logger


class StaticLTM:
    _instance = None

    def __new__(cls) -> "StaticLTM":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        """Initialize the StaticLTM instance by loading data."""
        # Append stored data and run data
        result = load_data()
        stored_data = get_stored_data()
        run_data = get_run_data()
        if stored_data:
            result += "\nStored Data:" + stored_data
        if run_data:
            result += f"\nprevious_context_data: {run_data}"

        self.consolidated_data: str = result

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
