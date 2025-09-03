from typing import Any, Dict, Literal, Optional, TypedDict, Union

# Model API Types
# To add support for new API types:
# 1. Add the new type to this Literal
# 2. Update relevant configuration handling code
ModelAPIType = Literal[
    "openai",  # OpenAI API
    "anthropic",  # Anthropic API (Claude)
    "azure",  # Azure OpenAI
    "mistral",  # Mistral AI
    "groq",  # Groq API
    "ollama",  # Ollama local models
    "google",  # Google AI (Gemini, PaLM)
    "deepseek",  # DeepSeek AI
    "bedrock",  # AWS Bedrock
    # Add new API types here
]


class ModelConfig(TypedDict, total=False):
    """Model configuration parameters."""

    model_name: str
    model_api_key: Optional[str]
    model_base_url: Optional[str]
    model_api_type: Optional[ModelAPIType]
    model_api_version: Optional[str]
    model_project_id: Optional[str]
    model_region: Optional[str]
    model_client_host: Optional[str]
    model_native_tool_calls: Optional[bool]
    model_hide_tools: Optional[str]
    model_aws_region: Optional[str]
    model_aws_access_key: Optional[str]
    model_aws_secret_key: Optional[str]
    model_aws_profile_name: Optional[str]
    model_aws_session_token: Optional[str]
    model_pricing: Optional[float]


class LLMConfigParams(TypedDict, total=False):
    """LLM-specific configuration parameters."""

    cache_seed: Optional[int]
    temperature: float
    seed: Optional[int]
    max_tokens: Optional[int]
    presence_penalty: Optional[float]
    frequency_penalty: Optional[float]
    stop: Optional[Union[str, list[str]]]


class AgentConfig(TypedDict):
    """Complete agent configuration."""

    model_config_params: ModelConfig
    llm_config_params: LLMConfigParams
    other_settings: Dict[str, Any]


class AgentConfigSource:
    """Configuration source identifiers."""

    ENV = "environment"
    FILE = "file"
    GLOBAL_CONFIG = "global_config"
    DEFAULT = "default"


# Standard agent types
STANDARD_AGENT_TYPES = ["planner_agent", "nav_agent", "mem_agent", "helper_agent"]

# Default configuration values
DEFAULT_LLM_CONFIG_PARAMS = LLMConfigParams(temperature=0.1)

# Environment variable to config key mapping
ENV_TO_MODEL_CONFIG_MAPPING = {
    "LLM_MODEL_NAME": "model_name",
    "LLM_MODEL_API_KEY": "model_api_key",
    "LLM_MODEL_BASE_URL": "model_base_url",
    "LLM_MODEL_API_TYPE": "model_api_type",
    "LLM_MODEL_API_VERSION": "model_api_version",
    "LLM_MODEL_PROJECT_ID": "model_project_id",
    "LLM_MODEL_REGION": "model_region",
    "LLM_MODEL_CLIENT_HOST": "model_client_host",
    "LLM_MODEL_NATIVE_TOOL_CALLS": "model_native_tool_calls",
    "LLM_MODEL_HIDE_TOOLS": "model_hide_tools",
    "LLM_MODEL_AWS_REGION": "model_aws_region",
    "LLM_MODEL_AWS_ACCESS_KEY": "model_aws_access_key",
    "LLM_MODEL_AWS_SECRET_KEY": "model_aws_secret_key",
    "LLM_MODEL_AWS_PROFILE_NAME": "model_aws_profile_name",
    "LLM_MODEL_AWS_SESSION_TOKEN": "model_aws_session_token",
    "LLM_MODEL_PRICING": "model_pricing",
}

ENV_TO_LLM_PARAMS_MAPPING = {
    "LLM_MODEL_PRESENCE_PENALTY": "presence_penalty",
    "LLM_MODEL_FREQUENCY_PENALTY": "frequency_penalty",
    "LLM_MODEL_STOP": "stop",
}
