from testzeus_hercules.config import NonSingletonConfigManager
from testzeus_hercules.utils.llm_helper import create_chat_model
from testzeus_hercules.utils.model_utils import adapt_llm_params_for_model
from testzeus_hercules.core.agents import high_level_planner_agent


def test_create_chat_model_sets_default_timeout_and_retry() -> None:
    model = create_chat_model({"model": "gpt-4o", "api_key": "test-key"}, {})

    assert model.request_timeout == 60.0
    assert model.max_retries == 1


def test_create_chat_model_honors_timeout_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT", "12.5")
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")

    model = create_chat_model({"model": "gpt-4o", "api_key": "test-key"}, {})

    assert model.request_timeout == 12.5
    assert model.max_retries == 0


def test_create_chat_model_strips_autogen_cache_seed() -> None:
    model = create_chat_model({"model": "gpt-4o", "api_key": "test-key"}, {"cache_seed": 42})

    assert "cache_seed" not in model.model_kwargs


def test_adapt_llm_params_strips_autogen_cache_seed() -> None:
    params = adapt_llm_params_for_model("gpt-4o", {"cache_seed": 42})

    assert "cache_seed" not in params


def test_create_chat_model_uses_openai_compatible_claude_params() -> None:
    model = create_chat_model(
        {"model": "claude-haiku-5-5", "api_key": "test-key"},
        {"max_tokens_to_sample": 1200, "temperature": 0.2, "top_p": None},
    )

    assert model.max_tokens == 1200
    assert "max_tokens_to_sample" not in model.model_kwargs
    assert model.temperature == 0.2
    assert "top_p" not in model.model_kwargs


def test_claude_legacy_token_name_normalizes_to_max_tokens() -> None:
    params = adapt_llm_params_for_model("claude-haiku-5-5", {"max_tokens_to_sample": 768})

    assert params["max_tokens"] == 768
    assert "max_tokens_to_sample" not in params


def test_claude_max_tokens_is_kept_and_wins_over_legacy_key() -> None:
    params = adapt_llm_params_for_model("claude-sonnet-4", {"max_tokens": 512, "max_tokens_to_sample": 999})

    assert params["max_tokens"] == 512
    assert "max_tokens_to_sample" not in params


def test_claude_defaults_max_tokens_when_unset() -> None:
    params = adapt_llm_params_for_model("claude-sonnet-4", {})

    assert params["max_tokens"] == 4096
    assert "max_tokens_to_sample" not in params


def test_gpt5_parameter_behavior_is_preserved() -> None:
    params = adapt_llm_params_for_model(
        "gpt-5-mini",
        {"max_tokens": 1024, "temperature": 0, "top_p": None},
    )

    assert params["max_completion_tokens"] == 1024
    assert "temperature" not in params
    assert "top_p" not in params


def test_planner_agent_strips_autogen_cache_seed(monkeypatch) -> None:
    monkeypatch.setattr(high_level_planner_agent, "get_user_ltm", lambda: None)

    agent = high_level_planner_agent.PlannerAgent({"model": "gpt-4o", "api_key": "test-key"}, {"cache_seed": 42})

    assert "cache_seed" not in agent.llm.model_kwargs


def test_planner_agent_normalizes_claude_params_for_chatopenai(monkeypatch) -> None:
    monkeypatch.setattr(high_level_planner_agent, "get_user_ltm", lambda: None)

    agent = high_level_planner_agent.PlannerAgent(
        {"model": "claude-haiku-5-5", "api_key": "test-key"},
        {"max_tokens_to_sample": 1200, "temperature": 0.1},
    )

    assert agent.llm.max_tokens == 1200
    assert agent.llm.temperature == 0.1
    assert "max_tokens_to_sample" not in agent.llm.model_kwargs


def test_full_config_normalization_uses_openai_compatible_token_key() -> None:
    config = NonSingletonConfigManager(
        {
            "LLM_MODEL_NAME": "claude-haiku-5-5",
            "LLM_MODEL_TEMPERATURE": 0.1,
            "LLM_MODEL_MAX_TOKENS": 1200,
        },
        ignore_env=True,
    )

    params = config.get_adapted_llm_params()

    assert params["max_tokens"] == 1200
    assert "max_tokens_to_sample" not in params
