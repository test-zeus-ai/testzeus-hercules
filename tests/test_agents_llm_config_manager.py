"""Offline tests for AgentsLLMConfigManager hardening.

Covers:
- fail-fast ValueError when AGENTS_LLM_CONFIG_FILE is configured but missing
  or unreadable/invalid, while env-only fallback still works,
- change detection via config-version fingerprint (catches in-place dict
  mutations that id(config_dict) missed),
- per-role fallback to environment-derived config for partial provider
  stanzas.
"""

import json
import pathlib
from typing import Any, Generator

import pytest
import testzeus_hercules.core.agents_llm_config_manager as manager_module
from testzeus_hercules.config import SingletonConfigManager, set_global_conf
from testzeus_hercules.core.agents_llm_config_manager import AgentsLLMConfigManager


def _write_config(path: pathlib.Path, payload: dict[str, Any]) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _provider_config(model_name: str) -> dict[str, Any]:
    agent = {
        "model_name": model_name,
        "model_api_key": "test-key",
        "model_base_url": "http://localhost:4000",
    }
    return {role: dict(agent) for role in ("planner_agent", "nav_agent", "helper_agent")}


@pytest.fixture(autouse=True)
def _restore_singletons(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Snapshot and restore the config and agents-manager singletons."""
    monkeypatch.delenv("AGENTS_LLM_CONFIG_FILE", raising=False)
    monkeypatch.delenv("AGENTS_LLM_CONFIG_FILE_REF_KEY", raising=False)
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


# ---------------------------------------------------------------------------
# Configured-but-unusable AGENTS_LLM_CONFIG_FILE: warn + env fallback by
# default (origin/main), fail fast with STRICT_AGENTS_LLM_CONFIG=true
# ---------------------------------------------------------------------------


def _env_model_conf(config_file: str) -> None:
    set_global_conf(
        {
            "AGENTS_LLM_CONFIG_FILE": config_file,
            "AGENTS_LLM_CONFIG_FILE_REF_KEY": "litellm",
            "LLM_MODEL_NAME": "env-model",
            "LLM_MODEL_API_KEY": "env-key",
        },
        override=True,
    )


def test_missing_config_file_falls_back_to_env_by_default(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRICT_AGENTS_LLM_CONFIG", raising=False)
    _env_model_conf(str(tmp_path / "does_not_exist.json"))

    manager = AgentsLLMConfigManager.get_instance()

    assert manager.get_active_provider() == "environment"
    assert manager.get_agent_config("planner_agent")["model_config_params"]["model"] == "env-model"


def test_invalid_json_config_file_falls_back_to_env_by_default(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRICT_AGENTS_LLM_CONFIG", raising=False)
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    _env_model_conf(str(bad))

    manager = AgentsLLMConfigManager.get_instance()

    assert manager.get_active_provider() == "environment"
    assert manager.get_agent_config("planner_agent")["model_config_params"]["model"] == "env-model"


def test_missing_config_file_raises_at_initialize_when_strict(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRICT_AGENTS_LLM_CONFIG", "true")
    missing = tmp_path / "does_not_exist.json"
    set_global_conf(
        {"AGENTS_LLM_CONFIG_FILE": str(missing), "AGENTS_LLM_CONFIG_FILE_REF_KEY": "litellm"},
        override=True,
    )

    with pytest.raises(ValueError, match="does not exist"):
        AgentsLLMConfigManager.get_instance()


def test_invalid_json_config_file_raises_at_initialize_when_strict(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRICT_AGENTS_LLM_CONFIG", "true")
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    set_global_conf(
        {"AGENTS_LLM_CONFIG_FILE": str(bad), "AGENTS_LLM_CONFIG_FILE_REF_KEY": "litellm"},
        override=True,
    )

    with pytest.raises(ValueError, match="Failed to load agents LLM config file"):
        AgentsLLMConfigManager.get_instance()


def test_env_fallback_still_works_when_no_file_configured() -> None:
    # No AGENTS_LLM_CONFIG_FILE anywhere: env-derived config stays the fallback.
    conf = set_global_conf({"LLM_MODEL_NAME": "env-model", "LLM_MODEL_API_KEY": "env-key"}, override=True)
    assert not conf.get_config().get("AGENTS_LLM_CONFIG_FILE")

    manager = AgentsLLMConfigManager.get_instance()

    assert manager.get_active_provider() == "environment"
    cfg = manager.get_agent_config("planner_agent")
    assert cfg["model_config_params"]["model"] == "env-model"


# ---------------------------------------------------------------------------
# In-place config mutation detection (config-version fingerprint)
# ---------------------------------------------------------------------------


def test_inplace_config_mutation_triggers_reinit(tmp_path: pathlib.Path) -> None:
    file_a = _write_config(tmp_path / "a.json", {"litellm": _provider_config("model-a")})
    file_b = _write_config(tmp_path / "b.json", {"litellm": _provider_config("model-b")})

    conf = SingletonConfigManager.instance()
    # Raw in-place mutation — no reconstruction, no version bump.
    conf.get_config()["AGENTS_LLM_CONFIG_FILE"] = file_a
    conf.get_config()["AGENTS_LLM_CONFIG_FILE_REF_KEY"] = "litellm"

    manager = AgentsLLMConfigManager.get_instance()
    assert manager.get_agent_config("planner_agent")["model_config_params"]["model"] == "model-a"

    # Mutate in place again: get_instance() must re-init and pick up file_b.
    conf.get_config()["AGENTS_LLM_CONFIG_FILE"] = file_b
    manager = AgentsLLMConfigManager.get_instance()
    assert manager.get_agent_config("planner_agent")["model_config_params"]["model"] == "model-b"


def test_version_bump_alone_triggers_reinit(tmp_path: pathlib.Path) -> None:
    from testzeus_hercules.config import get_config_version

    file_a = _write_config(tmp_path / "a.json", {"litellm": _provider_config("model-a")})
    conf = SingletonConfigManager.instance()
    conf.get_config()["AGENTS_LLM_CONFIG_FILE"] = file_a
    conf.get_config()["AGENTS_LLM_CONFIG_FILE_REF_KEY"] = "litellm"

    manager = AgentsLLMConfigManager.get_instance()
    fingerprint_before = manager._last_global_config_fingerprint
    assert fingerprint_before[0] == get_config_version()

    # set_default_test_id bumps the version without changing watched keys —
    # the version component alone must trigger a re-init.
    conf.set_default_test_id("some-test")
    manager = AgentsLLMConfigManager.get_instance()
    assert manager._last_global_config_fingerprint[0] == get_config_version()
    assert manager._last_global_config_fingerprint[0] != fingerprint_before[0]


# ---------------------------------------------------------------------------
# Partial provider stanza -> per-role env fallback
# ---------------------------------------------------------------------------


def test_partial_stanza_falls_back_to_env_config(tmp_path: pathlib.Path) -> None:
    partial = _write_config(
        tmp_path / "partial.json",
        {"litellm": {"planner_agent": {"model_name": "file-model", "model_api_key": "k", "model_base_url": "http://file"}}},
    )
    set_global_conf(
        {
            "AGENTS_LLM_CONFIG_FILE": partial,
            "AGENTS_LLM_CONFIG_FILE_REF_KEY": "litellm",
            "LLM_MODEL_NAME": "env-model",
            "LLM_MODEL_API_KEY": "env-key",
            "LLM_MODEL_BASE_URL": "http://env",
        },
        override=True,
    )

    manager = AgentsLLMConfigManager.get_instance()

    # The role present in the file still resolves from the file...
    assert manager.get_agent_config("planner_agent")["model_config_params"]["model"] == "file-model"
    # ...while the missing role falls back to the environment-derived config.
    nav_cfg = manager.get_agent_config("nav_agent")
    assert nav_cfg["model_config_params"]["model"] == "env-model"
    assert nav_cfg["model_config_params"]["api_key"] == "env-key"


def test_missing_role_raises_when_env_config_also_unavailable(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    partial = _write_config(
        tmp_path / "partial.json",
        {"litellm": {"planner_agent": {"model_name": "file-model", "model_api_key": "k", "model_base_url": "http://file"}}},
    )
    set_global_conf(
        {"AGENTS_LLM_CONFIG_FILE": partial, "AGENTS_LLM_CONFIG_FILE_REF_KEY": "litellm"},
        override=True,
    )

    monkeypatch.setattr(manager_module.ConfigEnvLoader, "load_from_env", classmethod(lambda cls: {}))

    manager = AgentsLLMConfigManager.get_instance()
    with pytest.raises(ValueError, match="No configuration found for agent: nav_agent"):
        manager.get_agent_config("nav_agent")
