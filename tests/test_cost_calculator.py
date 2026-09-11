"""Offline tests for model_pricing-based cost estimation."""

import logging
from typing import Any

import pytest
from testzeus_hercules.core.simple_hercules import SimpleHercules
from testzeus_hercules.utils import cost_calculator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_config(model_config_params: dict[str, Any] | None = None, other_settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a minimal AgentConfig-shaped dict."""
    return {
        "model_config_params": model_config_params or {},
        "llm_config_params": {},
        "other_settings": other_settings or {},
    }


def _install_fake_manager(monkeypatch: pytest.MonkeyPatch, configs: dict[str, Any]) -> None:
    """Replace the AgentsLLMConfigManager used by cost_calculator with a fake."""

    class FakeAgentsLLMConfigManager:
        @classmethod
        def get_instance(cls) -> "FakeAgentsLLMConfigManager":
            return cls()

        def get_agent_config(self, agent_name: str) -> dict[str, Any]:
            if agent_name not in configs:
                raise ValueError(f"No configuration found for agent: {agent_name}")
            return configs[agent_name]

    monkeypatch.setattr(cost_calculator, "AgentsLLMConfigManager", FakeAgentsLLMConfigManager)


def _bare_hercules() -> SimpleHercules:
    """Create a SimpleHercules instance without running __init__."""
    return SimpleHercules.__new__(SimpleHercules)


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------


def test_estimate_cost_float_pricing_applies_to_total_tokens() -> None:
    # 0.5 USD per 1M tokens blended over 2M tokens = 1.0 USD.
    assert cost_calculator.estimate_cost(0.5, 1_000_000, 1_000_000) == pytest.approx(1.0)
    # Blended rate applies to prompt + completion combined.
    assert cost_calculator.estimate_cost(2.0, 100_000, 0) == pytest.approx(0.2)
    assert cost_calculator.estimate_cost(2.0, 0, 100_000) == pytest.approx(0.2)


def test_estimate_cost_dict_pricing_input_output_split() -> None:
    pricing = {"input": 1.0, "output": 3.0}
    # 1M input tokens at 1.0 USD/1M + 1M output tokens at 3.0 USD/1M = 4.0 USD.
    assert cost_calculator.estimate_cost(pricing, 1_000_000, 1_000_000) == pytest.approx(4.0)
    assert cost_calculator.estimate_cost(pricing, 500_000, 250_000) == pytest.approx(0.5 + 0.75)


def test_estimate_cost_dict_pricing_accepts_aliases() -> None:
    assert cost_calculator.estimate_cost({"prompt": 1.0, "completion": 3.0}, 1_000_000, 1_000_000) == pytest.approx(4.0)
    # Primary keys win over aliases when mixed.
    assert cost_calculator.estimate_cost({"input": 1.0, "prompt": 9.0, "completion": 3.0}, 1_000_000, 1_000_000) == pytest.approx(4.0)


def test_estimate_cost_invalid_inputs_return_none() -> None:
    # Non-positive float pricing.
    assert cost_calculator.estimate_cost(0.0, 1_000, 1_000) is None
    assert cost_calculator.estimate_cost(-1.0, 1_000, 1_000) is None
    # Non-numeric pricing.
    assert cost_calculator.estimate_cost(None, 1_000, 1_000) is None  # type: ignore[arg-type]
    assert cost_calculator.estimate_cost("abc", 1_000, 1_000) is None  # type: ignore[arg-type]
    # Invalid dict pricing: missing / non-numeric / negative rates.
    assert cost_calculator.estimate_cost({}, 1_000, 1_000) is None
    assert cost_calculator.estimate_cost({"input": 1.0}, 1_000, 1_000) is None
    assert cost_calculator.estimate_cost({"input": "x", "output": 1.0}, 1_000, 1_000) is None
    assert cost_calculator.estimate_cost({"input": -1.0, "output": 1.0}, 1_000, 1_000) is None
    assert cost_calculator.estimate_cost({"input": 1.0, "output": -1.0}, 1_000, 1_000) is None
    # All-zero dict pricing is non-positive overall.
    assert cost_calculator.estimate_cost({"input": 0, "output": 0}, 1_000, 1_000) is None
    # Negative token counts are rejected.
    assert cost_calculator.estimate_cost(0.5, -1, 1_000) is None
    assert cost_calculator.estimate_cost({"input": 1.0, "output": 1.0}, 1_000, -1) is None


def test_estimate_cost_accepts_numeric_string_and_zero_output_rate() -> None:
    assert cost_calculator.estimate_cost("0.5", 2_000_000, 0) == pytest.approx(1.0)
    # Zero output rate is valid as long as the total pricing is positive.
    assert cost_calculator.estimate_cost({"input": 1.0, "output": 0}, 1_000_000, 1_000_000) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# estimate_cost_for_tokens
# ---------------------------------------------------------------------------


def test_estimate_cost_for_tokens_wrapper() -> None:
    tokens = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    assert cost_calculator.estimate_cost_for_tokens(0.5, tokens) == pytest.approx(1.0)
    assert cost_calculator.estimate_cost_for_tokens({"input": 1.0, "output": 3.0}, tokens) == pytest.approx(4.0)


def test_estimate_cost_for_tokens_handles_missing_or_invalid_tokens() -> None:
    assert cost_calculator.estimate_cost_for_tokens(0.5, {}) == 0.0
    assert cost_calculator.estimate_cost_for_tokens(0.5, {"prompt_tokens": "1000000", "completion_tokens": "1000000"}) == pytest.approx(1.0)
    assert cost_calculator.estimate_cost_for_tokens(0.5, None) is None  # type: ignore[arg-type]
    assert cost_calculator.estimate_cost_for_tokens(None, {"prompt_tokens": 1}) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# resolve_model_pricing
# ---------------------------------------------------------------------------


def test_resolve_model_pricing_for_specific_role(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_manager(
        monkeypatch,
        {
            "planner_agent": _agent_config({"price": 0.5}),
            "nav_agent": _agent_config({"price": 2.0}),
            "helper_agent": _agent_config(),
        },
    )
    assert cost_calculator.resolve_model_pricing("planner_agent") == pytest.approx(0.5)
    assert cost_calculator.resolve_model_pricing("nav_agent") == pytest.approx(2.0)
    assert cost_calculator.resolve_model_pricing("helper_agent") is None


def test_resolve_model_pricing_without_role_searches_all_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_manager(
        monkeypatch,
        {
            "planner_agent": _agent_config(),
            "nav_agent": _agent_config({"price": 2.0}),
            "helper_agent": _agent_config({"price": 9.0}),
        },
    )
    # planner_agent is missing/unset, so the first configured role (nav_agent) wins.
    assert cost_calculator.resolve_model_pricing() == pytest.approx(2.0)


def test_resolve_model_pricing_accepts_raw_key_other_settings_and_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_manager(
        monkeypatch,
        {
            "planner_agent": _agent_config({"model_pricing": 2.5}),  # raw key, not normalized
            "nav_agent": _agent_config(other_settings={"price": 1.5}),  # top-level "price" lands in other_settings
            "helper_agent": _agent_config({"price": "0.75"}),  # numeric string from env/file loaders
        },
    )
    assert cost_calculator.resolve_model_pricing("planner_agent") == pytest.approx(2.5)
    assert cost_calculator.resolve_model_pricing("nav_agent") == pytest.approx(1.5)
    assert cost_calculator.resolve_model_pricing("helper_agent") == pytest.approx(0.75)


def test_resolve_model_pricing_returns_dict_pricing_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    pricing = {"input": 1.0, "output": 3.0}
    _install_fake_manager(monkeypatch, {"planner_agent": _agent_config({"price": pricing})})
    assert cost_calculator.resolve_model_pricing("planner_agent") is pricing


def test_resolve_model_pricing_returns_none_when_unset_or_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_manager(
        monkeypatch,
        {
            "planner_agent": _agent_config(),
            "nav_agent": _agent_config({"price": "not-a-number"}),
            "helper_agent": _agent_config(),
        },
    )
    assert cost_calculator.resolve_model_pricing() is None


def test_resolve_model_pricing_returns_none_when_manager_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenAgentsLLMConfigManager:
        @classmethod
        def get_instance(cls) -> "BrokenAgentsLLMConfigManager":
            raise RuntimeError("Config not initialized. Call initialize() first.")

    monkeypatch.setattr(cost_calculator, "AgentsLLMConfigManager", BrokenAgentsLLMConfigManager)
    assert cost_calculator.resolve_model_pricing("planner_agent") is None
    assert cost_calculator.resolve_model_pricing() is None


# ---------------------------------------------------------------------------
# Non-finite pricing and fractional token robustness
# ---------------------------------------------------------------------------


def test_estimate_cost_for_tokens_accepts_fractional_token_strings() -> None:
    # Token fields may arrive as float-ish strings (e.g. "321.5" from junit) —
    # coerce instead of crashing on bare int().
    assert cost_calculator.estimate_cost_for_tokens(1.0, {"prompt_tokens": "321.5", "completion_tokens": 0}) == pytest.approx(321 / 1_000_000)
    assert cost_calculator.estimate_cost_for_tokens(1.0, {"prompt_tokens": 10.9, "completion_tokens": 1.2}) == pytest.approx(11 / 1_000_000)


def test_estimate_cost_for_tokens_garbage_tokens_become_zero() -> None:
    assert cost_calculator.estimate_cost_for_tokens(1.0, {"prompt_tokens": "abc", "completion_tokens": None}) == 0.0


def test_estimate_cost_rejects_non_finite_pricing() -> None:
    # json.load accepts NaN/Infinity literals; they must be treated as missing
    # pricing rather than poisoning the cost math.
    assert cost_calculator.estimate_cost(float("nan"), 1_000, 1_000) is None
    assert cost_calculator.estimate_cost(float("inf"), 1_000, 1_000) is None
    assert cost_calculator.estimate_cost("nan", 1_000, 1_000) is None  # type: ignore[arg-type]
    assert cost_calculator.estimate_cost("Infinity", 1_000, 1_000) is None  # type: ignore[arg-type]
    assert cost_calculator.estimate_cost({"input": float("nan"), "output": 1.0}, 1_000, 1_000) is None
    assert cost_calculator.estimate_cost({"input": 1.0, "output": "-Infinity"}, 1_000, 1_000) is None  # type: ignore[arg-type]


def test_estimate_cost_overflow_returns_zero_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="testzeus_hercules.utils.cost_calculator"):
        result = cost_calculator.estimate_cost(1e308, 1_000_000, 0)
    assert result == 0.0
    assert any("non-finite" in r.message for r in caplog.records)


def test_estimate_cost_overflowing_token_counts_return_zero_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    # 10**400 does not fit a float: the estimate must degrade to 0.0, never raise.
    with caplog.at_level(logging.WARNING, logger="testzeus_hercules.utils.cost_calculator"):
        assert cost_calculator.estimate_cost(1.0, 10**400, 0) == 0.0
        assert cost_calculator.estimate_cost({"input": 1.0, "output": 3.0}, 0, 10**400) == 0.0
    assert caplog.records


def test_estimate_cost_for_tokens_huge_int_tokens_become_zero() -> None:
    # Token counts that overflow float coerce to "invalid" (0 tokens), not a crash.
    assert cost_calculator.estimate_cost_for_tokens(1.0, {"prompt_tokens": 10**400, "completion_tokens": 0}) == 0.0


def test_resolve_model_pricing_ignores_non_finite_pricing(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_manager(
        monkeypatch,
        {
            "planner_agent": _agent_config({"price": "nan"}),
            "nav_agent": _agent_config({"price": 2.0}),
            "helper_agent": _agent_config(),
        },
    )
    # planner's non-finite pricing is skipped, so the next configured role wins.
    assert cost_calculator.resolve_model_pricing() == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# SimpleHercules._build_cost_metrics estimation path
# ---------------------------------------------------------------------------


def test_build_cost_metrics_estimates_when_provider_cost_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cost_calculator, "resolve_model_pricing", lambda role=None: 0.5)
    final_state = {
        "total_prompt_tokens": 1_000_000,
        "total_completion_tokens": 1_000_000,
        "cost_available": False,
    }

    result = _bare_hercules()._build_cost_metrics(final_state)

    usage = result["usage_including_cached_inference"]
    assert usage["total_cost"] == pytest.approx(1.0)
    assert usage["cost_source"] == "estimated_from_model_pricing"
    assert "cost_unavailable" not in usage
    langgraph_usage = usage["langgraph"]
    assert langgraph_usage["cost"] == pytest.approx(1.0)
    assert langgraph_usage["cost_source"] == "estimated_from_model_pricing"
    assert "cost_unavailable" not in langgraph_usage
    # Token accounting stays untouched.
    assert langgraph_usage["prompt_tokens"] == 1_000_000
    assert langgraph_usage["completion_tokens"] == 1_000_000
    assert langgraph_usage["total_tokens"] == 2_000_000


def test_build_cost_metrics_estimation_supports_dict_pricing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cost_calculator, "resolve_model_pricing", lambda role=None: {"input": 1.0, "output": 3.0})
    final_state = {"total_prompt_tokens": 1_000_000, "total_completion_tokens": 1_000_000, "cost_available": False}

    usage = _bare_hercules()._build_cost_metrics(final_state)["usage_including_cached_inference"]

    assert usage["total_cost"] == pytest.approx(4.0)
    assert usage["cost_source"] == "estimated_from_model_pricing"


def test_build_cost_metrics_provider_reported_cost_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cost_calculator, "resolve_model_pricing", lambda role=None: 0.5)
    final_state = {
        "total_prompt_tokens": 1_000_000,
        "total_completion_tokens": 1_000_000,
        "cost_available": True,
        "total_cost": 0.25,
    }

    usage = _bare_hercules()._build_cost_metrics(final_state)["usage_including_cached_inference"]

    assert usage["total_cost"] == pytest.approx(0.25)
    assert usage["langgraph"]["cost"] == pytest.approx(0.25)
    assert "cost_source" not in usage
    assert "cost_unavailable" not in usage


def test_build_cost_metrics_estimates_when_provider_cost_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cost_calculator, "resolve_model_pricing", lambda role=None: 2.0)
    final_state = {
        "total_prompt_tokens": 100_000,
        "total_completion_tokens": 0,
        "cost_available": True,
        "total_cost": 0.0,  # reported but falsy -> estimate instead
    }

    usage = _bare_hercules()._build_cost_metrics(final_state)["usage_including_cached_inference"]

    assert usage["total_cost"] == pytest.approx(0.2)
    assert usage["cost_source"] == "estimated_from_model_pricing"


def test_build_cost_metrics_keeps_cost_unavailable_when_no_pricing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cost_calculator, "resolve_model_pricing", lambda role=None: None)
    final_state = {"total_prompt_tokens": 1_000, "total_completion_tokens": 2_000, "cost_available": False}

    usage = _bare_hercules()._build_cost_metrics(final_state)["usage_including_cached_inference"]

    assert usage["cost_unavailable"] is True
    assert usage["langgraph"]["cost_unavailable"] is True
    assert "total_cost" not in usage
    assert "cost_source" not in usage


def test_build_cost_metrics_keeps_cost_unavailable_when_no_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pricing is configured but there are no tokens to price.
    monkeypatch.setattr(cost_calculator, "resolve_model_pricing", lambda role=None: 0.5)
    final_state = {"total_prompt_tokens": 0, "total_completion_tokens": 0, "cost_available": False}

    usage = _bare_hercules()._build_cost_metrics(final_state)["usage_including_cached_inference"]

    assert usage["cost_unavailable"] is True
    assert usage["langgraph"]["cost_unavailable"] is True
    assert "total_cost" not in usage
    assert "cost_source" not in usage


def test_build_cost_metrics_survives_estimation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(role: str | None = None) -> float:
        raise RuntimeError("pricing lookup exploded")

    monkeypatch.setattr(cost_calculator, "resolve_model_pricing", _boom)
    final_state = {"total_prompt_tokens": 1_000, "total_completion_tokens": 2_000, "cost_available": False}

    usage = _bare_hercules()._build_cost_metrics(final_state)["usage_including_cached_inference"]

    assert usage["cost_unavailable"] is True
    assert "total_cost" not in usage
