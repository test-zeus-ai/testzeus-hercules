import re
from typing import Any, Dict, Iterable

from .logger import logger


def _is_gpt5_model(model_name: str) -> bool:
    """
    Check if the model is a GPT-5 variant using regex pattern.
    Matches: gpt-5, gpt-5-mini, gpt-5-nano, gpt-5-2025-08-07, etc.
    """
    return bool(re.match(r"^gpt-5", model_name))


def adapt_llm_params_for_model(
    model_name: str,
    llm_config: Dict[str, Any],
    *,
    transport: str = "native",
    unsupported_params: Iterable[str] | None = None,
) -> Dict[str, Any]:
    """
    Normalize token-limit params for different providers/families.

    For GPT-5 models, converts max_tokens to max_completion_tokens.
    For Claude models, accepts legacy max_tokens_to_sample input and emits
    max_tokens for both native Anthropic and OpenAI-compatible transports.
    For other models, ensures max_tokens is present.
    """
    params = dict(llm_config or {})
    params.pop("cache_seed", None)
    params = {key: value for key, value in params.items() if value is not None}

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
        legacy_token_key = "max_tokens_to_sample" in params
        if "max_tokens" not in params and legacy_token_key:
            params["max_tokens"] = params["max_tokens_to_sample"]
        params.pop("max_tokens_to_sample", None)
        if legacy_token_key:
            logger.warning(
                "Deprecated param 'max_tokens_to_sample' supplied for %s; translating to 'max_tokens'.",
                model_name,
            )
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

    omitted = set(unsupported_params or ())
    for param in omitted:
        params.pop(param, None)

    params["model"] = model_name
    return params
