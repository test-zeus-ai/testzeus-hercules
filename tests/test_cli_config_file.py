import importlib.util
import json
import logging
import os
import pathlib
import sys
import types
from typing import Any, Generator

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "testzeus_hercules" / "config.py"


def _clear_testzeus_modules() -> None:
    for name in list(sys.modules):
        if name == "testzeus_hercules" or name.startswith("testzeus_hercules."):
            del sys.modules[name]


def _load_config_module(argv: list[str]) -> Any:
    """Load testzeus_hercules.config in isolation, stubbing internal package
    init so we don't drag in core/agents/tools (which require system deps)."""
    _clear_testzeus_modules()

    pkg = types.ModuleType("testzeus_hercules")
    pkg.__path__ = [str(ROOT / "testzeus_hercules")]
    sys.modules["testzeus_hercules"] = pkg

    utils_pkg = types.ModuleType("testzeus_hercules.utils")
    utils_pkg.__path__ = [str(ROOT / "testzeus_hercules" / "utils")]
    sys.modules["testzeus_hercules.utils"] = utils_pkg

    logger_mod = types.ModuleType("testzeus_hercules.utils.logger")
    logger_mod.logger = logging.getLogger("test-config")
    sys.modules["testzeus_hercules.utils.logger"] = logger_mod

    timestamp_mod = types.ModuleType("testzeus_hercules.utils.timestamp_helper")
    timestamp_mod.get_timestamp_str = lambda: "0"
    sys.modules["testzeus_hercules.utils.timestamp_helper"] = timestamp_mod

    telemetry_mod = types.ModuleType("testzeus_hercules.telemetry")

    class EventData:  # noqa: D401
        pass

    class EventType:  # noqa: D401
        pass

    telemetry_mod.EventData = EventData
    telemetry_mod.EventType = EventType
    telemetry_mod.add_event = lambda *args, **kwargs: None
    sys.modules["testzeus_hercules.telemetry"] = telemetry_mod

    old_argv = sys.argv[:]
    sys.argv = argv[:]
    try:
        spec = importlib.util.spec_from_file_location("testzeus_hercules.config", CONFIG_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["testzeus_hercules.config"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.argv = old_argv


@pytest.fixture(autouse=True)
def _set_test_env() -> Generator[None, None, None]:
    os.environ["IS_TEST_ENV"] = "true"
    yield


def test_test_env_import_skips_strict_llm_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IS_TEST_ENV", "true")
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
    monkeypatch.delenv("LLM_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("AGENTS_LLM_CONFIG_FILE", "agents_llm_config.json")
    monkeypatch.delenv("AGENTS_LLM_CONFIG_FILE_REF_KEY", raising=False)

    module = _load_config_module(["testzeus-hercules"])
    cfg = module.get_global_conf().get_config()

    assert cfg["AGENTS_LLM_CONFIG_FILE"] == "agents_llm_config.json"
    assert "AGENTS_LLM_CONFIG_FILE_REF_KEY" not in cfg


def test_ignore_env_skips_strict_llm_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_config_module(["testzeus-hercules"])
    monkeypatch.setenv("IS_TEST_ENV", "false")

    cfg = module.NonSingletonConfigManager(
        {"AGENTS_LLM_CONFIG_FILE": "agents_llm_config.json"},
        ignore_env=True,
    ).get_config()

    assert cfg["AGENTS_LLM_CONFIG_FILE"] == "agents_llm_config.json"
    assert "AGENTS_LLM_CONFIG_FILE_REF_KEY" not in cfg


def test_max_completion_tokens_env_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL_MAX_COMPLETION_TOKENS", "1234")

    module = _load_config_module(["testzeus-hercules"])
    cfg = module.get_global_conf().get_config()

    assert cfg["LLM_MODEL_MAX_COMPLETION_TOKENS"] == "1234"


def test_yaml_config_file_is_loaded_via_cli_flag(tmp_path: pathlib.Path) -> None:
    config_file = tmp_path / "hercules.config.yaml"
    config_file.write_text(
        "\n".join(
            [
                f"input_file: {tmp_path / 'input' / 'test.feature'}",
                f"output_path: {tmp_path / 'output'}",
                f"test_data_path: {tmp_path / 'test_data'}",
                f"agents_llm_config_file: {tmp_path / 'agents_llm_config.json'}",
                "agents_llm_config_file_ref_key: huggingface",
                "browser: chromium",
                "headless: false",
                "save_proofs: true",
            ]
        ),
        encoding="utf-8",
    )

    module = _load_config_module(["testzeus-hercules", "--config", str(config_file)])
    cfg = module.get_global_conf().get_config()

    assert cfg["INPUT_GHERKIN_FILE_PATH"] == str(tmp_path / "input" / "test.feature")
    assert cfg["JUNIT_XML_BASE_PATH"] == str(tmp_path / "output")
    assert cfg["TEST_DATA_PATH"] == str(tmp_path / "test_data")
    assert cfg["AGENTS_LLM_CONFIG_FILE"] == str(tmp_path / "agents_llm_config.json")
    assert cfg["AGENTS_LLM_CONFIG_FILE_REF_KEY"] == "huggingface"
    assert cfg["BROWSER_TYPE"] == "chromium"
    assert cfg["HEADLESS"] == "false"
    assert cfg["TAKE_SCREENSHOTS"] == "true"


def test_json_config_file_is_loaded_via_cli_flag(tmp_path: pathlib.Path) -> None:
    config_file = tmp_path / "hercules.config.json"
    config_file.write_text(
        json.dumps(
            {
                "input_file": str(tmp_path / "input.feature"),
                "llm_model": "from-json",
                "llm_model_api_key": "json-key",
                "headless": True,
            }
        ),
        encoding="utf-8",
    )

    module = _load_config_module(["testzeus-hercules", "--config", str(config_file)])
    cfg = module.get_global_conf().get_config()

    assert cfg["INPUT_GHERKIN_FILE_PATH"] == str(tmp_path / "input.feature")
    assert cfg["LLM_MODEL_NAME"] == "from-json"
    assert cfg["LLM_MODEL_API_KEY"] == "json-key"
    assert cfg["HEADLESS"] == "true"


def test_cli_flags_override_config_file_values(tmp_path: pathlib.Path) -> None:
    config_file = tmp_path / "hercules.config.yaml"
    config_file.write_text(
        f"input_file: {tmp_path / 'from-config.feature'}\nllm_model: from-config\nllm_model_api_key: secret\n",
        encoding="utf-8",
    )

    module = _load_config_module(
        [
            "testzeus-hercules",
            "--config",
            str(config_file),
            "--input-file",
            str(tmp_path / "from-cli.feature"),
        ]
    )
    cfg = module.get_global_conf().get_config()

    assert cfg["INPUT_GHERKIN_FILE_PATH"] == str(tmp_path / "from-cli.feature")
    assert cfg["LLM_MODEL_NAME"] == "from-config"
    assert cfg["LLM_MODEL_API_KEY"] == "secret"


def test_llm_temperature_cli_does_not_clear_agents_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTS_LLM_CONFIG_FILE", "agents_llm_config.json")
    monkeypatch.setenv("AGENTS_LLM_CONFIG_FILE_REF_KEY", "litellm")
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
    monkeypatch.delenv("LLM_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL_API_TYPE", raising=False)

    module = _load_config_module(["testzeus-hercules", "--llm-temperature", "0.2"])
    cfg = module.get_global_conf().get_config()

    assert cfg["AGENTS_LLM_CONFIG_FILE"] == "agents_llm_config.json"
    assert cfg["AGENTS_LLM_CONFIG_FILE_REF_KEY"] == "litellm"
    assert cfg["LLM_MODEL_TEMPERATURE"] == "0.2"
