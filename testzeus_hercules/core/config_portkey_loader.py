import json
from typing import Any, Dict, List, Optional, Union, cast

from portkey_ai import PORTKEY_GATEWAY_URL, createHeaders
from testzeus_hercules.utils.logger import logger


class PortkeyConfigLoader:
    """Handles Portkey-specific configuration and integration.

    This class manages the transformation of standard LLM configs into
    Portkey-compatible configurations, enabling advanced features like
    routing, reliability, and monitoring.

    Portkey acts as a gateway/middleware for LLM API calls, providing:
    - Unified API interface for multiple providers
    - Load balancing and fallback strategies
    - Monitoring and observability
    - Caching (both simple and semantic)
    - Guardrails for content filtering

    See: https://docs.ag2.ai/docs/ecosystem/portkey for more details
    """

    def __init__(self) -> None:
        """Initialize the PortkeyConfigLoader."""
        self.portkey_api_key: Optional[str] = None

    def transform_config(
        self,
        base_config: Dict[str, Any],
        portkey_api_key: str,
        portkey_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Transform a base LLM config into a Portkey-enabled config.

        This method rewrites the model configuration to use Portkey as the
        API gateway while preserving the original provider information in the
        headers. It follows the Portkey Universal API approach that converts
        all provider APIs to follow the OpenAI signature.

        Args:
            base_config: Original LLM configuration (containing model_config_params, etc.)
            portkey_api_key: Portkey API key
            portkey_config: Optional additional Portkey configuration options like
                           strategy, targets, cache settings, guardrails, etc.

        Returns:
            Dict containing Portkey-enabled configuration
        """
        # Extract the model configuration from the base config
        model_config = base_config.get("model_config_params", {})

        # Determine the provider based on the model config
        provider = self._determine_provider(model_config)

        # Get the original model name and API key
        model = model_config.get("model", "")
        api_key = model_config.get("api_key", "")

        # Create the basic headers for Portkey
        portkey_headers = createHeaders(
            api_key=portkey_api_key,
            provider=provider,
        )

        # If we have a strategy configuration, prepare it for Portkey
        portkey_options: Dict[str, Any] = {}

        if portkey_config:
            # Process routing strategy (fallback or loadbalance)
            if "strategy" in portkey_config:
                portkey_options["strategy"] = {"mode": portkey_config["strategy"]}

            # Process targets for routing if specified
            if "targets" in portkey_config and isinstance(portkey_config["targets"], list):
                portkey_options["targets"] = self._prepare_targets(
                    portkey_config["targets"],
                    default_model=model,
                    default_api_key=api_key,
                )

            # Process caching options
            if "cache" in portkey_config and portkey_config["cache"]:
                cache_config = {"mode": "semantic"}
                if "cache_ttl" in portkey_config:
                    cache_config["ttl"] = portkey_config["cache_ttl"]
                portkey_options["cache"] = cache_config

            # Process guardrails
            if "guardrails" in portkey_config:
                portkey_options["guardrails"] = portkey_config["guardrails"]

            # Process retry options
            retry_config = {}
            if "retry_count" in portkey_config:
                retry_config["count"] = portkey_config["retry_count"]
            if "timeout" in portkey_config:
                retry_config["timeout"] = portkey_config["timeout"]
            if retry_config:
                portkey_options["retry"] = retry_config

        # Include any Portkey options as virtual parameters in the headers
        if portkey_options:
            portkey_headers["x-portkey-config"] = json.dumps(portkey_options)

        # Create Portkey-enabled config
        portkey_model_config = {
            # Keep original model config values except those we're overriding
            **{k: v for k, v in model_config.items() if k not in ["base_url", "api_type", "default_headers"]},
            # Set Portkey URL as the base URL
            "base_url": PORTKEY_GATEWAY_URL,
            # Portkey uses OpenAI's API signature
            "api_type": "openai",
            # Add the Portkey headers
            "default_headers": portkey_headers,
        }

        # Update the base config with Portkey settings
        transformed_config = base_config.copy()
        transformed_config["model_config_params"] = portkey_model_config
        transformed_config["is_portkey_enabled"] = True

        logger.info(f"Transformed configuration to use Portkey with provider: {provider}")
        return transformed_config

    def _determine_provider(self, model_config: Dict[str, Any]) -> str:
        """Determine the appropriate provider based on model configuration.

        Maps the internal API type to the provider identifier needed by Portkey.

        Args:
            model_config: Model configuration dictionary

        Returns:
            str: Provider identifier for Portkey
        """
        api_type = model_config.get("api_type", "").lower()
        model_name = model_config.get("model", "").lower()

        # Map API types to Portkey provider identifiers
        provider_mapping = {
            "azure": "azure-openai",
            "azure-openai": "azure-openai",
            "anthropic": "anthropic",
            "mistral": "mistral-ai",
            "groq": "groq",
            "ollama": "ollama",
            "google": "google-ai",
            "deepseek": "deepseek",
            "bedrock": "aws-bedrock",
            "cohere": "cohere",
        }

        # Check for specific API types
        for api_key, provider in provider_mapping.items():
            if api_key in api_type:
                return provider

        # Fallback: Check model name for clues if API type not matched
        if any(name in model_name for name in ["claude", "haiku", "sonnet", "opus"]):
            return "anthropic"
        elif any(name in model_name for name in ["mixtral", "mistral", "codestral"]):
            return "mistral-ai"
        elif any(name in model_name for name in ["gemini", "palm"]):
            return "google-ai"
        elif any(name in model_name for name in ["llama", "phi"]):
            return "ollama"
        elif any(name in model_name for name in ["cohere", "command"]):
            return "cohere"

        # Default to OpenAI if nothing else matches
        return "openai"

    def _prepare_targets(
        self,
        targets: List[Dict[str, Any]],
        default_model: str = "",
        default_api_key: str = "",
    ) -> List[Dict[str, Any]]:
        """Prepare target configurations for Portkey routing.

        Processes the targets list for fallback or load balancing,
        ensuring they have the required properties.

        Args:
            targets: List of target configurations
            default_model: Default model name to use if not specified in targets
            default_api_key: Default API key to use if not specified in targets

        Returns:
            List of processed target configurations
        """
        processed_targets = []

        for target in targets:
            processed_target = target.copy()

            # Ensure provider is present
            if "provider" not in processed_target:
                logger.warning(f"Skipping target without provider: {target}")
                continue

            # Add defaults if missing
            if "api_key" not in processed_target and default_api_key:
                processed_target["api_key"] = default_api_key

            # Ensure we have an override_params with model if needed
            if "override_params" not in processed_target:
                if default_model:
                    processed_target["override_params"] = {"model": default_model}
            elif "model" not in processed_target["override_params"] and default_model:
                processed_target["override_params"]["model"] = default_model

            # Set default weight if missing
            if "weight" not in processed_target:
                processed_target["weight"] = 1

            processed_targets.append(processed_target)

        return processed_targets

    def _add_portkey_features(self, model_config: Dict[str, Any], portkey_config: Dict[str, Any]) -> None:
        """Add Portkey-specific features to the configuration.

        Args:
            model_config: Model configuration to update
            portkey_config: Portkey-specific configuration options
        """
        # Add reliability settings
        if "strategy" in portkey_config:
            model_config["strategy"] = portkey_config["strategy"]

        # Add caching settings
        if "cache" in portkey_config:
            model_config["cache"] = portkey_config["cache"]

        # Add routing/fallback targets
        if "targets" in portkey_config:
            model_config["targets"] = portkey_config["targets"]

        # Add any guardrails
        if "guardrails" in portkey_config:
            model_config["guardrails"] = portkey_config["guardrails"]
