"""Token-limit parameter normalization for chat model configs (offline).

Chat models are always built on an OpenAI-compatible chat endpoint, so no
config may ever leave this module carrying the legacy completions parameter
name (``max_tokens_to_sample``); legacy names must be repaired into
``max_tokens``, never the reverse.
"""

from testzeus_hercules.utils.model_utils import adapt_llm_params_for_model


def test_claude_keeps_max_tokens_and_never_sets_legacy_name() -> None:
    params = adapt_llm_params_for_model("claude-3-5-sonnet", {"max_tokens": 1024})

    assert params["max_tokens"] == 1024
    assert "max_tokens_to_sample" not in params


def test_claude_repairs_legacy_max_tokens_to_sample() -> None:
    params = adapt_llm_params_for_model("anthropic/claude-3-opus", {"max_tokens_to_sample": 2048})

    assert params["max_tokens"] == 2048
    assert "max_tokens_to_sample" not in params


def test_claude_translates_max_completion_tokens_into_max_tokens() -> None:
    params = adapt_llm_params_for_model("claude-sonnet-4", {"max_completion_tokens": 512})

    assert params["max_tokens"] == 512
    assert "max_completion_tokens" not in params


def test_claude_defaults_to_max_tokens_4096() -> None:
    params = adapt_llm_params_for_model("claude-3-haiku", {})

    assert params["max_tokens"] == 4096
    assert "max_tokens_to_sample" not in params


def test_claude_prefers_max_tokens_over_legacy_name_when_both_present() -> None:
    params = adapt_llm_params_for_model("claude-3-5-sonnet", {"max_tokens": 800, "max_tokens_to_sample": 100})

    assert params["max_tokens"] == 800
    assert "max_tokens_to_sample" not in params


def test_non_claude_models_keep_max_tokens() -> None:
    params = adapt_llm_params_for_model("gpt-4o", {"max_tokens": 1234})

    assert params["max_tokens"] == 1234
    assert "max_tokens_to_sample" not in params
    assert "max_completion_tokens" not in params


def test_gpt5_still_converts_max_tokens_to_max_completion_tokens() -> None:
    params = adapt_llm_params_for_model("gpt-5", {"max_tokens": 900})

    assert params["max_completion_tokens"] == 900
    assert "max_tokens" not in params


def test_no_family_ever_emits_the_legacy_completions_name() -> None:
    for model_name in ("claude-3-5-sonnet", "claude-opus-4", "gpt-4o", "gpt-5-mini", "o3", "gemini-2.0-flash"):
        params = adapt_llm_params_for_model(model_name, {"max_tokens": 77})

        assert "max_tokens_to_sample" not in params, model_name
