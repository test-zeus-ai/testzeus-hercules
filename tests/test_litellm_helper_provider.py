"""Offline tests for ensure_litellm_provider / get_litellm_chat_model.

Covers:
- ensure_litellm_provider fixing AGENTS_LLM_CONFIG_FILE_REF_KEY in place
  (without rebuilding the singleton, which used to kill the process via
  _check_llm_config and reset runtime state).
- The missing-field warning firing on normalized params only, plus the
  empty-model-name warning with the documented gpt-4o fallback intact.
"""

import json
import logging
from typing import Any, Generator

import pytest
from testzeus_hercules.config import (
    SingletonConfigManager,
    get_config_version,
    get_global_conf,
    set_global_conf,
    set_global_conf_keys,
)
from testzeus_hercules.core.agents_llm_config_manager import AgentsLLMConfigManager
from testzeus_hercules.utils import litellm_helper


def _write_agent_config(path: Any, model_name: str = "proxy-model") -> str:
    agent = {
        "model_name": model_name,
        "model_api_key": "test-key",
        "model_base_url": "http://localhost:4000",
    }
    payload = {"litellm": {role: dict(agent) for role in ("planner_agent", "nav_agent", "helper_agent")}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def _restore_singletons() -> Generator[None, None, None]:
    """Snapshot and restore the config and agents-manager singletons so these
    tests cannot leak state into the rest of the suite."""
    conf_instance = SingletonConfigManager._instance
    saved_conf = dict(conf_instance.get_config()) if conf_instance is not None else None
    saved_agents = (
        AgentsLLMConfigManager._instance,
        AgentsLLMConfigManager._config,
        AgentsLLMConfigManager._last_global_config_fingerprint,
    )
    yield
    if conf_instance is not None and saved_conf is not None:
        conf_instance.get_config().clear()
        conf_instance.get_config().update(saved_conf)
    SingletonConfigManager._instance = conf_instance
    (
        AgentsLLMConfigManager._instance,
        AgentsLLMConfigManager._config,
        AgentsLLMConfigManager._last_global_config_fingerprint,
    ) = saved_agents


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep ambient env from influencing the singleton rebuilt by override=True.
    monkeypatch.delenv("AGENTS_LLM_CONFIG_FILE", raising=False)
    monkeypatch.delenv("AGENTS_LLM_CONFIG_FILE_REF_KEY", raising=False)


# ---------------------------------------------------------------------------
# Finding: ensure_litellm_provider must fix the ref key WITHOUT rebuilding the
# singleton (no process exit, no lost keys, no reset runtime state).
# ---------------------------------------------------------------------------


def test_ensure_litellm_provider_fixes_ref_key_in_place(tmp_path: Any) -> None:
    config_file = _write_agent_config(tmp_path / "agents_llm_config.json")

    # Programmatic config with a deliberately wrong ref key.
    set_global_conf(
        {
            "AGENTS_LLM_CONFIG_FILE": config_file,
            "AGENTS_LLM_CONFIG_FILE_REF_KEY": "wrong-provider",
            "MY_PROGRAMMATIC_KEY": "keep-me",
        },
        override=True,
    )

    # Must complete without SystemExit/raise (a singleton rebuild here used to
    # drop AGENTS_LLM_CONFIG_FILE and exit the process in _check_llm_config).
    litellm_helper.ensure_litellm_provider()

    cfg = get_global_conf().get_config()
    assert cfg["AGENTS_LLM_CONFIG_FILE_REF_KEY"] == "litellm"
    # Other keys — including the programmatically set ones — survive.
    assert cfg["AGENTS_LLM_CONFIG_FILE"] == config_file
    assert cfg["MY_PROGRAMMATIC_KEY"] == "keep-me"
    assert AgentsLLMConfigManager.get_instance().get_active_provider() == "litellm"


def test_ensure_litellm_provider_raises_when_no_file_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    set_global_conf({"MODE": "prod"}, override=True)
    with pytest.raises(ValueError, match="AGENTS_LLM_CONFIG_FILE"):
        litellm_helper.ensure_litellm_provider()


# ---------------------------------------------------------------------------
# Finding: warning checks must use the normalized params, and an empty
# model_name must warn while still returning the documented fallback model.
# ---------------------------------------------------------------------------


def test_get_litellm_chat_model_no_warning_for_fully_configured_agent(tmp_path: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = _write_agent_config(tmp_path / "agents_llm_config.json")
    set_global_conf(
        {
            "AGENTS_LLM_CONFIG_FILE": config_file,
            "AGENTS_LLM_CONFIG_FILE_REF_KEY": "litellm",
        },
        override=True,
    )

    sentinel = object()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        litellm_helper,
        "create_chat_model",
        lambda model_cfg, llm_params: (captured.update(model_cfg=model_cfg) or sentinel),
    )

    with caplog.at_level(logging.WARNING, logger="testzeus_hercules.utils.litellm_helper"):
        model = litellm_helper.get_litellm_chat_model("planner_agent")

    assert model is sentinel
    assert captured["model_cfg"]["model"] == "proxy-model"
    assert captured["model_cfg"]["api_key"] == "test-key"
    assert captured["model_cfg"]["base_url"] == "http://localhost:4000"
    assert not [r for r in caplog.records if "missing required field" in r.message]
    assert not [r for r in caplog.records if "empty model name" in r.message]


def test_get_litellm_chat_model_warns_and_falls_back_on_empty_model_name(tmp_path: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "agents_llm_config.json"
    agent = {
        # no model_name on purpose
        "model_api_key": "test-key",
        "model_base_url": "http://localhost:4000",
    }
    config_file.write_text(json.dumps({"litellm": {role: dict(agent) for role in ("planner_agent", "nav_agent", "helper_agent")}}), encoding="utf-8")
    set_global_conf(
        {
            "AGENTS_LLM_CONFIG_FILE": str(config_file),
            "AGENTS_LLM_CONFIG_FILE_REF_KEY": "litellm",
        },
        override=True,
    )

    sentinel = object()
    monkeypatch.setattr(litellm_helper, "create_chat_model", lambda model_cfg, llm_params: sentinel)

    with caplog.at_level(logging.WARNING, logger="testzeus_hercules.utils.litellm_helper"):
        model = litellm_helper.get_litellm_chat_model("planner_agent")

    # Warning emitted, and the documented gpt-4o fallback still yields a model.
    assert any("empty model name" in r.message for r in caplog.records)
    assert model is sentinel


def test_set_global_conf_keys_preserves_singleton_state() -> None:
    set_global_conf({"PRESERVE_ME": "yes"}, override=True)
    version_before = get_config_version()

    conf = set_global_conf_keys({"NEW_KEY": "value"})

    assert conf is get_global_conf()
    cfg = conf.get_config()
    assert cfg["NEW_KEY"] == "value"
    assert cfg["PRESERVE_ME"] == "yes"
    assert get_config_version() == version_before + 1
