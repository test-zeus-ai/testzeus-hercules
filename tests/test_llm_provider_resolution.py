"""model_api_type / model_api_version / model_client_host are honoured (#37, #46, #48).

The example config advertises Azure, Ollama, Gemini, Groq, Mistral, DeepSeek and Anthropic entries; every
model is called through an OpenAI-style client, so the settings select the endpoint and client class.
"""

import pytest
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from testzeus_hercules.core.agents import high_level_planner_agent
from testzeus_hercules.core.agents_llm_config import AgentsLLMConfig
from testzeus_hercules.utils.llm_helper import convert_model_config_to_langchain_format, create_chat_model, resolve_provider_settings


@pytest.fixture(autouse=True)
def _no_ambient_openai_endpoint(monkeypatch) -> None:
    for name in ("OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_API_KEY", "MODEL_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"):
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
    assert model.azure_endpoint == "https://res.openai.azure.com"  # reduced to scheme + host; the client adds /openai/deployments/...
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


# ── the real runtime path: config file -> AgentsLLMConfig -> converter -> client ──────────────────────────


def _through_runtime_config(raw: dict) -> dict:
    """What the agents actually receive: normalize_agent_config, then the langchain converter."""
    normalized = AgentsLLMConfig().normalize_agent_config(raw)
    return convert_model_config_to_langchain_format(normalized["model_config_params"])


def test_azure_settings_survive_the_runtime_config_path() -> None:
    raw = {"model_name": "gpt-4o", "model_api_key": "k", "model_api_type": "azure", "model_api_version": "2024-05-01-preview", "model_base_url": "https://res.openai.azure.com/"}

    model = create_chat_model(_through_runtime_config(raw), {})

    assert isinstance(model, AzureChatOpenAI)
    assert model.openai_api_version == "2024-05-01-preview" and model.deployment_name == "gpt-4o"


def test_ollama_client_host_survives_the_runtime_config_path() -> None:
    raw = {"model_name": "llama3", "model_api_type": "ollama", "model_client_host": "http://gpu-box:11434"}

    assert create_chat_model(_through_runtime_config(raw), {}).openai_api_base == "http://gpu-box:11434/v1"


@pytest.mark.parametrize("raw_type", ["azure", "azure_openai", "azure-openai", "Azure-OpenAI"])
def test_azure_api_type_spellings_are_equivalent(raw_type) -> None:
    raw = {"model_name": "gpt-4o", "model_api_key": "k", "model_api_type": raw_type, "model_api_version": "2024-05-01-preview", "model_base_url": "https://res.openai.azure.com/"}

    assert isinstance(create_chat_model(_through_runtime_config(raw), {}), AzureChatOpenAI)


def test_planner_agent_gets_azure_from_the_runtime_config_path(monkeypatch) -> None:
    monkeypatch.setattr(high_level_planner_agent, "get_user_ltm", lambda: None)
    raw = {"model_name": "gpt-4o", "model_api_key": "k", "model_api_type": "azure", "model_api_version": "2024-05-01-preview", "model_base_url": "https://res.openai.azure.com/"}

    agent = high_level_planner_agent.PlannerAgent(_through_runtime_config(raw), {})

    assert isinstance(agent.llm, AzureChatOpenAI)


# ── Azure endpoint handling ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "configured",
    [
        "https://res.openai.azure.com",
        "https://res.openai.azure.com/",
        "https://res.openai.azure.com/openai/deployments/gpt-4o",
        "https://res.openai.azure.com/openai/deployments/gpt-4o/chat/completions",
    ],
)
def test_azure_endpoint_is_reduced_to_scheme_and_host(configured) -> None:
    model = create_chat_model({"model_name": "gpt-4o", "model_api_key": "k", "model_api_type": "azure", "model_api_version": "2024-05-01-preview", "model_base_url": configured})

    assert model.azure_endpoint == "https://res.openai.azure.com"


def test_azure_v1_endpoint_with_an_api_version_uses_the_plain_client() -> None:
    model = create_chat_model(
        {"model_name": "gpt-4o", "model_api_key": "k", "model_api_type": "azure", "model_api_version": "2024-05-01-preview", "model_base_url": "https://res.openai.azure.com/openai/v1/"}
    )

    assert type(model) is ChatOpenAI and model.openai_api_base == "https://res.openai.azure.com/openai/v1/"


def test_azure_without_an_endpoint_fails_with_a_clear_message() -> None:
    with pytest.raises(ValueError, match="needs the resource endpoint"):
        create_chat_model({"model_name": "gpt-4o", "model_api_key": "k", "model_api_type": "azure", "model_api_version": "2024-05-01-preview"})


def test_azure_endpoint_and_key_can_come_from_the_azure_environment(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://env.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "env-key")

    model = create_chat_model({"model_name": "gpt-4o", "model_api_type": "azure", "model_api_version": "2024-05-01-preview"})

    assert isinstance(model, AzureChatOpenAI)
    assert str(model.client._client.base_url) == "https://env.openai.azure.com/openai/deployments/gpt-4o/"


def test_timeout_and_retries_reach_the_azure_client() -> None:
    model = create_chat_model({"model_name": "gpt-4o", "model_api_key": "k", "model_api_type": "azure", "model_api_version": "2024-05-01-preview", "model_base_url": "https://r.openai.azure.com"}, {})

    assert model.request_timeout == 60.0 and model.max_retries == 1


# ── precedence ──────────────────────────────────────────────────────────────────────────────────────────


def test_openai_endpoint_from_the_environment_is_not_replaced_by_an_api_type_default(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.corp/v1")

    settings = resolve_provider_settings({"model": "claude-sonnet-4", "api_key": "k", "api_type": "anthropic"}, "claude-sonnet-4")

    assert settings["base_url"] is None  # the client then reads OPENAI_BASE_URL itself


def test_ollama_explicit_base_url_wins_over_client_host() -> None:
    model = create_chat_model({"model_name": "llama3", "model_api_type": "ollama", "model_base_url": "http://proxy:4000/v1", "model_client_host": "http://gpu-box:11434"})

    assert model.openai_api_base == "http://proxy:4000/v1"


def test_ollama_client_host_without_a_scheme_gets_one() -> None:
    model = create_chat_model({"model_name": "llama3", "model_api_type": "ollama", "model_client_host": "gpu-box:11434"})

    assert model.openai_api_base == "http://gpu-box:11434/v1"


# ── keyless local endpoints ─────────────────────────────────────────────────────────────────────────────


def test_local_endpoint_uses_the_environment_api_key_when_there_is_one(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "proxy-key")
    model = create_chat_model({"model_name": "qwen", "model_base_url": "http://localhost:4000/v1"})

    assert model.openai_api_key.get_secret_value() == "proxy-key"


def test_planner_keeps_the_environment_api_key_for_a_local_proxy(monkeypatch) -> None:
    monkeypatch.setattr(high_level_planner_agent, "get_user_ltm", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "proxy-key")

    agent = high_level_planner_agent.PlannerAgent({"model_name": "qwen", "base_url": "http://localhost:4000/v1"}, {})

    assert agent.llm.openai_api_key.get_secret_value() == "proxy-key"


def test_planner_uses_a_placeholder_key_only_when_no_key_exists(monkeypatch) -> None:
    monkeypatch.setattr(high_level_planner_agent, "get_user_ltm", lambda: None)

    agent = high_level_planner_agent.PlannerAgent({"model_name": "llama3", "model_api_type": "ollama"}, {})

    assert agent.llm.openai_api_key.get_secret_value() == "not-needed"


# ── credentials: one resolution for the planner and the navigation agents ───────────────────────────────


def _planner_key(monkeypatch, config: dict) -> str | None:
    monkeypatch.setattr(high_level_planner_agent, "get_user_ltm", lambda: None)
    key = high_level_planner_agent.PlannerAgent(config, {}).llm.openai_api_key
    return key.get_secret_value() if key else None


def test_planner_uses_model_api_key_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "env-model-key")
    config = {"model": "gpt-4o", "base_url": "https://api.example.com/v1"}

    assert _planner_key(monkeypatch, config) == "env-model-key"
    assert create_chat_model(config).openai_api_key.get_secret_value() == "env-model-key"


def test_planner_accepts_the_raw_model_api_key(monkeypatch) -> None:
    config = {"model_name": "gpt-4o", "model_api_key": "raw-key", "model_base_url": "https://api.example.com/v1"}

    assert _planner_key(monkeypatch, config) == "raw-key"
    assert create_chat_model(config).openai_api_key.get_secret_value() == "raw-key"


def test_planner_uses_azure_openai_api_key_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-env-key")
    config = {"model": "gpt-4o", "api_type": "azure", "api_version": "2024-05-01-preview", "base_url": "https://res.openai.azure.com"}
    monkeypatch.setattr(high_level_planner_agent, "get_user_ltm", lambda: None)

    agent = high_level_planner_agent.PlannerAgent(config, {})

    assert isinstance(agent.llm, AzureChatOpenAI) and agent.llm.openai_api_key.get_secret_value() == "azure-env-key"


@pytest.mark.parametrize(
    "config",
    [
        {"model": "gpt-4o", "api_key": "k", "base_url": "https://api.example.com/v1"},
        {"model": "llama3", "api_type": "ollama", "client_host": "http://gpu-box:11434"},
        {"model": "qwen", "base_url": "http://127.0.0.1:4000/v1"},
        {"model": "gemini-2.0-flash", "api_key": "g"},
    ],
)
def test_planner_and_navigation_agents_resolve_the_same_endpoint_and_key(monkeypatch, config) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-other-service")
    monkeypatch.setattr(high_level_planner_agent, "get_user_ltm", lambda: None)

    planner = high_level_planner_agent.PlannerAgent(dict(config), {}).llm
    nav = create_chat_model(dict(config), {})

    assert planner.openai_api_base == nav.openai_api_base
    assert planner.openai_api_key.get_secret_value() == nav.openai_api_key.get_secret_value()


# ── the real OPENAI_API_KEY must not be sent to an Ollama host ──────────────────────────────────────────


@pytest.mark.parametrize("host", ["http://gpu-box:11434", "gpu-box:11434", "http://localhost:11434"])
def test_ollama_never_receives_the_environment_openai_key(monkeypatch, host) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-secret")
    config = {"model_name": "llama3", "model_api_type": "ollama", "model_client_host": host}

    assert create_chat_model(config).openai_api_key.get_secret_value() == "not-needed"
    assert _planner_key(monkeypatch, {"model_name": "llama3", "model_api_type": "ollama", "model_client_host": host}) == "not-needed"


# ── Azure next to an OpenAI gateway configured in the environment ───────────────────────────────────────


@pytest.mark.parametrize("variable", ["OPENAI_BASE_URL", "OPENAI_API_BASE"])
def test_azure_client_ignores_an_openai_gateway_in_the_environment(monkeypatch, variable) -> None:
    monkeypatch.setenv(variable, "https://gateway.corp/v1")
    config = {"model_name": "gpt-4o", "model_api_key": "k", "model_api_type": "azure", "model_api_version": "2024-05-01-preview", "model_base_url": "https://res.openai.azure.com"}

    model = create_chat_model(config)

    assert isinstance(model, AzureChatOpenAI)
    assert str(model.client._client.base_url) == "https://res.openai.azure.com/openai/deployments/gpt-4o/"


# ── Ollama: the placeholder decision comes before any real key ──────────────────────────────────────────


@pytest.mark.parametrize("env_name", ["MODEL_API_KEY", "OPENAI_API_KEY"])
@pytest.mark.parametrize("config_key", [None, "sk-config-key"])
def test_ollama_lan_host_never_receives_a_real_key(monkeypatch, env_name, config_key) -> None:
    monkeypatch.setenv(env_name, "sk-real-secret")
    config = {"model_name": "llama3", "model_api_type": "ollama", "model_client_host": "http://192.168.1.20:11434"}
    if config_key:
        config["model_api_key"] = config_key

    assert create_chat_model(dict(config)).openai_api_key.get_secret_value() == "not-needed"
    assert _planner_key(monkeypatch, dict(config)) == "not-needed"


# ── Azure credentials: key for both client styles, Entra ID token when there is no key ───────────────────

_AZURE_V1 = {"model_name": "gpt-4o", "model_api_type": "azure", "model_api_version": "2025-01-01", "model_base_url": "https://res.openai.azure.com/openai/v1/"}
_AZURE_CLASSIC = {"model_name": "gpt-4o", "model_api_type": "azure", "model_api_version": "2024-05-01-preview", "model_base_url": "https://res.openai.azure.com/"}


def test_azure_v1_endpoint_uses_azure_openai_api_key(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-env-key")

    model = create_chat_model(dict(_AZURE_V1))

    assert type(model) is ChatOpenAI and model.openai_api_key.get_secret_value() == "azure-env-key"
    assert _planner_key(monkeypatch, dict(_AZURE_V1)) == "azure-env-key"


def test_azure_v1_endpoint_without_api_version_uses_azure_openai_api_key(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-env-key")
    config = {key: value for key, value in _AZURE_V1.items() if key != "model_api_version"}

    assert create_chat_model(config).openai_api_key.get_secret_value() == "azure-env-key"


def test_azure_entra_token_is_passed_to_the_azure_client(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_AD_TOKEN", "entra-token")

    model = create_chat_model(dict(_AZURE_CLASSIC))

    assert isinstance(model, AzureChatOpenAI) and model.azure_ad_token.get_secret_value() == "entra-token"
    assert model.openai_api_key is None


def test_azure_entra_token_is_the_bearer_token_on_the_v1_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_AD_TOKEN", "entra-token")

    model = create_chat_model(dict(_AZURE_V1))

    assert type(model) is ChatOpenAI and model.openai_api_key.get_secret_value() == "entra-token"


def test_planner_accepts_the_azure_entra_token(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_AD_TOKEN", "entra-token")
    monkeypatch.setattr(high_level_planner_agent, "get_user_ltm", lambda: None)

    agent = high_level_planner_agent.PlannerAgent(dict(_AZURE_CLASSIC), {})

    assert isinstance(agent.llm, AzureChatOpenAI) and agent.llm.azure_ad_token.get_secret_value() == "entra-token"


def test_azure_api_key_wins_over_the_entra_token(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_AD_TOKEN", "entra-token")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-env-key")

    model = create_chat_model(dict(_AZURE_CLASSIC))

    assert model.openai_api_key.get_secret_value() == "azure-env-key"


def test_azure_without_any_credential_is_still_rejected() -> None:
    with pytest.raises(ValueError, match="API key"):
        create_chat_model(dict(_AZURE_V1))


def test_azure_entra_token_is_ignored_for_other_providers(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_AD_TOKEN", "entra-token")

    with pytest.raises(ValueError, match="API key"):
        create_chat_model({"model_name": "gpt-4o", "model_base_url": "https://api.example.com/v1"})


# ── keyless detection: local servers reached by more than localhost ─────────────────────────────────────


@pytest.mark.parametrize(
    "base_url",
    [
        "http://host.docker.internal:8000/v1",
        "http://host.containers.internal:8000/v1",
        "http://0.0.0.0:8000/v1",
        "http://192.168.1.50:8000/v1",
        "http://10.1.2.3:8000/v1",
        "http://172.16.5.4:8000/v1",
        "http://172.31.255.254:8000/v1",
        "http://169.254.10.10:8000/v1",
        "http://[fd12:3456:789a::1]:8000/v1",
        "http://[fe80::1]:8000/v1",
        "http://[::1]:8000/v1",
        "http://gpu-box.local:8000/v1",
        "http://vllm.localhost:8000/v1",
        "http://LOCALHOST:8000/v1",
    ],
)
def test_local_and_private_base_urls_need_no_api_key(base_url) -> None:
    model = create_chat_model({"model_name": "qwen", "model_base_url": base_url})

    assert model.openai_api_base == base_url and model.openai_api_key.get_secret_value() == "not-needed"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.example.com/v1",
        "http://vllm.mycorp.com:8000/v1",
        "http://8.8.8.8:8000/v1",
        "http://172.32.0.1:8000/v1",  # just outside 172.16.0.0/12
        "http://192.169.1.1:8000/v1",
        "http://[2001:4860::1]:8000/v1",
        "https://notlocal.com/v1",
        "https://localhost.example.com/v1",
        "https://mylocal.example/v1",
    ],
)
def test_public_base_urls_still_require_an_api_key(base_url) -> None:
    with pytest.raises(ValueError, match="API key"):
        create_chat_model({"model_name": "gpt-4o", "model_base_url": base_url})


def test_private_base_url_keeps_an_explicit_api_key(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "gateway-key")

    model = create_chat_model({"model_name": "qwen", "model_base_url": "http://host.docker.internal:4000/v1"})

    assert model.openai_api_key.get_secret_value() == "gateway-key"
