import json
import os
import random
from typing import Any, Dict, List, Optional, Union, cast

from testzeus_hercules.utils.logger import logger


class ConfigFileLoader:
    """Handles loading configuration from JSON files.

    This class provides utilities for loading agent configurations from
    JSON files and for normalizing environment variable based configurations.
    """

    @staticmethod
    def load_from_file(file_path: str, ref_key: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Load configuration from a file.

        Args:
            file_path: Path to the configuration file
            ref_key: Optional reference key to extract specific config section

        Returns:
            Dict containing the provider-to-agent mapping configurations

        Raises:
            FileNotFoundError: If the file doesn't exist
            json.JSONDecodeError: If the file contains invalid JSON
            KeyError: If ref_key is provided but not found in the file
        """
        try:
            with open(file_path, "r") as file:
                file_config = json.load(file)

            if ref_key:
                if ref_key in file_config:
                    logger.info(f"Loading configuration from: {file_path} with key: {ref_key}")
                    return {ref_key: ConfigFileLoader._filter_none_values(file_config[ref_key])}
                else:
                    logger.error(f"Key '{ref_key}' not found in the configuration file.")
                    raise KeyError(f"Key '{ref_key}' not found in the configuration file.")

            # Filter None values from all providers
            filtered_config = {}
            for provider, provider_config in file_config.items():
                filtered_config[provider] = ConfigFileLoader._filter_none_values(provider_config)

            return filtered_config

        except FileNotFoundError as e:
            logger.error(f"Configuration file not found: {file_path}")
            raise e
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in configuration file: {file_path}")
            raise e
        except Exception as e:
            logger.error(f"Error loading configuration file: {e}")
            raise e

    @staticmethod
    def _filter_none_values(
        config: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Filter out None values from configuration dictionaries.

        Args:
            config: Configuration dictionary to filter

        Returns:
            Filtered configuration dictionary
        """
        filtered_config = {}
        for agent_name, agent_config in config.items():
            filtered_agent_config = {}
            for key, value in agent_config.items():
                if value is not None:
                    if isinstance(value, dict):
                        # Filter nested dictionaries
                        nested_filtered = {k: v for k, v in value.items() if v is not None}
                        if nested_filtered:  # Only add non-empty dictionaries
                            filtered_agent_config[key] = nested_filtered
                    else:
                        filtered_agent_config[key] = value
            filtered_config[agent_name] = filtered_agent_config
        return filtered_config

    @staticmethod
    def validate_config_structure(config: Dict[str, Any]) -> bool:
        """Validate the basic structure of the configuration.

        Args:
            config: Configuration dictionary to validate

        Returns:
            bool: True if valid, False otherwise
        """
        # Each agent config should have model_name and llm_config_params
        for agent_config in config.values():
            if not isinstance(agent_config, dict):
                return False

            if "model_name" not in agent_config:
                return False

            if "llm_config_params" not in agent_config:
                agent_config["llm_config_params"] = {}

        return True
