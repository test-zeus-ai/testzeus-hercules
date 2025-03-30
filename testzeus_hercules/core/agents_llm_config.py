import json
from typing import Any, Dict, Optional, cast

from testzeus_hercules.core.agent_config_types import (
    DEFAULT_LLM_CONFIG_PARAMS,
    AgentConfig,
    LLMConfigParams,
    ModelConfig,
)
from testzeus_hercules.core.agent_registry import AgentRegistry
from testzeus_hercules.utils.logger import logger


class AgentsLLMConfig:
    """Configuration processor for LLM agents.

    This class processes raw configuration data into structured agent configurations.
    It handles normalization and provides access to agent-specific configurations
    through a registry system that supports multiple providers.
    """

    def __init__(self) -> None:
        """Initialize the configuration processor."""
        self.registry = AgentRegistry()

    def load_from_file(self, file_path: str, provider_key: Optional[str] = None) -> None:
        """Load configurations from JSON file.

        Args:
            file_path: Path to the JSON configuration file
            provider_key: Optional key to set as active provider

        Raises:
            FileNotFoundError: If the configuration file doesn't exist
            json.JSONDecodeError: If the file contains invalid JSON
        """
        logger.info(f"Loading configurations from file: {file_path}")
        with open(file_path, "r") as f:
            config_data = json.load(f)

        # Register all providers from the file
        for provider, configs in config_data.items():
            normalized_configs = {agent: self.normalize_agent_config(agent_config) for agent, agent_config in configs.items()}
            self.registry.register_provider(provider, normalized_configs)

        # Set active provider if specified
        if provider_key:
            if provider_key not in config_data:
                raise ValueError(f"Provider key '{provider_key}' not found in config file")
            self.registry.set_active_provider(provider_key)

    def register_provider(self, provider: str, configs: Dict[str, Dict[str, Any]]) -> None:
        """Register a provider with its configurations.

        Args:
            provider: Provider name
            configs: Dictionary mapping agent names to their configurations
        """
        normalized_configs = {agent: self.normalize_agent_config(agent_config) for agent, agent_config in configs.items()}
        self.registry.register_provider(provider, normalized_configs)

    def normalize_agent_config(self, raw_config: Dict[str, Any]) -> AgentConfig:
        """Normalize raw config into typed AgentConfig.

        Args:
            raw_config: Raw configuration dictionary

        Returns:
            Normalized AgentConfig
        """
        # Extract and transform model configuration parameters
        model_config: Dict[str, Any] = {}

        # Map keys according to expected format
        for raw_key, normalized_key in {
            "model_name": "model",
            "model_api_key": "api_key",
            "model_base_url": "base_url",
            "model_client_host": "client_host",
            "model_native_tool_calls": "native_tool_calls",
            "model_hide_tools": "hide_tools",
            "model_api_type": "api_type",
            "model_project_id": "gcp_project_id",
            "model_region": "gcp_region",
            "model_api_version": "api_version",
            "model_aws_region": "aws_region",
            "model_aws_access_key": "aws_access_key",
            "model_aws_secret_key": "aws_secret_key",
            "model_aws_profile_name": "aws_profile_name",
            "model_aws_session_token": "aws_session_token",
            "model_pricing": "price",
        }.items():
            if raw_key in raw_config and raw_config[raw_key] is not None:
                model_config[normalized_key] = raw_config[raw_key]

        # Get LLM parameters with defaults
        llm_params: Dict[str, Any] = {}
        llm_params.update(DEFAULT_LLM_CONFIG_PARAMS)

        # Update with any custom llm_config_params
        if "llm_config_params" in raw_config and raw_config["llm_config_params"]:
            llm_params.update(raw_config["llm_config_params"])

        # Collect other settings (exclude None values)
        other_settings = {k: v for k, v in raw_config.items() if not k.startswith("model_") and k != "llm_config_params" and v is not None}

        return AgentConfig(
            model_config_params=cast(ModelConfig, model_config),
            llm_config_params=cast(LLMConfigParams, llm_params),
            other_settings=other_settings,
        )

    def get_agent_config(self, agent_name: str, provider: Optional[str] = None) -> Optional[AgentConfig]:
        """Get configuration for a specific agent.

        Args:
            agent_name: Name of the agent
            provider: Optional provider override

        Returns:
            Agent configuration if found, None otherwise
        """
        return self.registry.get_agent_config(agent_name, provider)

    def list_available_agents(self, provider: Optional[str] = None) -> list[str]:
        """List available agents for the current or specified provider.

        Args:
            provider: Optional provider to list agents for

        Returns:
            List of agent names
        """
        return self.registry.list_agents(provider)

    def get_active_provider(self) -> Optional[str]:
        """Get the currently active provider.

        Returns:
            Active provider name or None if no provider is active
        """
        return self.registry.get_active_provider()

    def list_providers(self) -> list[str]:
        """Get list of available providers.

        Returns:
            List of provider names
        """
        return self.registry.list_providers()
