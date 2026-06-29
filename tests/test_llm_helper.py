from testzeus_hercules.utils.llm_helper import create_chat_model


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
