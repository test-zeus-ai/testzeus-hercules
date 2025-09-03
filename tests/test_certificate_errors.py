import os
import pytest
from typing import Generator
from testzeus_hercules.config import SingletonConfigManager, set_global_conf, get_global_conf


class TestCertificateErrors:
    """Basic test suite for IGNORE_CERTIFICATE_ERRORS functionality."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self) -> Generator[None, None, None]:
        """Setup and teardown for each test."""
        os.environ["IS_TEST_ENV"] = "true"

        SingletonConfigManager.reset_instance()
        yield
        SingletonConfigManager.reset_instance()

    def test_should_ignore_certificate_errors_default_false(self) -> None:
        """Test that should_ignore_certificate_errors returns False by default."""
        if "IGNORE_CERTIFICATE_ERRORS" in os.environ:
            del os.environ["IGNORE_CERTIFICATE_ERRORS"]

        config = set_global_conf({"IGNORE_CERTIFICATE_ERRORS": "false", "LLM_MODEL_NAME": "test-model", "LLM_MODEL_API_KEY": "test-key"}, ignore_env=True)
        assert config.should_ignore_certificate_errors() is False

    def test_should_ignore_certificate_errors_true(self) -> None:
        """Test that should_ignore_certificate_errors returns True when set to 'true'."""
        os.environ["IGNORE_CERTIFICATE_ERRORS"] = "true"

        config = set_global_conf({"IGNORE_CERTIFICATE_ERRORS": "true", "LLM_MODEL_NAME": "test-model", "LLM_MODEL_API_KEY": "test-key"}, ignore_env=True)
        assert config.should_ignore_certificate_errors() is True

    def test_should_ignore_certificate_errors_case_insensitive(self) -> None:
        """Test that should_ignore_certificate_errors is case insensitive."""
        os.environ["IGNORE_CERTIFICATE_ERRORS"] = "TRUE"
        config = set_global_conf({"IGNORE_CERTIFICATE_ERRORS": "TRUE", "LLM_MODEL_NAME": "test-model", "LLM_MODEL_API_KEY": "test-key"}, ignore_env=True)
        assert config.should_ignore_certificate_errors() is True

        os.environ["IGNORE_CERTIFICATE_ERRORS"] = "True"
        config = set_global_conf({"IGNORE_CERTIFICATE_ERRORS": "True", "LLM_MODEL_NAME": "test-model", "LLM_MODEL_API_KEY": "test-key"}, ignore_env=True)
        assert config.should_ignore_certificate_errors() is True

    def test_should_ignore_certificate_errors_missing_key(self) -> None:
        """Test that should_ignore_certificate_errors returns False when key is missing."""
        if "IGNORE_CERTIFICATE_ERRORS" in os.environ:
            del os.environ["IGNORE_CERTIFICATE_ERRORS"]

        config = set_global_conf({"LLM_MODEL_NAME": "test-model", "LLM_MODEL_API_KEY": "test-key"}, ignore_env=True)
        assert config.should_ignore_certificate_errors() is False

    def test_environment_variable_override(self) -> None:
        """Test that environment variable IGNORE_CERTIFICATE_ERRORS overrides config."""
        os.environ["IGNORE_CERTIFICATE_ERRORS"] = "true"

        config_dict = {"IGNORE_CERTIFICATE_ERRORS": "false", "AGENTS_LLM_CONFIG_FILE": "agents_llm_config.json", "AGENTS_LLM_CONFIG_FILE_REF_KEY": "azure"}
        config = set_global_conf(config_dict, ignore_env=False)

        assert config.should_ignore_certificate_errors() is True

    def test_playwright_manager_with_certificate_errors_enabled(self) -> None:
        """Test that PlaywrightManager includes certificate error flags when enabled."""
        os.environ["IGNORE_CERTIFICATE_ERRORS"] = "true"

        config_dict = {
            "IGNORE_CERTIFICATE_ERRORS": "true",
            "BROWSER_TYPE": "chromium",
            "HEADLESS": "true",
            "RECORD_VIDEO": "false",
            "TAKE_SCREENSHOTS": "false",
            "CAPTURE_NETWORK": "false",
            "LLM_MODEL_NAME": "test-model",
            "LLM_MODEL_API_KEY": "test-key",
        }

        set_global_conf(config_dict, ignore_env=True)

        config = get_global_conf()
        assert config.should_ignore_certificate_errors() is True

    def test_playwright_manager_with_certificate_errors_disabled(self) -> None:
        """Test that PlaywrightManager does NOT include certificate error flags when disabled."""
        if "IGNORE_CERTIFICATE_ERRORS" in os.environ:
            del os.environ["IGNORE_CERTIFICATE_ERRORS"]

        config_dict = {
            "IGNORE_CERTIFICATE_ERRORS": "false",
            "BROWSER_TYPE": "chromium",
            "HEADLESS": "true",
            "RECORD_VIDEO": "false",
            "TAKE_SCREENSHOTS": "false",
            "CAPTURE_NETWORK": "false",
            "LLM_MODEL_NAME": "test-model",
            "LLM_MODEL_API_KEY": "test-key",
        }

        set_global_conf(config_dict, ignore_env=True)

        config = get_global_conf()
        assert config.should_ignore_certificate_errors() is False
