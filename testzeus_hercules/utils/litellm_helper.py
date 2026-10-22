"""LiteLLM-only LLM access for Hercules."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from testzeus_hercules.config import get_global_conf, set_global_conf
from testzeus_hercules.core.agents_llm_config_manager import AgentsLLMConfigManager
from testzeus_hercules.utils.llm_helper import (
    convert_model_config_to_langchain_format,
    create_chat_model,
)
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.model_utils import adapt_llm_params_for_model

LITELLM_PROVIDER = "litellm"


def ensure_litellm_provider() -> None:
    """Ensure the active LLM provider is LiteLLM (via agents_llm_config.json)."""
    cfg = get_global_conf().get_config()
    config_file = cfg.get("AGENTS_LLM_CONFIG_FILE")
    ref_key = cfg.get("AGENTS_LLM_CONFIG_FILE_REF_KEY")

    if not config_file:
        raise ValueError("LiteLLM is required. Set AGENTS_LLM_CONFIG_FILE to agents_llm_config.json " "with a 'litellm' provider section and AGENTS_LLM_CONFIG_FILE_REF_KEY=litellm.")

    if ref_key != LITELLM_PROVIDER:
        logger.warning(
            "Forcing AGENTS_LLM_CONFIG_FILE_REF_KEY to '%s' (was %r)",
            LITELLM_PROVIDER,
            ref_key,
        )
        set_global_conf({"AGENTS_LLM_CONFIG_FILE_REF_KEY": LITELLM_PROVIDER}, override=True)

    manager = AgentsLLMConfigManager.get_instance()
    manager.initialize()

    active = manager.get_active_provider()
    if active != LITELLM_PROVIDER:
        raise ValueError(f"Only the '{LITELLM_PROVIDER}' provider is supported; " f"active provider is '{active}'. " f"Ensure agents_llm_config.json contains a '{LITELLM_PROVIDER}' section.")


def get_litellm_chat_model(agent_name: str = "planner_agent") -> BaseChatModel:
    """Return a LangChain chat model configured for the LiteLLM proxy."""
    ensure_litellm_provider()
    agent_cfg = AgentsLLMConfigManager.get_instance().get_agent_config(agent_name)
    model_config_params = agent_cfg.get("model_config_params", {})
    missing_fields = [
        field
        for field in ("model_api_key", "model_base_url")
        if not model_config_params.get(field)
    ]
    if missing_fields:
        logger.warning(
            "LiteLLM chat model config for %s is missing required field(s): %s",
            agent_name,
            ", ".join(missing_fields),
        )
    model_cfg = convert_model_config_to_langchain_format(agent_cfg["model_config_params"])
    model_name = model_cfg.get("model") or agent_cfg["model_config_params"].get("model_name", "")
    llm_params = adapt_llm_params_for_model(model_name, agent_cfg["llm_config_params"])
    return create_chat_model(model_cfg, llm_params)
