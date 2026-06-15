import os
from typing import Generator

import pytest

from testzeus_hercules.config import SingletonConfigManager, set_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager


@pytest.fixture(autouse=True)
def reset_config_and_browser_manager() -> Generator[None, None, None]:
    os.environ["IS_TEST_ENV"] = "true"
    SingletonConfigManager.reset_instance()
    PlaywrightManager._instances.clear()
    PlaywrightManager._default_instance = None
    yield
    SingletonConfigManager.reset_instance()
    PlaywrightManager._instances.clear()
    PlaywrightManager._default_instance = None


def _set_minimal_config() -> None:
    set_global_conf(
        {
            "BROWSER_TYPE": "chromium",
            "HEADLESS": "true",
            "RECORD_VIDEO": "false",
            "TAKE_SCREENSHOTS": "false",
            "CAPTURE_NETWORK": "false",
            "AGENTS_LLM_CONFIG_FILE": "agents_llm_config.json",
            "AGENTS_LLM_CONFIG_FILE_REF_KEY": "litellm",
        },
        ignore_env=True,
    )


def test_cdp_connection_does_not_navigate_on_connect_by_default() -> None:
    _set_minimal_config()

    manager = PlaywrightManager(
        stake_id="cdp-default",
        cdp_config={"endpoint_url": "ws://example.test/devtools/browser/1"},
    )

    assert manager.cdp_navigate_on_connect is False


def test_cdp_navigation_can_still_be_requested_explicitly() -> None:
    _set_minimal_config()

    manager = PlaywrightManager(
        stake_id="cdp-explicit",
        cdp_config={"endpoint_url": "ws://example.test/devtools/browser/1"},
        cdp_navigate_on_connect=True,
    )

    assert manager.cdp_navigate_on_connect is True
