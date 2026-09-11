import json
import os
from typing import Any, Dict, Optional, cast

from testzeus_hercules.config import (
    bump_config_version,
    get_config_version,
    get_global_conf,
)
from testzeus_hercules.core.agent_config_types import AgentConfig
from testzeus_hercules.core.agents_llm_config import AgentsLLMConfig
from testzeus_hercules.core.config_env_loader import ConfigEnvLoader
from testzeus_hercules.core.config_file_loader import ConfigFileLoader
from testzeus_hercules.core.config_portkey_loader import PortkeyConfigLoader
from testzeus_hercules.utils.logger import logger

# Config keys the manager depends on. They are fingerprinted (together with the
# global config version) so in-place edits via get_config() are detected too.
_WATCHED_CONFIG_KEYS = ("AGENTS_LLM_CONFIG_FILE", "AGENTS_LLM_CONFIG_FILE_REF_KEY")


class AgentsLLMConfigManager:
    """Singleton manager for AgentsLLMConfig instances.

    This class manages the lifecycle and state of AgentsLLMConfig instances,
    providing centralized access and control over configuration.
    """

    _instance: Optional["AgentsLLMConfigManager"] = None
    _config: Optional[AgentsLLMConfig] = None
    _last_global_config_fingerprint: Optional[tuple] = None
    _portkey_loader: Optional[PortkeyConfigLoader] = None

    def __init__(self) -> None:
        """Private constructor to enforce singleton pattern."""
        if AgentsLLMConfigManager._instance is not None:
            raise RuntimeError("Use get_instance() instead")
        AgentsLLMConfigManager._instance = self
        self._portkey_loader = PortkeyConfigLoader()

    @classmethod
    def _global_config_fingerprint(cls) -> tuple:
        """Build a change-detection fingerprint of the global config.

        Uses the monotonic ``config_version`` counter instead of
        ``id(config_dict)`` — object identity misses in-place edits and can
        alias a recycled (freed) dict. The watched config keys are included so
        raw in-place mutations (``conf.get_config()[key] = value``) are caught
        as well, since they do not bump the version counter.
        """
        global_config = get_global_conf().get_config()
        return (
            get_config_version(),
            *(global_config.get(key) for key in _WATCHED_CONFIG_KEYS),
        )

    @classmethod
    def get_instance(cls) -> "AgentsLLMConfigManager":
        """Get or create the singleton instance.

        Returns:
            AgentsLLMConfigManager: The singleton instance
        """

        if cls._instance is None:
            cls._instance = cls()

        # Check if global config has changed
        fingerprint = cls._global_config_fingerprint()
        if cls._instance._last_global_config_fingerprint != fingerprint:
            logger.info("Global config changed, reinitializing AgentsLLMConfigManager")
            cls._instance._last_global_config_fingerprint = fingerprint
            cls._instance.initialize()

        return cls._instance

    @classmethod
    def setup_agent_config(
        cls,
        loaded_config_dict: Dict[str, Dict[str, Dict[str, Any]]],
        active_provider: str,
    ) -> None:
        """Set up agent configurations using a pre-loaded config dictionary.

        This method is the preferred way to initialize the manager with pre-loaded
        configurations. It handles normalization and registration of configurations
        for all providers and agents. This follows the Dict priority in our
        Dict > File > Env hierarchy.

        Args:
            loaded_config_dict: Dictionary mapping provider names to their agent configurations.
                Structure: {provider_name: {agent_name: agent_config}}
            active_provider: Name of the provider to set as active

        Raises:
            ValueError: If active_provider is not found in loaded_config_dict
            RuntimeError: If configuration initialization fails
        """
        if active_provider not in loaded_config_dict:
            raise ValueError(f"Active provider '{active_provider}' not found in configuration")

        try:
            # Get or create manager instance
            manager = cls.get_instance()

            # Create and configure AgentsLLMConfig
            config = AgentsLLMConfig()

            # Register configurations for each provider
            for provider, configs in loaded_config_dict.items():
                # Filter out None values from each agent configuration
                filtered_configs = {}
                for agent, agent_config in configs.items():
                    # Remove None values from top-level keys
                    filtered_agent_config = {k: v for k, v in agent_config.items() if v is not None}

                    # Handle nested dictionaries like llm_config_params
                    for key, value in filtered_agent_config.items():
                        if isinstance(value, dict):
                            filtered_agent_config[key] = {k: v for k, v in value.items() if v is not None}

                    filtered_configs[agent] = filtered_agent_config

                # Normalize and register the filtered configs
                normalized_configs = {agent: config.normalize_agent_config(agent_config) for agent, agent_config in filtered_configs.items()}
                config.registry.register_provider(provider, normalized_configs)

            # Set the active provider
            config.registry.set_active_provider(active_provider)

            # Set the configured instance
            manager._config = config
            logger.info(f"Successfully initialized agent configurations with provider '{active_provider}'")

        except Exception as e:
            error_msg = f"Failed to initialize agent configurations: {str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def initialize(self) -> None:
        """Initialize or reinitialize configuration.

        This follows the priority order: File > Env
        If a configuration file is specified, it will be used.
        Otherwise, environment variables will be used.
        """
        global_config = get_global_conf().get_config()
        self._config = AgentsLLMConfig()

        # Try loading from file first (File priority)
        config_file = global_config.get("AGENTS_LLM_CONFIG_FILE")
        config_ref_key = global_config.get("AGENTS_LLM_CONFIG_FILE_REF_KEY")

        if config_file:
            # An explicitly configured file must be usable — failing silently
            # over to env/default config hides misconfiguration (or, worse,
            # causes real API spend against an unintended default model).
            if not os.path.exists(config_file):
                raise ValueError(f"AGENTS_LLM_CONFIG_FILE is set to '{config_file}' but the file does not exist. " "Create the file or unset AGENTS_LLM_CONFIG_FILE to use environment configuration.")
            try:
                logger.info(f"Loading agent config from file: {config_file}")
                if config_ref_key:
                    logger.info(f"Using reference key: {config_ref_key}")

                # Use ConfigFileLoader to load the file
                file_config = ConfigFileLoader.load_from_file(config_file, config_ref_key)

                # Register all providers from the file
                for provider, configs in file_config.items():
                    # Filter and normalize each agent config
                    normalized_configs = {}
                    for agent_name, agent_config in configs.items():
                        # Note: config_file_loader already filters None values
                        normalized_configs[agent_name] = self._config.normalize_agent_config(agent_config)

                    self._config.registry.register_provider(provider, normalized_configs)

                # If a ref_key was specified, it becomes the active provider
                # Otherwise, use the first provider
                active_provider = config_ref_key if config_ref_key in file_config else next(iter(file_config))
                self._config.registry.set_active_provider(active_provider)

                logger.info(f"Successfully loaded configuration from file with provider: {active_provider}")
                return
            except Exception as e:
                raise ValueError(f"Failed to load agents LLM config file '{config_file}': {e}") from e

        # Fall back to environment variables (Env priority)
        logger.info("Loading config from environment variables")
        # Use ConfigEnvLoader to get environment-based configuration
        env_config = ConfigEnvLoader.load_from_env()

        # Register the environment provider
        if env_config:
            self._config.register_provider("environment", env_config)
            self._config.registry.set_active_provider("environment")
            logger.info("Successfully loaded configuration from environment variables")
        else:
            logger.warning("No environment configuration found")

    def get_config(self) -> AgentsLLMConfig:
        """Get the current configuration instance.

        Returns:
            AgentsLLMConfig: The current configuration

        Raises:
            RuntimeError: If configuration is not initialized
        """
        if not self._config:
            raise RuntimeError("Config not initialized. Call initialize() first.")
        return self._config

    def _get_portkey_config(self) -> Dict[str, Any]:
        """Get Portkey configuration from global config.

        Returns:
            Dict containing Portkey configuration
        """
        global_config = get_global_conf()
        portkey_config = {}

        # Add strategy if configured
        strategy = global_config.get_config().get("PORTKEY_STRATEGY")
        if strategy:
            portkey_config["strategy"] = strategy

        # Add cache settings if enabled
        if global_config.get_config().get("PORTKEY_CACHE_ENABLED", "").lower() == "true":
            portkey_config["cache"] = True

            # Add cache TTL if configured
            if cache_ttl := global_config.get_config().get("PORTKEY_CACHE_TTL"):
                try:
                    portkey_config["cache_ttl"] = int(cache_ttl)
                except ValueError:
                    logger.warning(f"Invalid PORTKEY_CACHE_TTL value: {cache_ttl}")

        # Add retry count if configured
        if retry_count := global_config.get_config().get("PORTKEY_RETRY_COUNT"):
            try:
                portkey_config["retry_count"] = int(retry_count)
            except ValueError:
                logger.warning(f"Invalid PORTKEY_RETRY_COUNT value: {retry_count}")

        # Add timeout if configured
        if timeout := global_config.get_config().get("PORTKEY_TIMEOUT"):
            try:
                portkey_config["timeout"] = float(timeout)
            except ValueError:
                logger.warning(f"Invalid PORTKEY_TIMEOUT value: {timeout}")

        # Add targets if configured
        targets = global_config.get_config().get("PORTKEY_TARGETS")
        if targets:
            try:
                portkey_config["targets"] = json.loads(targets)
            except json.JSONDecodeError:
                logger.warning("Invalid PORTKEY_TARGETS JSON, skipping")

        # Add guardrails if configured
        guardrails = global_config.get_config().get("PORTKEY_GUARDRAILS")
        if guardrails:
            try:
                portkey_config["guardrails"] = json.loads(guardrails)
            except json.JSONDecodeError:
                logger.warning("Invalid PORTKEY_GUARDRAILS JSON, skipping")

        return portkey_config

    def _transform_config_with_portkey(self, config: AgentConfig) -> AgentConfig:
        """Apply Portkey transformation to an agent configuration if Portkey is enabled.

        Args:
            config: The agent configuration to transform

        Returns:
            Transformed agent configuration
        """
        global_config = get_global_conf()
        if not global_config.is_portkey_enabled() or not self._portkey_loader:
            return config

        portkey_api_key = global_config.get_portkey_api_key()
        if not portkey_api_key:
            logger.warning("Portkey is enabled but no API key is provided")
            return config

        try:
            # Convert AgentConfig to dict for transformation
            config_dict = {
                "model_config_params": config["model_config_params"],
                "llm_config_params": config["llm_config_params"],
                "other_settings": config["other_settings"],
            }

            # Check if there's a portkey_config in other_settings
            portkey_config = config["other_settings"].get("portkey_config", {})

            # If no portkey_config in other_settings, get from global config
            if not portkey_config:
                portkey_config = self._get_portkey_config()

            # Apply Portkey transformation
            transformed_config_dict = self._portkey_loader.transform_config(config_dict, portkey_api_key, portkey_config)

            # Create new AgentConfig from transformed dict
            return AgentConfig(
                model_config_params=transformed_config_dict["model_config_params"],
                llm_config_params=transformed_config_dict["llm_config_params"],
                other_settings=transformed_config_dict.get("other_settings", {}),
            )
        except Exception as e:
            logger.error(f"Failed to apply Portkey transformation: {str(e)}")
            # Return original config on error
            return config

    def get_agent_config(self, agent_name: str) -> AgentConfig:
        """Get configuration for a specific agent.

        If Portkey is enabled, the configuration will be transformed
        to use Portkey before being returned.

        Args:
            agent_name: Name of the agent to get configuration for

        Returns:
            AgentConfig: Agent configuration (potentially Portkey-transformed)

        Raises:
            RuntimeError: If configuration is not initialized
            ValueError: If agent configuration is not found
        """
        if not self._config:
            raise RuntimeError("Config not initialized. Call initialize() first.")

        config = self._config.get_agent_config(agent_name)
        if not config:
            # Partial provider stanzas (e.g. only planner_agent defined in the
            # config file) would otherwise raise ValueError at first use
            # mid-run. Fall back to the environment-derived config for the
            # missing role instead.
            active_provider = self._config.get_active_provider()
            logger.warning(
                "No configuration for agent '%s' under provider '%s'; falling back to environment-derived config",
                agent_name,
                active_provider,
            )
            env_config = ConfigEnvLoader.load_from_env().get(agent_name)
            if env_config:
                config = self._config.normalize_agent_config(env_config)
            if not config:
                raise ValueError(f"No configuration found for agent: {agent_name}")

        # Check if Portkey is enabled
        global_config = get_global_conf()
        if global_config.is_portkey_enabled():
            logger.info(f"Applying Portkey transformation to agent config: {agent_name}")

            # Apply Portkey transformation if enabled
            portkey_config = self._transform_config_with_portkey(config)

            # Log the provider being used
            if "is_portkey_enabled" in portkey_config["other_settings"]:
                model_api_type = portkey_config["model_config_params"].get(
                    "api_type",
                    global_config.get_config().get("LLM_MODEL_API_TYPE", "openai"),
                )
                provider = portkey_config["model_config_params"].get(
                    "provider",
                    global_config.get_config().get("LLM_MODEL_API_TYPE", "openai"),
                )
                logger.info(f"Using Portkey with API type: {model_api_type}, provider: {provider}")
            return portkey_config

        return config

    def get_active_provider(self) -> Optional[str]:
        """Get the currently active provider.

        Returns:
            str: Name of the active provider, or None if not set
        """
        if not self._config:
            return None
        return self._config.get_active_provider()

    def list_available_agents(self) -> list[str]:
        """Get list of available agents.

        Returns:
            list[str]: List of agent names

        Raises:
            RuntimeError: If configuration is not initialized
        """
        if not self._config:
            raise RuntimeError("Config not initialized. Call initialize() first.")
        return self._config.list_available_agents()

    def list_providers(self) -> list[str]:
        """Get list of available providers.

        Returns:
            list[str]: List of provider names

        Raises:
            RuntimeError: If configuration is not initialized
        """
        if not self._config:
            raise RuntimeError("Config not initialized. Call initialize() first.")
        return self._config.list_providers()

    def reset(self) -> None:
        """Reset the configuration state."""
        logger.info("Resetting config state")
        self._config = None
        self._last_global_config_fingerprint = None
