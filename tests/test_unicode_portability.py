"""Unicode/BOM portability tests for the config-loading entry points.

Windows text editors routinely save JSON configs with a leading UTF-8 BOM; the
config loaders opened files through the locale codec, so ``json.load`` crashed
at startup with ``json.JSONDecodeError: Unexpected UTF-8 BOM``. These tests pin
``encoding="utf-8-sig"`` behavior on both loaders
(core/agents_llm_config.py and core/config_file_loader.py).
"""

import json

from testzeus_hercules.core.agents_llm_config import AgentsLLMConfig
from testzeus_hercules.core.config_file_loader import ConfigFileLoader

BOM_BYTES = b"\xef\xbb\xbf"

CONFIG_PAYLOAD = {"litellm": {role: {"model_name": "gpt-4o", "model_api_key": "test-key", "model_base_url": "http://localhost:4000"} for role in ("planner_agent", "nav_agent", "helper_agent")}}


def _write_bom_config(path, payload=None) -> str:
    """Write the payload as UTF-8 JSON prefixed with a BOM, like a Windows save."""
    data = CONFIG_PAYLOAD if payload is None else payload
    path.write_bytes(BOM_BYTES + json.dumps(data).encode("utf-8"))
    return str(path)


def test_config_file_loader_accepts_bom_config(tmp_path) -> None:
    config_path = _write_bom_config(tmp_path / "agents.json")

    loaded = ConfigFileLoader.load_from_file(config_path)

    assert "litellm" in loaded
    assert loaded["litellm"]["nav_agent"]["model_name"] == "gpt-4o"


def test_config_file_loader_accepts_bom_config_with_ref_key(tmp_path) -> None:
    config_path = _write_bom_config(tmp_path / "agents.json")

    loaded = ConfigFileLoader.load_from_file(config_path, ref_key="litellm")

    assert list(loaded) == ["litellm"]
    assert loaded["litellm"]["planner_agent"]["model_api_key"] == "test-key"


def test_agents_llm_config_load_from_file_accepts_bom_config(tmp_path) -> None:
    config_path = _write_bom_config(tmp_path / "agents.json")
    config = AgentsLLMConfig()

    config.load_from_file(config_path, provider_key="litellm")

    assert config.get_active_provider() == "litellm"
    agent = config.get_agent_config("nav_agent")
    assert agent is not None
    assert agent["model_config_params"]["model"] == "gpt-4o"


def test_loaders_still_accept_bomless_utf8_and_non_ascii_values(tmp_path) -> None:
    """utf-8-sig must not change plain-BOM-less behavior, and values may be Unicode."""
    payload = {"litellm": {"nav_agent": {"model_name": "测试模型", "final_note": "Успех ✓"}}}
    config_path = tmp_path / "agents.json"
    config_path.write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    loaded = ConfigFileLoader.load_from_file(str(config_path))
    assert loaded["litellm"]["nav_agent"]["model_name"] == "测试模型"

    config = AgentsLLMConfig()
    config.load_from_file(str(config_path), provider_key="litellm")
    assert config.get_agent_config("nav_agent")["other_settings"]["final_note"] == "Успех ✓"
