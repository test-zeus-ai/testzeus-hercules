"""Contract tests against the REAL Appium / selenium clients (``mobile`` extra).

The fake-driver tests prove our logic; these prove the client APIs we call
still exist. They exist because ``appium.webdriver.common.touch_action`` was
removed in Appium-Python-Client 4.0 while the stubbed tests kept passing.
Skipped (not failed) only when the ``mobile`` extra is not installed.
"""

import ast
import importlib
import inspect
import pathlib

import pytest

pytest.importorskip("appium")
pytest.importorskip("selenium")

import testzeus_hercules.core.mobile as mobile_package  # noqa: E402
from testzeus_hercules.core.mobile import capabilities as capabilities_module  # noqa: E402

MOBILE_SOURCE_FILES = sorted(pathlib.Path(mobile_package.__file__).parent.glob("*.py")) + [pathlib.Path(mobile_package.__file__).parents[1] / "agents" / "mobile_nav_agent.py"]


def _client_imports(path: pathlib.Path) -> list:
    """Every ``import``/``from ... import`` of appium/selenium in a file, at any nesting depth."""
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in {"appium", "selenium"}:
            found.extend((node.module, alias.name) for alias in node.names)
        elif isinstance(node, ast.Import):
            found.extend((alias.name, None) for alias in node.names if alias.name.split(".")[0] in {"appium", "selenium"})
    return found


def test_mobile_package_actually_imports_client_modules() -> None:
    # Guards the guard: if the scan found nothing, the resolve test below would pass vacuously.
    all_imports = [item for path in MOBILE_SOURCE_FILES for item in _client_imports(path)]
    assert ("appium.options.common.base", "AppiumOptions") in all_imports
    assert any(module.startswith("selenium.webdriver.common.actions") for module, _ in all_imports)


@pytest.mark.parametrize("path", MOBILE_SOURCE_FILES, ids=lambda p: p.name)
def test_every_client_import_in_mobile_code_resolves_with_installed_client(path) -> None:
    for module_name, attribute in _client_imports(path):
        module = importlib.import_module(module_name)  # ImportError here == removed module (e.g. touch_action)
        if attribute is not None and not hasattr(module, attribute):
            importlib.import_module(f"{module_name}.{attribute}")  # ``from pkg import submodule``


def test_remote_driver_exposes_every_method_the_manager_calls() -> None:
    from appium import webdriver

    for name in (
        "find_element",
        "execute",
        "execute_script",
        "press_keycode",
        "swipe",
        "start_recording_screen",
        "stop_recording_screen",
        "get_screenshot_as_file",
        "get_window_size",
        "quit",
    ):
        assert callable(getattr(webdriver.Remote, name, None)), f"appium Remote.{name} is missing"
    assert isinstance(inspect.getattr_static(webdriver.Remote, "page_source"), property)


def test_modifier_combinations_use_press_keycode_metastate_not_key_down() -> None:
    from appium import webdriver

    assert "metastate" in inspect.signature(webdriver.Remote.press_keycode).parameters
    # The classic key_down/key_up helpers are not part of the client: do not call them.
    assert not hasattr(webdriver.Remote, "key_down")
    assert not hasattr(webdriver.Remote, "key_up")


@pytest.mark.parametrize("platform", ["android", "ios"])
def test_built_capabilities_load_into_real_appium_options(platform) -> None:
    from appium.options.common.base import AppiumOptions

    caps = capabilities_module.build_capabilities(platform, device_name="d", app="/tmp/app", platform_version="17", udid="u")
    options = AppiumOptions()
    for name, value in caps.items():
        options.set_capability(name, value if isinstance(value, (bool, int, float)) else str(value))
    assert options.to_capabilities()["platformName"] == caps["platformName"]
