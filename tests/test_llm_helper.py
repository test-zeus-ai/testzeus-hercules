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


def test_planner_agent_strips_autogen_cache_seed(monkeypatch) -> None:
    monkeypatch.setattr(high_level_planner_agent, "get_user_ltm", lambda: None)

    agent = high_level_planner_agent.PlannerAgent({"model": "gpt-4o", "api_key": "test-key"}, {"cache_seed": 42})

    assert "cache_seed" not in agent.llm.model_kwargs
