from typing import Dict, List, Optional

from testzeus_hercules.core.agent_config_types import AgentConfig
from testzeus_hercules.utils.logger import logger


class AgentRegistry:
    """Registry for managing agent configurations across different providers."""

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._providers: Dict[str, Dict[str, AgentConfig]] = {}
        self._active_provider: Optional[str] = None

    def register_provider(self, provider: str, configs: Dict[str, AgentConfig]) -> None:
        """Register a provider with its agent configurations.

        Args:
            provider: Provider identifier (e.g., 'mistral', 'openai_gpt')
            configs: Dictionary mapping agent names to their configurations
        """
        logger.info(f"Registering provider: {provider} with {len(configs)} agent configs")
        self._providers[provider] = configs

    def set_active_provider(self, provider: str) -> None:
        """Set the active provider for agent configurations.

        Args:
            provider: Provider identifier to set as active

        Raises:
            ValueError: If the provider is not registered
        """
        if provider not in self._providers:
            raise ValueError(f"Provider {provider} not registered. Available providers: {list(self._providers.keys())}")
        logger.info(f"Setting active provider to: {provider}")
        self._active_provider = provider

    def get_agent_config(self, agent_name: str, provider: Optional[str] = None) -> Optional[AgentConfig]:
        """Get agent configuration from specified or active provider.

        Args:
            agent_name: Name of the agent to get configuration for
            provider: Optional provider override, uses active provider if not specified

        Returns:
            AgentConfig if found, None otherwise

        Raises:
            RuntimeError: If no provider is specified and no active provider is set
        """
        use_provider = provider or self._active_provider
        if not use_provider:
            raise RuntimeError("No provider specified and no active provider set")

        if use_provider not in self._providers:
            raise ValueError(f"Provider {use_provider} not registered")

        config = self._providers[use_provider].get(agent_name)
        if config is None:
            logger.warning(f"No configuration found for agent {agent_name} in provider {use_provider}")
        return config

    def list_providers(self) -> List[str]:
        """Get list of registered providers.

        Returns:
            List of provider identifiers
        """
        return list(self._providers.keys())

    def list_agents(self, provider: Optional[str] = None) -> List[str]:
        """List available agents for a provider.

        Args:
            provider: Optional provider to list agents for, uses active provider if not specified

        Returns:
            List of agent names

        Raises:
            RuntimeError: If no provider is specified and no active provider is set
        """
        use_provider = provider or self._active_provider
        if not use_provider:
            raise RuntimeError("No provider specified and no active provider set")

        if use_provider not in self._providers:
            raise ValueError(f"Provider {use_provider} not registered")

        return list(self._providers[use_provider].keys())

    def get_active_provider(self) -> Optional[str]:
        """Get the currently active provider.

        Returns:
            Active provider identifier or None if no provider is active
        """
        return self._active_provider

    def clear_provider(self, provider: str) -> None:
        """Remove a provider and its configurations.

        Args:
            provider: Provider identifier to remove
        """
        if provider in self._providers:
            del self._providers[provider]
            if self._active_provider == provider:
                self._active_provider = None
            logger.info(f"Cleared provider: {provider}")

    def clear_all(self) -> None:
        """Remove all providers and configurations."""
        self._providers.clear()
        self._active_provider = None
        logger.info("Cleared all providers and configurations")
