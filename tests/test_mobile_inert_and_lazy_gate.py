"""Mobile is inert by default, and its gate/settings are evaluated lazily through the global config."""

import sys

import pytest
from tests import conftest_mobile as cm
from tests.conftest_mobile import clean_manager_state, registry_guard  # noqa: F401
from testzeus_hercules.config import get_global_conf

pytest_plugins = ["tests.conftest_mobile"]

MOBILE_KEYS = ("DEVICE_MANAGER", "DEVICE_OS", "APPIUM_SERVER_URL", "APPIUM_SERVER_PORT", "DEVICE_NAME")


@pytest.fixture
def clean_mobile_config(monkeypatch):
    """No mobile keys in env or in the global config dict (restored afterwards)."""
    for key in MOBILE_KEYS:
        monkeypatch.delenv(key, raising=False)
    config = get_global_conf().get_config()
    for key in MOBILE_KEYS:
        if key in config:
            monkeypatch.delitem(config, key)
    return config


# ── inert when DEVICE_MANAGER is unset (even with the real client installed) ─


def test_whole_mobile_path_is_inert_when_device_manager_unset(monkeypatch, registry_guard, clean_manager_state, clean_mobile_config) -> None:
    from testzeus_hercules.core.agents.high_level_planner_agent import (
        PlannerAgent,
        get_planner_prompt,
    )
    from testzeus_hercules.core.runner import BaseRunner
    from testzeus_hercules.core.simple_hercules import SimpleHercules

    cm.purge_mobile_modules()
    cm.drop_mobile_registrations(registry_guard)
    try:
        mobile = cm.import_mobile_package()

        # gate closed, nothing imported, nothing registered
        assert mobile.mobile_enabled() is False
        assert "testzeus_hercules.core.mobile.tools" not in sys.modules
        assert list(registry_guard.get("mobile_nav_agent", [])) == []

        # planner prompt byte-identical, no mobile helper routing
        assert get_planner_prompt() == PlannerAgent.prompt
        assert SimpleHercules._mobile_gate_open() is False
        with pytest.raises(ValueError, match="mobile"):
            SimpleHercules._validate_planner_decision({"terminate": "no", "next_step": "tap it", "target_helper": "mobile"})

        # no mobile agent constructed
        hercules = SimpleHercules(stake_id="inert", browser_nav_max_chat_round=5)
        assert hercules._create_mobile_nav_agent({"model": "gpt-4o", "api_key": "k"}, {}, None) is None

        # runner teardown neither fails nor creates a device manager
        BaseRunner._teardown_appium_instances()
        assert not cm.AppiumManager._instances
    finally:
        cm.purge_mobile_modules()


# ── settings resolve lazily through the global config, env as fallback ──────


def test_settings_read_global_config_keys(monkeypatch, clean_mobile_config) -> None:
    monkeypatch.setitem(clean_mobile_config, "DEVICE_MANAGER", "Appium")
    monkeypatch.setitem(clean_mobile_config, "DEVICE_OS", "iOS")
    monkeypatch.setitem(clean_mobile_config, "APPIUM_SERVER_PORT", "4999")
    assert cm.mobile_settings.get_device_manager() == "appium"
    assert cm.mobile_settings.get_device_os() == "ios"
    assert cm.mobile_settings.get_appium_server_url() == "http://127.0.0.1:4999"
    assert cm.mobile_settings.get_device_name() == "iPhone Simulator"


def test_settings_fall_back_to_live_environment(monkeypatch, clean_mobile_config) -> None:
    assert cm.mobile_settings.get_device_manager() == "playwright"
    monkeypatch.setenv("DEVICE_MANAGER", "appium")  # set AFTER import: no import-time snapshot
    assert cm.mobile_settings.get_device_manager() == "appium"
    monkeypatch.setenv("DEVICE_NAME", "Pixel_8")
    assert cm.mobile_settings.get_device_name() == "Pixel_8"


def test_config_value_wins_over_environment(monkeypatch, clean_mobile_config) -> None:
    monkeypatch.setenv("DEVICE_OS", "android")
    monkeypatch.setitem(clean_mobile_config, "DEVICE_OS", "ios")
    assert cm.mobile_settings.get_device_os() == "ios"


def test_settings_survive_a_broken_global_config(monkeypatch, clean_mobile_config) -> None:
    def boom():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(cm.mobile_settings, "get_global_conf", boom)
    monkeypatch.setenv("DEVICE_OS", "ios")
    assert cm.mobile_settings.get_device_os() == "ios"


# ── the gate is evaluated at use, not frozen at import ──────────────────────


def test_gate_enabled_after_import_registers_tools_lazily(monkeypatch, registry_guard, clean_mobile_config) -> None:
    cm.install_stub_appium(monkeypatch)
    cm.drop_mobile_registrations(registry_guard)
    mobile = cm.import_mobile_package()  # DEVICE_MANAGER unset at import time
    try:
        assert mobile.mobile_enabled() is False
        assert list(registry_guard.get("mobile_nav_agent", [])) == []

        monkeypatch.setenv("DEVICE_MANAGER", "appium")  # e.g. .env / config applied after the import
        assert mobile.mobile_enabled() is True
        assert sorted(entry["name"] for entry in registry_guard["mobile_nav_agent"]) == sorted(cm.mobile_tools.MOBILE_TOOLS)

        monkeypatch.delenv("DEVICE_MANAGER")
        assert mobile.mobile_enabled() is False  # and it can close again
    finally:
        cm.purge_mobile_modules()


def test_mobile_agent_registers_tools_even_if_package_was_imported_closed(monkeypatch, registry_guard, clean_mobile_config) -> None:
    cm.install_stub_appium(monkeypatch)
    cm.drop_mobile_registrations(registry_guard)
    cm.import_mobile_package()
    try:
        assert list(registry_guard.get("mobile_nav_agent", [])) == []

        from testzeus_hercules.core.agents import mobile_nav_agent

        monkeypatch.setattr(mobile_nav_agent.MultimodalBaseNavAgent, "load_tools", lambda self, *a, **k: None)
        agent = mobile_nav_agent.MobileNavAgent.__new__(mobile_nav_agent.MobileNavAgent)
        agent.register_tools()
        assert len(registry_guard["mobile_nav_agent"]) == len(cm.mobile_tools.MOBILE_TOOLS)
    finally:
        cm.purge_mobile_modules()
