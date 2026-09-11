"""Registry gating: mobile tools register under 'mobile_nav_agent' only when enabled."""

import sys

from tests import conftest_mobile as cm
from tests.conftest_mobile import registry_guard  # noqa: F401

pytest_plugins = ["tests.conftest_mobile"]


def test_no_registration_when_device_manager_unset(monkeypatch, registry_guard) -> None:
    monkeypatch.delenv("DEVICE_MANAGER", raising=False)
    cm.install_stub_appium(monkeypatch)  # client present but feature not selected
    cm.drop_mobile_registrations(registry_guard)

    mobile = cm.import_mobile_package()
    assert mobile.mobile_enabled() is False
    assert list(registry_guard.get("mobile_nav_agent", [])) == []


def test_no_registration_when_appium_client_missing(monkeypatch, registry_guard) -> None:
    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    # no stub installed: find_spec("appium") is None
    cm.drop_mobile_registrations(registry_guard)

    mobile = cm.import_mobile_package()
    assert mobile.mobile_enabled() is False
    assert list(registry_guard.get("mobile_nav_agent", [])) == []


def test_enabled_registers_mobile_tools_under_mobile_nav_agent_only(monkeypatch, registry_guard) -> None:
    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    cm.install_stub_appium(monkeypatch)
    cm.drop_mobile_registrations(registry_guard)

    counts_before = {agent: len(entries) for agent, entries in registry_guard.items()}

    mobile = cm.import_mobile_package()
    assert mobile.mobile_enabled() is True

    registered = sorted(entry["name"] for entry in registry_guard.get("mobile_nav_agent", []))
    assert registered == sorted(cm.mobile_tools.MOBILE_TOOLS)
    assert len(registered) == 20

    # No other agent gained registrations.
    for agent, entries in registry_guard.items():
        if agent != "mobile_nav_agent":
            assert len(entries) == counts_before.get(agent, 0)
    assert set(registry_guard.keys()) - set(counts_before.keys()) <= {"mobile_nav_agent"}


def test_enabled_planner_enum_contains_mobile(monkeypatch, registry_guard) -> None:
    from testzeus_hercules.core.agents.high_level_planner_agent import (
        PlannerAgent,
        get_planner_prompt,
    )

    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    cm.install_stub_appium(monkeypatch)
    cm.import_mobile_package()

    prompt = get_planner_prompt()
    assert prompt != PlannerAgent.prompt
    assert "'mobile'" in prompt
    assert "executor'|'mobile'|'Not_Applicable'" in prompt
    assert "Mobile: Native app navigation, gestures, device keys, state verification" in prompt


def test_disabled_planner_prompt_is_byte_identical(monkeypatch, registry_guard) -> None:
    from testzeus_hercules.core.agents.high_level_planner_agent import (
        PlannerAgent,
        get_planner_prompt,
    )

    monkeypatch.delenv("DEVICE_MANAGER", raising=False)
    cm.import_mobile_package()

    assert get_planner_prompt() == PlannerAgent.prompt
    assert "'mobile'" not in get_planner_prompt()


def test_mobile_nav_agent_module_exposes_exact_names(monkeypatch, registry_guard) -> None:
    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    cm.install_stub_appium(monkeypatch)
    cm.import_mobile_package()

    import testzeus_hercules.core.agents.mobile_nav_agent as mobile_nav_module

    assert mobile_nav_module.MobileNavAgent.agent_name == "mobile_nav_agent"
    assert "##TERMINATE TASK##" in mobile_nav_module.MobileNavAgent.prompt
    assert sys.modules["testzeus_hercules.core.agents.mobile_nav_agent"] is mobile_nav_module


# ── @tool dedup: purge + re-import must not duplicate registrations ─────────


def test_reimport_does_not_duplicate_mobile_registrations(monkeypatch, registry_guard) -> None:
    import importlib

    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    cm.install_stub_appium(monkeypatch)
    cm.drop_mobile_registrations(registry_guard)

    mobile = cm.import_mobile_package()
    assert mobile.mobile_enabled() is True
    count_first = len(registry_guard["mobile_nav_agent"])
    assert count_first == len(cm.mobile_tools.MOBILE_TOOLS)

    # Simulate a module purge + re-import (e.g. test-isolation reloads):
    # the @tool decorator must replace entries, never append duplicates.
    sys.modules.pop("testzeus_hercules.core.mobile.tools", None)
    importlib.import_module("testzeus_hercules.core.mobile.tools")

    names = [entry["name"] for entry in registry_guard["mobile_nav_agent"]]
    assert len(names) == count_first
    assert len(set(names)) == count_first


def test_tool_decorator_same_name_registers_once_with_latest_definition(registry_guard) -> None:
    from testzeus_hercules.core.tools.tool_registry import tool

    @tool(agent_names=["dedup_probe_agent"], description="first", name="probe_tool")
    async def first_probe() -> str:
        return "first"

    @tool(agent_names=["dedup_probe_agent"], description="second", name="probe_tool")
    async def second_probe() -> str:
        return "second"

    entries = list(registry_guard.get("dedup_probe_agent", []))
    assert len(entries) == 1
    assert entries[0]["func"] is second_probe
    assert entries[0]["description"] == "second"


def test_tool_decorator_keeps_distinct_names_and_agents(registry_guard) -> None:
    from testzeus_hercules.core.tools.tool_registry import tool

    @tool(agent_names=["dedup_probe_agent"], description="a", name="probe_a")
    async def probe_a() -> str:
        return "a"

    @tool(agent_names=["dedup_probe_agent"], description="b", name="probe_b")
    async def probe_b() -> str:
        return "b"

    @tool(agent_names=["other_probe_agent"], description="a2", name="probe_a")
    async def probe_a2() -> str:
        return "a2"

    assert [entry["name"] for entry in registry_guard["dedup_probe_agent"]] == ["probe_a", "probe_b"]
    assert [entry["name"] for entry in registry_guard["other_probe_agent"]] == ["probe_a"]
