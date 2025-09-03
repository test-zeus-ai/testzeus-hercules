import re
from typing import Dict, Any

from testzeus_hercules.utils.logger import logger


def _is_gpt_version_model(model_name: str, version: str) -> bool:

    """
    Check if the model is a GPT-5 variant using regex pattern.
    Matches: gpt-5, gpt-5-mini, gpt-5-nano, gpt-5-2025-08-07, etc.
    """
    
    return bool(re.match(f'^gpt-{version}', model_name))


def adapt_llm_params_for_model(model_name: str, model_config_params: Dict[str, Any], llm_config: Dict[str, Any]) -> Dict[str, Any]:

    """
    Normalize token-limit params for different providers/families.
    
    For GPT-5 models, converts max_tokens to max_completion_tokens.
    For Claude models, converts max_tokens to max_tokens_to_sample.
    For other models, ensures max_tokens is present.
    """
    params = dict(llm_config or {})

    if _is_gpt_version_model(model_name, ""):
        # add api_key from model_config_params to llm_config_params
        if "api_key" in model_config_params:
            params["api_key"] = model_config_params["api_key"]
        if _is_gpt_version_model(model_name, "5"):
            # GPT-5 and newer models use max_completion_tokens
            if "max_tokens" in params and "max_completion_tokens" not in params:
                params["max_completion_tokens"] = params.pop("max_tokens")
                logger.warning(
                    "Deprecated param 'max_tokens' supplied for %s; auto-translating to 'max_completion_tokens'.",
                    model_name,
                )
            # Remove/ignore temperature for GPT-5 (only default is supported by API)
            if "temperature" in params:
                if params["temperature"] not in (None, 1, 1.0):
                    logger.warning(
                        "Model %s ignores non-default temperature=%s; removing to use API default (1).",
                        model_name,
                        params["temperature"],
                    )
                params.pop("temperature", None)
            # Set default if neither is present
            if "max_completion_tokens" not in params:
                params["max_completion_tokens"] = 4096
            
            params['reasoning'] = {"effort": "low"}

    elif model_name.startswith(("claude", "anthropic")):
        # Claude models use max_tokens_to_sample
        if "max_tokens" in params and "max_tokens_to_sample" not in params:
            params["max_tokens_to_sample"] = params.pop("max_tokens")
            logger.warning(
                "Deprecated param 'max_tokens' supplied for %s; auto-translating to 'max_tokens_to_sample'.",
                model_name,
            )
        # Set default if neither is present
        if "max_tokens_to_sample" not in params:
            params["max_tokens_to_sample"] = 4096

    else:
        # Other models use max_tokens
        # If max_completion_tokens is present, convert it back to max_tokens
        if "max_completion_tokens" in params and "max_tokens" not in params:
            params["max_tokens"] = params.pop("max_completion_tokens")
            logger.info(
                "Converting 'max_completion_tokens' to 'max_tokens' for %s model.",
                model_name,
            )
        # Set default if neither is present
        if "max_tokens" not in params:
            params["max_tokens"] = 4096
    
    params["model"] = model_name
    return params