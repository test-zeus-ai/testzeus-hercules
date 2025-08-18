import json
import random
from typing import Any, Dict, Optional

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.agent_config_types import (
    ENV_TO_LLM_PARAMS_MAPPING,
    ENV_TO_MODEL_CONFIG_MAPPING,
)
from testzeus_hercules.utils.logger import logger


class ConfigEnvLoader:
    """Handles loading configuration from environment variables.

    This class is responsible for loading and normalizing configuration
    from the global configuration manager, applying defaults, and handling
    environment-specific logic.
    """

    # Mapping for Portkey-specific configuration keys
    KEY_MAPPING_ENV_PORTKEY: dict[str, str] = {
        "PORTKEY_STRATEGY": "strategy",
        "PORTKEY_CACHE_ENABLED": "cache",
        "PORTKEY_TARGETS": "targets",
        "PORTKEY_GUARDRAILS": "guardrails",
        "PORTKEY_RETRY_COUNT": "retry_count",
        "PORTKEY_TIMEOUT": "timeout",
        "PORTKEY_CACHE_TTL": "cache_ttl",
    }

    @classmethod
    def load_from_env(cls) -> Dict[str, Dict[str, Any]]:
        """Load configuration from the global configuration manager.

        Returns:
            Dict containing the configuration with all agents sharing same config
        """
        logger.info("Loading configuration from global config manager")
        normalized_config = cls._normalize_config_from_env()

        # Check if Portkey is enabled from global config
        config_manager = get_global_conf()
        if config_manager.is_portkey_enabled():
            logger.info("Portkey integration is enabled")
            normalized_config["enable_portkey"] = "true"
            normalized_config["portkey_api_key"] = config_manager.get_portkey_api_key() or ""

            # Load Portkey-specific configuration
            portkey_config = cls._load_portkey_config()
            if portkey_config:
                normalized_config["portkey_config"] = portkey_config

        # All agents share the same configuration
        return {
            "planner_agent": normalized_config,
            "nav_agent": normalized_config,
            "mem_agent": normalized_config,
            "helper_agent": normalized_config,
        }

    @classmethod
    def _normalize_config_from_env(cls) -> Dict[str, Any]:
        """Normalize configuration from environment variables.

        This method transforms environment variables into a properly structured
        configuration dictionary suitable for LLM agents. It handles:
        1. Model configuration parameters (extracted directly from ENV_TO_MODEL_CONFIG_MAPPING)
        2. LLM parameters with appropriate type conversions
        3. Model-specific defaults based on the selected model

        Note: The keys in ENV_TO_MODEL_CONFIG_MAPPING already contain the correct naming
        convention expected by AgentsLLMConfig.normalize_agent_config(), so we use them
        directly without adding any prefix.

        Returns:
            Dict containing normalized configuration
        """
        global_config = get_global_conf().get_config()
        normalized_config: Dict[str, Any] = {}

        # Load model-related configuration
        # The keys in ENV_TO_MODEL_CONFIG_MAPPING (e.g., "model_name", "model_api_key") are
        # exactly what AgentsLLMConfig.normalize_agent_config() expects
        for env_key, config_key in ENV_TO_MODEL_CONFIG_MAPPING.items():
            if env_key in global_config and global_config[env_key]:
                value = global_config[env_key]
                # Special handling for pricing which should be a float
                if env_key == "LLM_MODEL_PRICING":
                    try:
                        value = float(value)
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid float value for {env_key}: {value}")
                # Handle boolean values
                elif env_key in ["LLM_MODEL_NATIVE_TOOL_CALLS", "LLM_MODEL_HIDE_TOOLS"]:
                    if isinstance(value, str):
                        value = value.lower() in ["true", "1", "yes", "y"]
                    else:
                        value = bool(value)

                # Store the value with the mapped key directly (without adding a model_ prefix)
                normalized_config[config_key] = value

        # Load LLM parameter configuration
        llm_config_params: Dict[str, Any] = {}
        for env_key, config_key in ENV_TO_LLM_PARAMS_MAPPING.items():
            if env_key in global_config and global_config[env_key]:
                # Handle type conversions for numeric parameters
                value = global_config[env_key]
                if config_key in [
                    "temperature",
                    "presence_penalty",
                    "frequency_penalty",
                ]:
                    try:
                        value = float(value)
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid float value for {env_key}: {value}")
                        continue
                elif config_key in ["cache_seed", "seed", "max_tokens", "max_completion_tokens"]:
                    try:
                        value = int(value)
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid int value for {env_key}: {value}")
                        continue
                elif config_key in ["stop"]:
                    # Convert string to list if it looks like JSON
                    if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
                        try:
                            value = json.loads(value)
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON in {env_key}: {value}")
                            continue
                llm_config_params[config_key] = value

        # Apply model-specific defaults if needed
        model_name = normalized_config.get("model_name", "").lower()
        if model_name.startswith("gpt"):  # GPT models
            llm_config_params.setdefault("temperature", 0.0)
        else:  # Other models (maintain some reasonable defaults)
            llm_config_params.setdefault("temperature", 0.1)

        # Only add llm_config_params if we have values
        if llm_config_params:
            normalized_config["llm_config_params"] = llm_config_params

        return normalized_config

    @classmethod
    def _load_portkey_config(cls) -> Dict[str, Any]:
        """Load Portkey-specific configuration from environment variables.

        Returns:
            Dict containing Portkey configuration
        """
        global_config = get_global_conf().get_config()
        portkey_config: Dict[str, Any] = {}

        # Process strategy
        if "PORTKEY_STRATEGY" in global_config and global_config["PORTKEY_STRATEGY"]:
            strategy = global_config["PORTKEY_STRATEGY"]
            if strategy in ["fallback", "loadbalance"]:
                portkey_config["strategy"] = strategy
            else:
                logger.warning(f"Invalid PORTKEY_STRATEGY: {strategy}. Using default 'fallback'")
                portkey_config["strategy"] = "fallback"

        # Process cache settings
        if "PORTKEY_CACHE_ENABLED" in global_config:
            cache_enabled = global_config["PORTKEY_CACHE_ENABLED"].lower() == "true"
            if cache_enabled:
                portkey_config["cache"] = True

                # Add cache TTL if configured
                if "PORTKEY_CACHE_TTL" in global_config:
                    try:
                        portkey_config["cache_ttl"] = int(global_config["PORTKEY_CACHE_TTL"])
                    except ValueError:
                        logger.warning(f"Invalid PORTKEY_CACHE_TTL: {global_config['PORTKEY_CACHE_TTL']}. Using default.")

        # Process targets (JSON format)
        if "PORTKEY_TARGETS" in global_config and global_config["PORTKEY_TARGETS"]:
            try:
                targets = json.loads(global_config["PORTKEY_TARGETS"])
                if isinstance(targets, list):
                    portkey_config["targets"] = targets
                else:
                    logger.warning("PORTKEY_TARGETS is not a valid JSON array")
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in PORTKEY_TARGETS: {global_config['PORTKEY_TARGETS']}")

        # Process guardrails (JSON format)
        if "PORTKEY_GUARDRAILS" in global_config and global_config["PORTKEY_GUARDRAILS"]:
            try:
                guardrails = json.loads(global_config["PORTKEY_GUARDRAILS"])
                if isinstance(guardrails, dict):
                    portkey_config["guardrails"] = guardrails
                else:
                    logger.warning("PORTKEY_GUARDRAILS is not a valid JSON object")
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in PORTKEY_GUARDRAILS: {global_config['PORTKEY_GUARDRAILS']}")

        # Process retry count
        if "PORTKEY_RETRY_COUNT" in global_config:
            try:
                portkey_config["retry_count"] = int(global_config["PORTKEY_RETRY_COUNT"])
            except ValueError:
                logger.warning(f"Invalid PORTKEY_RETRY_COUNT: {global_config['PORTKEY_RETRY_COUNT']}. Using default.")

        # Process timeout
        if "PORTKEY_TIMEOUT" in global_config:
            try:
                portkey_config["timeout"] = float(global_config["PORTKEY_TIMEOUT"])
            except ValueError:
                logger.warning(f"Invalid PORTKEY_TIMEOUT: {global_config['PORTKEY_TIMEOUT']}. Using default.")

        return portkey_config

    @classmethod
    def get_env_config_file_path(cls) -> str | None:
        """Get the configuration file path from global config.

        Returns:
            Optional[str]: Configuration file path if set
        """
        return get_global_conf().get_config().get("AGENTS_LLM_CONFIG_FILE")

    @classmethod
    def get_env_config_ref_key(cls) -> str | None:
        """Get the configuration reference key from global config.

        Returns:
            Optional[str]: Configuration reference key if set
        """
        return get_global_conf().get_config().get("AGENTS_LLM_CONFIG_FILE_REF_KEY")
