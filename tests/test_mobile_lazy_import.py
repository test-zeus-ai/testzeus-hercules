"""The critical regression guard: mobile automation must be inert without the Appium client.

With ``importlib.util.find_spec("appium")`` faked to return None:

1. importing ``testzeus_hercules.core.mobile`` succeeds,
2. ``mobile_enabled()`` is False and no tools are registered,
3. calling a mobile tool returns a graceful install-hint string (never raises),
4. the core package and ``core.tools`` still import normally.
"""

import asyncio
import importlib
import importlib.util
import sys

from tests import conftest_mobile as cm
from tests.conftest_mobile import registry_guard  # noqa: F401

pytest_plugins = ["tests.conftest_mobile"]


def _fake_find_spec_missing_appium(real_find_spec):
    def fake(name, *args, **kwargs):
        if name == "appium" or name.startswith("appium."):
            return None
        return real_find_spec(name, *args, **kwargs)

    return fake


def test_mobile_package_is_inert_without_appium_client(monkeypatch, registry_guard) -> None:
    monkeypatch.setenv("DEVICE_MANAGER", "appium")  # env enables; missing client must disable
    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec_missing_appium(importlib.util.find_spec))

    cm.purge_mobile_modules()
    cm.drop_mobile_registrations(registry_guard)
    try:
        mobile = importlib.import_module("testzeus_hercules.core.mobile")

        # (a) import succeeded; (b) gating is closed and nothing registered
        assert mobile.mobile_enabled() is False
        assert "testzeus_hercules.core.mobile.tools" not in sys.modules
        assert list(registry_guard.get("mobile_nav_agent", [])) == []

        # (c) tool call degrades to the graceful install-hint string
        tools = importlib.import_module("testzeus_hercules.core.mobile.tools")
        result = asyncio.run(tools.tap(10, 10))
        assert result.startswith("Error:")
        assert "Appium client" in result
        assert "pip install 'testzeus-hercules[mobile]'" in result

        for entry in registry_guard.get("mobile_nav_agent", []):
            assert entry["func"] is not None  # registration happened only via this explicit import
    finally:
        cm.purge_mobile_modules()

    # (d) core package + tool package imports still succeed after the purge cycle
    import testzeus_hercules  # noqa: F401
    import testzeus_hercules.core.tools  # noqa: F401


def test_core_tools_import_unaffected_without_appium(monkeypatch, registry_guard) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec_missing_appium(importlib.util.find_spec))

    cm.purge_mobile_modules()
    cm.drop_mobile_registrations(registry_guard)
    try:
        import testzeus_hercules  # noqa: F401
        import testzeus_hercules.core.mobile  # must succeed without the client
        import testzeus_hercules.core.tools  # noqa: F401

        # The gate stayed closed: core.tools' eager pkgutil loader must not
        # have registered any mobile toolset as a side effect.
        assert "testzeus_hercules.core.mobile.tools" not in sys.modules
        assert list(registry_guard.get("mobile_nav_agent", [])) == []
    finally:
        cm.purge_mobile_modules()
