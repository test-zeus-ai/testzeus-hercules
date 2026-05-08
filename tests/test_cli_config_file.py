import os
from pathlib import Path
from typing import Generator

import pytest

from testzeus_hercules.config import SingletonConfigManager, set_global_conf


class TestCliConfigFile:
    @pytest.fixture(autouse=True)
    def setup_teardown(self) -> Generator[None, None, None]:
        os.environ["IS_TEST_ENV"] = "true"
        SingletonConfigManager.reset_instance()
        yield
        SingletonConfigManager.reset_instance()

    def test_yaml_config_file_is_loaded_via_cli_flag(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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

        monkeypatch.setattr("sys.argv", ["testzeus-hercules", "--config", str(config_file)])

        config = set_global_conf({}, ignore_env=False, override=True)

        assert config.get_config()["INPUT_GHERKIN_FILE_PATH"] == str(tmp_path / "input" / "test.feature")
        assert config.get_config()["JUNIT_XML_BASE_PATH"] == str(tmp_path / "output")
        assert config.get_config()["TEST_DATA_PATH"] == str(tmp_path / "test_data")
        assert config.get_config()["AGENTS_LLM_CONFIG_FILE"] == str(tmp_path / "agents_llm_config.json")
        assert config.get_config()["AGENTS_LLM_CONFIG_FILE_REF_KEY"] == "huggingface"
        assert config.get_config()["BROWSER_TYPE"] == "chromium"
        assert config.get_config()["HEADLESS"] == "false"
        assert config.get_config()["TAKE_SCREENSHOTS"] == "true"

    def test_cli_flags_override_config_file_values(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        config_file = tmp_path / "hercules.config.yaml"
        config_file.write_text(
            f"input_file: {tmp_path / 'from-config.feature'}\nllm_model: from-config\nllm_model_api_key: secret\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "sys.argv",
            [
                "testzeus-hercules",
                "--config",
                str(config_file),
                "--input-file",
                str(tmp_path / "from-cli.feature"),
            ],
        )

        config = set_global_conf({}, ignore_env=False, override=True)

        assert config.get_config()["INPUT_GHERKIN_FILE_PATH"] == str(tmp_path / "from-cli.feature")
        assert config.get_config()["LLM_MODEL_NAME"] == "from-config"
        assert config.get_config()["LLM_MODEL_API_KEY"] == "secret"
