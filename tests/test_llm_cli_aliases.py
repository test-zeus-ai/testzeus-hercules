import importlib.util
import logging
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "testzeus_hercules" / "config.py"


def _clear_testzeus_modules() -> None:
    for name in list(sys.modules):
        if name == "testzeus_hercules" or name.startswith("testzeus_hercules."):
            del sys.modules[name]


def _load_config_module(argv: list[str]):
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


def test_llm_base_url_alias_sets_model_base_url() -> None:
    module = _load_config_module(
        [
            "testzeus-hercules",
            "--llm-base-url",
            "https://openrouter.ai/api/v1",
            "--llm-model",
            "anthropic/claude-3-haiku",
            "--llm-model-api-key",
            "test-key",
        ]
    )

    config = module.get_global_conf().get_config()
    assert config["LLM_MODEL_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert config["LLM_MODEL_NAME"] == "anthropic/claude-3-haiku"
    assert config["LLM_MODEL_API_KEY"] == "test-key"


def test_canonical_llm_model_base_url_still_works() -> None:
    module = _load_config_module(
        [
            "testzeus-hercules",
            "--llm-model-base-url",
            "https://openrouter.ai/api/v1",
            "--llm-model",
            "anthropic/claude-3-haiku",
            "--llm-model-api-key",
            "test-key",
        ]
    )

    config = module.get_global_conf().get_config()
    assert config["LLM_MODEL_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert config["LLM_MODEL_NAME"] == "anthropic/claude-3-haiku"
