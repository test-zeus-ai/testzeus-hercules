"""Cost estimation helpers based on user-configured model pricing.

All pricing values handled here are expressed in USD per 1M tokens:

- A float pricing is a blended rate applied to all tokens (prompt + completion).
- A dict pricing splits the rates into "input" and "output" components
  (aliases "prompt" and "completion" are accepted).
"""

from __future__ import annotations

import math
from typing import Any

from testzeus_hercules.core.agent_config_types import STANDARD_AGENT_TYPES
from testzeus_hercules.core.agents_llm_config_manager import AgentsLLMConfigManager
from testzeus_hercules.utils.logger import logger

_TOKENS_PER_MILLION = 1_000_000

# Pricing may appear under the raw key ("model_pricing", as written in config
# files/env) or the normalized key ("price", as stored in model_config_params
# by AgentsLLMConfig.normalize_agent_config()).
_PRICING_KEYS = ("price", "model_pricing")


def _coerce_float(value: Any) -> float | None:
    """Convert a value to a finite float, or return None when it is not numeric.

    Non-finite results ("nan"/"Infinity" strings, NaN/inf floats — json.load
    accepts those literals) are rejected as missing so they cannot poison cost
    math downstream.
    """
    coerced: float | None = None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            coerced = float(value)
        except OverflowError:
            # Huge ints (e.g. 10**400) are numeric but do not fit a float.
            logger.warning("[COST] Ignoring out-of-range numeric value %r", value)
            return None
    elif isinstance(value, str):
        try:
            coerced = float(value)
        except ValueError:
            return None
    if coerced is None:
        return None
    if not math.isfinite(coerced):
        logger.warning("[COST] Ignoring non-finite pricing value %r", value)
        return None
    return coerced


def _coerce_token_count(value: Any) -> int:
    """Safely convert a token count to int (e.g. "321.5" -> 321, garbage -> 0)."""
    coerced = _coerce_float(value)
    if coerced is None:
        return 0
    return int(coerced)


def resolve_model_pricing(role: str | None = None) -> float | dict | None:
    """Resolve the user-configured ``model_pricing`` for an agent role.

    Args:
        role: Agent role name ("planner_agent", "nav_agent" or "helper_agent").
            When None, all standard roles are searched in order and the first
            pricing found is returned.

    Returns:
        The configured pricing in USD per 1M tokens — either a float (blended
        rate for all tokens) or a dict with "input"/"output" rates — or None
        when it is unset or the config manager is unavailable.
    """
    roles = [role] if role else list(STANDARD_AGENT_TYPES)
    for role_name in roles:
        try:
            agent_cfg = AgentsLLMConfigManager.get_instance().get_agent_config(role_name)
        except Exception as exc:
            logger.debug("[COST] Could not load agent config for %s: %s", role_name, exc)
            continue
        if not agent_cfg:
            continue
        for section in ("model_config_params", "other_settings"):
            settings = agent_cfg.get(section) or {}
            for key in _PRICING_KEYS:
                if key not in settings:
                    continue
                value = settings[key]
                if isinstance(value, dict):
                    return value
                coerced = _coerce_float(value)
                if coerced is not None:
                    return coerced
                logger.warning("[COST] Ignoring invalid model_pricing %r for %s", value, role_name)
    return None


def estimate_cost(model_pricing: float | dict, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Estimate cost in USD from pricing expressed in USD per 1M tokens.

    Args:
        model_pricing: Either a float — blended USD per 1M tokens applied to
            all tokens (prompt + completion) — or a dict with "input"/"output"
            keys (aliases "prompt"/"completion") holding USD per 1M tokens for
            each side.
        prompt_tokens: Number of prompt (input) tokens.
        completion_tokens: Number of completion (output) tokens.

    Returns:
        Estimated cost in USD, 0.0 when the computed cost overflows to a
        non-finite value or the token counts themselves overflow float, or
        None when the pricing is non-positive or the dict pricing is invalid
        (missing, non-numeric or negative rates).
    """
    if prompt_tokens < 0 or completion_tokens < 0:
        return None

    # Convert token counts up front inside a guard: ints beyond ~1.8e308 do not
    # survive float() (OverflowError), and multiplying pricing by the raw int
    # would raise before the isfinite check below could ever see an inf result.
    try:
        prompt_tokens_float = float(prompt_tokens)
        completion_tokens_float = float(completion_tokens)
    except OverflowError:
        logger.warning("[COST] Token counts (%r, %r) overflow float; treating estimated cost as 0.0", prompt_tokens, completion_tokens)
        return 0.0

    if isinstance(model_pricing, dict):
        input_rate = _coerce_float(model_pricing.get("input", model_pricing.get("prompt")))
        output_rate = _coerce_float(model_pricing.get("output", model_pricing.get("completion")))
        if input_rate is None or output_rate is None or input_rate < 0 or output_rate < 0:
            return None
        if input_rate + output_rate <= 0:
            return None
        result = (input_rate * prompt_tokens_float + output_rate * completion_tokens_float) / _TOKENS_PER_MILLION
    else:
        blended_rate = _coerce_float(model_pricing)
        if blended_rate is None or blended_rate <= 0:
            return None
        result = blended_rate * (prompt_tokens_float + completion_tokens_float) / _TOKENS_PER_MILLION

    # Guard against overflow (e.g. absurd pricing x huge token counts) so
    # callers never receive NaN/inf costs.
    if not math.isfinite(result):
        logger.warning("[COST] Estimated cost is non-finite (%r); treating as 0.0", result)
        return 0.0
    return result


def estimate_cost_for_tokens(model_pricing: float | dict, tokens: dict) -> float | None:
    """Estimate cost in USD from a token-usage dict.

    Args:
        model_pricing: Pricing in USD per 1M tokens — a float blended rate or a
            dict with "input"/"output" rates (see :func:`estimate_cost`).
        tokens: Dict holding "prompt_tokens" and "completion_tokens" counts.

    Returns:
        Estimated cost in USD, or None when the pricing or the token counts
        are invalid.
    """
    if not isinstance(tokens, dict):
        return None
    prompt_tokens = _coerce_token_count(tokens.get("prompt_tokens", 0))
    completion_tokens = _coerce_token_count(tokens.get("completion_tokens", 0))
    return estimate_cost(model_pricing, prompt_tokens, completion_tokens)
