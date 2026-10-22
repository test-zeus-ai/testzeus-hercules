import importlib.util
import json
import logging
import os
import pathlib
import sys
import threading
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
    # Snapshot modules so the purge below doesn't leak: other tests keep
    # references to module objects and lazy imports would silently re-import
    # fresh (unpatched) copies after eviction.
    saved_modules = {name: mod for name, mod in sys.modules.items() if name == "testzeus_hercules" or name.startswith("testzeus_hercules.")}
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
        for name in [n for n in sys.modules if n == "testzeus_hercules" or n.startswith("testzeus_hercules.")]:
            del sys.modules[name]
        sys.modules.update(saved_modules)


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


# --- _should_parse_cli_args: parse unless explicitly disabled -----------------
# Subcommand CLIs (dashboard etc.) own their argparse flags and are skipped by
# name; every other entrypoint parses the shared flags. ``--port`` from another
# CLI must never trip over the ambiguous ``--portkey-*`` prefix.


def test_dashboard_argv0_with_port_arg_does_not_parse_cli_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXECUTE_BULK", raising=False)

    module = _load_config_module(["hercules-dashboard", "--port", "8000", "--bulk"])

    cfg = module.get_global_conf().get_config()
    assert cfg["EXECUTE_BULK"] == "false"  # base config did not swallow subcommand flags


def test_unknown_entrypoint_still_parses_cli_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXECUTE_BULK", raising=False)

    # A custom wrapper script / other entrypoint used to be silently ignored.
    module = _load_config_module(["/usr/local/bin/my-wrapper", "--bulk"])

    assert module.get_global_conf().get_config()["EXECUTE_BULK"] == "true"


def test_unknown_entrypoint_ignores_ambiguous_foreign_port_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXECUTE_BULK", raising=False)

    # "--port" is an ambiguous prefix of the --portkey-* flags; for non-legacy
    # entrypoints abbreviations are off so it is ignored instead of SystemExit(2).
    module = _load_config_module(["my-wrapper", "--port", "8000", "--bulk"])

    assert module.get_global_conf().get_config()["EXECUTE_BULK"] == "true"


def test_cli_parse_can_be_explicitly_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXECUTE_BULK", raising=False)
    monkeypatch.setenv("HERCULES_DISABLE_CLI_PARSE", "true")

    module = _load_config_module(["testzeus-hercules", "--bulk"])

    assert module.get_global_conf().get_config()["EXECUTE_BULK"] == "false"


def test_main_py_argv0_parses_cli_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXECUTE_BULK", raising=False)

    # Parsing happens while the module executes under the test argv; --bulk
    # landing in the config proves _parse_arguments ran for this argv0.
    module = _load_config_module(["__main__.py", "--bulk"])

    assert module.get_global_conf().get_config()["EXECUTE_BULK"] == "true"


def test_console_script_argv0_parses_cli_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXECUTE_BULK", raising=False)

    module = _load_config_module(["testzeus-hercules", "--bulk"])

    assert module.get_global_conf().get_config()["EXECUTE_BULK"] == "true"


def test_should_parse_cli_args_decision_by_basename(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERCULES_DISABLE_CLI_PARSE", raising=False)
    module = _load_config_module(["testzeus-hercules"])

    monkeypatch.setattr(sys, "argv", ["/opt/venv/bin/hercules-dashboard", "--port", "8000"])
    assert module._should_parse_cli_args() is False

    monkeypatch.setattr(sys, "argv", ["/opt/venv/bin/testzeus-hercules", "--bulk"])
    assert module._should_parse_cli_args() is True

    monkeypatch.setattr(sys, "argv", ["__MAIN__.PY"])
    assert module._should_parse_cli_args() is True

    monkeypatch.setattr(sys, "argv", ["/opt/venv/bin/something-else"])
    assert module._should_parse_cli_args() is True


# --- Env vs Dict priority (BaseConfigManager.__init__) ------------------------
# Legacy default: the environment wins over an explicitly passed config dict.
# CONFIG_DICT_OVERRIDES_ENV=true opts into Dict > Env.


def test_env_beats_config_dict_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONFIG_DICT_OVERRIDES_ENV", raising=False)
    monkeypatch.setenv("INPUT_GHERKIN_FILE_PATH", "/from/env.feature")
    module = _load_config_module(["testzeus-hercules"])

    cfg = module.NonSingletonConfigManager({"INPUT_GHERKIN_FILE_PATH": "/from/dict.feature"}).get_config()

    assert cfg["INPUT_GHERKIN_FILE_PATH"] == "/from/env.feature"


def test_config_dict_beats_env_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIG_DICT_OVERRIDES_ENV", "true")
    monkeypatch.setenv("INPUT_GHERKIN_FILE_PATH", "/from/env.feature")
    module = _load_config_module(["testzeus-hercules"])

    cfg = module.NonSingletonConfigManager({"INPUT_GHERKIN_FILE_PATH": "/from/dict.feature"}).get_config()

    assert cfg["INPUT_GHERKIN_FILE_PATH"] == "/from/dict.feature"


def test_env_value_used_when_dict_omits_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INPUT_GHERKIN_FILE_PATH", "/from/env.feature")
    module = _load_config_module(["testzeus-hercules"])

    cfg = module.NonSingletonConfigManager({}).get_config()

    assert cfg["INPUT_GHERKIN_FILE_PATH"] == "/from/env.feature"


def test_default_used_when_env_and_dict_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INPUT_GHERKIN_FILE_PATH", raising=False)
    module = _load_config_module(["testzeus-hercules"])

    cfg = module.NonSingletonConfigManager({}).get_config()

    assert cfg["INPUT_GHERKIN_FILE_PATH"] == os.path.join("./opt", "input", "test.feature")


def test_set_global_conf_override_dict_beats_env_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIG_DICT_OVERRIDES_ENV", "true")
    monkeypatch.setenv("INPUT_GHERKIN_FILE_PATH", "/from/env.feature")
    module = _load_config_module(["testzeus-hercules"])

    module.set_global_conf({"INPUT_GHERKIN_FILE_PATH": "/from/dict.feature"}, override=True)

    assert module.get_global_conf().get_config()["INPUT_GHERKIN_FILE_PATH"] == "/from/dict.feature"


def test_cli_still_beats_both_env_and_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    # argparse mutates the config after init, so the CLI must win over both
    # the environment and an explicitly passed config dict.
    monkeypatch.setenv("INPUT_GHERKIN_FILE_PATH", "/from/env.feature")
    module = _load_config_module(["testzeus-hercules"])

    monkeypatch.setattr(sys, "argv", ["testzeus-hercules", "--input-file", "/from/cli.feature"])
    cfg = module.NonSingletonConfigManager({"INPUT_GHERKIN_FILE_PATH": "/from/dict.feature"}).get_config()

    assert cfg["INPUT_GHERKIN_FILE_PATH"] == "/from/cli.feature"


# --- Concurrent version bumps must not lose increments (config.py) -----------
# bump_config_version() is a read-modify-write on a module global; without the
# lock, two concurrent bumps can both read the same value and write back one
# increment (lost update), and readers can observe the dict update before the
# version bump.


def test_bump_config_version_concurrent_bumps_never_lose_an_increment() -> None:
    module = _load_config_module(["testzeus-hercules"])
    start = module.get_config_version()

    threads = 8
    bumps_per_thread = 50
    barrier = threading.Barrier(threads)

    def _bump() -> None:
        barrier.wait()
        for _ in range(bumps_per_thread):
            module.bump_config_version()

    workers = [threading.Thread(target=_bump) for _ in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert module.get_config_version() == start + threads * bumps_per_thread


def test_get_config_version_returns_current_value_after_bump() -> None:
    module = _load_config_module(["testzeus-hercules"])
    before = module.get_config_version()

    bumped = module.bump_config_version()

    assert bumped == before + 1
    assert module.get_config_version() == bumped


# --- per-run explicit overrides (guided / bulk) must beat the environment -----


def test_apply_explicit_overrides_scopes_dict_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import testzeus_hercules.__main__ as main_module

    monkeypatch.delenv("CONFIG_DICT_OVERRIDES_ENV", raising=False)
    seen: dict[str, Any] = {}

    def fake_set_global_conf(keys: dict, override: bool = False, **_kw: Any) -> None:
        seen["keys"] = keys
        seen["override"] = override
        seen["flag_during_call"] = os.environ.get("CONFIG_DICT_OVERRIDES_ENV")

    monkeypatch.setattr(main_module, "set_global_conf", fake_set_global_conf)

    main_module._apply_explicit_overrides({"INPUT_GHERKIN_FILE_PATH": "/x.feature"})

    assert seen == {"keys": {"INPUT_GHERKIN_FILE_PATH": "/x.feature"}, "override": True, "flag_during_call": "true"}
    # The global opt-in is restored afterwards: legacy precedence stays the default.
    assert "CONFIG_DICT_OVERRIDES_ENV" not in os.environ
