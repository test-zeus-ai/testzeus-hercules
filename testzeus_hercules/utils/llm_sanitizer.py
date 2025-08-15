from testzeus_hercules.utils.logger import logger
from typing import Any

def sanitize_llm_config_for_autogen(model_name: str, llm_config_params: dict[str, Any]) -> dict[str, Any]:
    config = llm_config_params.copy()

    if "gpt-5" in model_name.lower():
        config["temperature"] = 1
        config.pop("max_tokens", None)
        config.pop("max_completion_tokens", None)
        config.pop("presence_penalty", None)
        config.pop("frequency_penalty", None)
    return config
