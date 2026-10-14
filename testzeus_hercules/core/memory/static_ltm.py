import json
import os
from typing import Optional

import yaml  # Requires PyYAML installed: pip install pyyaml
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.memory.state_handler import (
    clear_runtime_state,
    get_run_data,
    get_stored_data,
)
from testzeus_hercules.core.memory.static_data_loader import load_data
from testzeus_hercules.utils.logger import logger


class StaticLTM:
    _instance: Optional["StaticLTM"] = None
    _loaded_for: Optional[str] = None

    def __new__(cls) -> "StaticLTM":
        # The cache is keyed on the configured test-data path: bulk runs repoint
        # TEST_DATA_PATH per suite inside one process, and every suite must see
        # its own test data as long-term memory, not the first suite's.
        test_data_path = get_global_conf().get_test_data_path()
        if cls._instance is None or cls._loaded_for != test_data_path:
            # The stored/run state in state_handler is process-global (keyed by
            # the per-scenario test id): when the test-data path changes it
            # belongs to the previous suite, so drop it before folding it into
            # the fresh memory — suite B's LTM must not inherit suite A's
            # stored secrets. Same-path reads keep the state: cross-scenario
            # sharing within a suite is intended.
            if cls._loaded_for is not None and cls._loaded_for != test_data_path:
                clear_runtime_state()
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
            cls._loaded_for = test_data_path
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the cached memory so the next read reloads it from disk."""
        cls._instance = None
        cls._loaded_for = None

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
