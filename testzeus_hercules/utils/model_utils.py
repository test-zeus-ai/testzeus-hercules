import re
from typing import Any, Dict

from .logger import logger


def _is_gpt5_model(model_name: str) -> bool:
    """
    Check if the model is a GPT-5 variant using regex pattern.
    Matches: gpt-5, gpt-5-mini, gpt-5-nano, gpt-5-2025-08-07, etc.
    """
    return bool(re.match(r"^gpt-5", model_name))


def adapt_llm_params_for_model(model_name: str, llm_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize token-limit params for different providers/families.

    For GPT-5 models, converts max_tokens to max_completion_tokens.
    For Claude models (reached through an OpenAI-compatible chat endpoint),
    normalizes every legacy token-limit name into max_tokens.
    For other models, ensures max_tokens is present.
    """
    params = dict(llm_config or {})
    params.pop("cache_seed", None)

    if _is_gpt5_model(model_name):
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

    elif model_name.startswith(("claude", "anthropic")):
        # max_tokens_to_sample belongs to the legacy completions API. Chat
        # models are always built on an OpenAI-compatible chat endpoint, which
        # takes max_tokens; sending the legacy name gets passed through as an
        # unknown kwarg and the request fails before it leaves the process.
        # Translate in that direction and repair a config that already carries
        # the legacy name.
        if "max_tokens_to_sample" in params and "max_tokens" not in params:
            params["max_tokens"] = params.pop("max_tokens_to_sample")
            logger.warning(
                "'max_tokens_to_sample' supplied for %s; that is the legacy completions parameter. Using 'max_tokens', which is what a chat endpoint accepts.",
                model_name,
            )
        params.pop("max_tokens_to_sample", None)
        if "max_completion_tokens" in params and "max_tokens" not in params:
            params["max_tokens"] = params.pop("max_completion_tokens")
            logger.info(
                "Converting 'max_completion_tokens' to 'max_tokens' for %s model.",
                model_name,
            )
        # Set default if neither is present
        if "max_tokens" not in params:
            params["max_tokens"] = 4096

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

    # Strip OpenAI o-series only params for non o-series models
    o_series_only_params = ["reasoning_effort", "reasoning_summary"]
    is_o_series = bool(re.match(r"^o[0-9]", model_name or ""))
    if not is_o_series:
        for p in o_series_only_params:
            if p in params:
                logger.info("Removing o-series param '%s' for non-o-series model %s.", p, model_name)
                params.pop(p)

    params["model"] = model_name
    return params
