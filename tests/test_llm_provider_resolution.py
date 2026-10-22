"""model_api_type / model_api_version / model_client_host are honoured (#37, #46, #48).

The example config advertises Azure, Ollama, Gemini, Groq, Mistral, DeepSeek and Anthropic entries; every
model is called through an OpenAI-style client, so the settings select the endpoint and client class.
"""

import pytest
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from testzeus_hercules.core.agents import high_level_planner_agent
from testzeus_hercules.utils.llm_helper import create_chat_model, resolve_provider_settings


@pytest.fixture(autouse=True)
def _no_ambient_openai_endpoint(monkeypatch) -> None:
    for name in ("OPENAI_BASE_URL", "OPENAI_API_BASE", "MODEL_API_KEY", "AZURE_OPENAI_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("api_type", "expected"),
    [
        ("google", "https://generativelanguage.googleapis.com/v1beta/openai/"),
        ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/"),
        ("groq", "https://api.groq.com/openai/v1"),
        ("mistral", "https://api.mistral.ai/v1"),
        ("deepseek", "https://api.deepseek.com"),
        ("anthropic", "https://api.anthropic.com/v1/"),
    ],
)
def test_api_type_selects_the_provider_endpoint(api_type, expected) -> None:
    model = create_chat_model({"model_name": "some-model", "model_api_key": "k", "model_api_type": api_type})

    assert isinstance(model, ChatOpenAI) and model.openai_api_base == expected


def test_explicit_base_url_wins_over_the_api_type_default() -> None:
    model = create_chat_model({"model_name": "m", "model_api_key": "k", "model_api_type": "mistral", "model_base_url": "https://proxy.example.com/v1"})

    assert model.openai_api_base == "https://proxy.example.com/v1"


def test_gemini_model_without_any_provider_settings_reaches_google() -> None:
    # `testzeus-hercules --llm-model gemini-2.0-flash --llm-model-api-key XXX` (no api type) used to hit api.openai.com.
    model = create_chat_model({"model": "gemini-2.0-flash", "api_key": "k"})

    assert model.openai_api_base == "https://generativelanguage.googleapis.com/v1beta/openai/"


def test_model_name_inference_is_skipped_when_an_openai_endpoint_is_configured_in_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example.com/v1")

    assert resolve_provider_settings({"model": "claude-sonnet-4", "api_key": "k"}, "claude-sonnet-4")["base_url"] is None


def test_azure_with_api_version_uses_the_azure_client() -> None:
    model = create_chat_model({"model_name": "gpt-4o", "model_api_key": "k", "model_api_type": "azure", "model_api_version": "2024-05-01-preview", "model_base_url": "https://res.openai.azure.com/"})

    assert isinstance(model, AzureChatOpenAI)
    assert model.azure_endpoint == "https://res.openai.azure.com/"
    assert model.openai_api_version == "2024-05-01-preview"
    assert model.deployment_name == "gpt-4o"


def test_azure_without_api_version_keeps_the_plain_client_for_v1_endpoints() -> None:
    model = create_chat_model({"model_name": "gpt-4o", "model_api_key": "k", "model_api_type": "azure", "model_base_url": "https://res.openai.azure.com/openai/v1/"})

    assert type(model) is ChatOpenAI and model.openai_api_base == "https://res.openai.azure.com/openai/v1/"


def test_ollama_client_host_becomes_the_openai_compatible_endpoint() -> None:
    model = create_chat_model({"model_name": "llama3", "model_api_type": "ollama", "model_client_host": "http://gpu-box:11434/"})

    assert model.openai_api_base == "http://gpu-box:11434/v1"


def test_ollama_defaults_to_localhost_and_needs_no_api_key() -> None:
    model = create_chat_model({"model_name": "llama3", "model_api_type": "ollama"})

    assert model.openai_api_base == "http://localhost:11434/v1"
    assert model.openai_api_key.get_secret_value()  # a placeholder, local servers ignore it


def test_local_base_url_needs_no_api_key() -> None:
    model = create_chat_model({"model_name": "qwen", "model_base_url": "http://127.0.0.1:8080/v1"})

    assert model.openai_api_base == "http://127.0.0.1:8080/v1"


def test_remote_endpoint_without_api_key_is_still_rejected() -> None:
    with pytest.raises(ValueError, match="API key"):
        create_chat_model({"model_name": "gpt-4o", "model_base_url": "https://api.example.com/v1"})


def test_openai_models_are_unchanged() -> None:
    model = create_chat_model({"model_name": "gpt-4o", "model_api_key": "k"})

    assert type(model) is ChatOpenAI


@pytest.mark.parametrize(
    ("model_config", "expected_class", "expected_attr"),
    [
        ({"model": "gpt-4o", "api_key": "k", "api_type": "azure", "api_version": "2024-05-01-preview", "base_url": "https://res.openai.azure.com/"}, AzureChatOpenAI, "azure_endpoint"),
        ({"model_name": "llama3", "model_api_type": "ollama", "model_client_host": "http://localhost:11434"}, ChatOpenAI, "openai_api_base"),
    ],
)
def test_planner_agent_honours_the_same_provider_settings(monkeypatch, model_config, expected_class, expected_attr) -> None:
    monkeypatch.setattr(high_level_planner_agent, "get_user_ltm", lambda: None)

    agent = high_level_planner_agent.PlannerAgent(model_config, {})

    assert type(agent.llm) is expected_class and getattr(agent.llm, expected_attr)
