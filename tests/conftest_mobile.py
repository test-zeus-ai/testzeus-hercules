"""Shared offline doubles for the mobile device automation test suite.

No test here touches a real device, opens a socket, or spawns a process:
the manager is exercised through its ``_driver_factory`` injection seam, and
gesture tests drive the REAL selenium W3C action classes (installed with the
``mobile`` extra) against a driver double that records the actions payload, so
a removed or renamed client API fails here instead of at runtime. The Appium
package itself is only stubbed in ``sys.modules`` for gating tests that need
the client "present" or "absent" regardless of the dev environment.

Modules of the mobile package are imported ONCE at conftest import time and
re-exported; tests must use these references (not fresh imports) so that
module purges performed by the lazy-import / registry-gating tests cannot
cause class-identity mismatches.
"""

import base64
import importlib.machinery
import importlib.util
import sys
import types

import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.mobile import appium_manager as appium_manager_module
from testzeus_hercules.core.mobile import capabilities as mobile_capabilities
from testzeus_hercules.core.mobile import keymaps as mobile_keymaps
from testzeus_hercules.core.mobile import settings as mobile_settings
from testzeus_hercules.core.mobile import tools as mobile_tools

AppiumManager = appium_manager_module.AppiumManager

# Gesture tests exercise real selenium W3C actions; skip (not fail) when the
# ``mobile`` extra is not installed in this environment.
requires_selenium = pytest.mark.skipif(importlib.util.find_spec("selenium") is None, reason="selenium (mobile extra) not installed")

_MOBILE_MODULE_NAMES = (
    "testzeus_hercules.core.mobile.tools",
    "testzeus_hercules.core.mobile.prompts",
    "testzeus_hercules.core.mobile.appium_manager",
    "testzeus_hercules.core.mobile.capabilities",
    "testzeus_hercules.core.mobile.keymaps",
    "testzeus_hercules.core.mobile.settings",
    "testzeus_hercules.core.mobile",
)


class FakeElement:
    """Minimal WebElement double: click / send_keys / clear + geometry."""

    def __init__(self, id: str = "el1") -> None:
        self.id = id
        self.clicked = 0
        self.sent_keys: list = []
        self.cleared = 0
        self.location = {"x": 10, "y": 20}
        self.size = {"width": 100, "height": 40}

    def click(self) -> None:
        self.clicked += 1

    def send_keys(self, text: str) -> None:
        self.sent_keys.append(text)

    def clear(self) -> None:
        self.cleared += 1


class FakeDriver:
    """Minimal Appium driver double recording every interaction."""

    PAGE_SOURCE = (
        '<hierarchy rotation="0">'
        '<node class="android.widget.Button" content-desc="OK" bounds="[0,0][100,50]" clickable="true" resource-id="com.example:id/ok" />'
        '<node class="android.widget.EditText" text="" bounds="[0,60][200,100]" resource-id="com.example:id/name" index="1" />'
        '<node class="android.widget.FrameLayout" bounds="[0,0][1080,1920]" />'
        "</hierarchy>"
    )

    def __init__(self, session_id: str = "sess-1") -> None:
        self.session_id = session_id
        self.keycodes: list = []  # press_keycode codes, in call order
        self.metastates: list = []  # metastate passed with each press_keycode (None when absent)
        self.swipes: list = []
        self.screenshots: list = []
        self.w3c_actions: list = []  # W3C "actions" payloads sent through driver.execute
        self.scripts: list = []  # (script, args) passed to execute_script
        self.finds: list = []
        self.quit_called = 0
        self.record_started = False
        self.record_stop_count = 0
        self.stop_error: Exception | None = None
        self.element = FakeElement()
        self.page_source = self.PAGE_SOURCE
        self._recording_b64 = base64.b64encode(b"fake-mp4-bytes").decode()

    # keys (the real client has press_keycode(keycode, metastate=None, flags=None) and NO key_down/key_up)
    def press_keycode(self, keycode: int, metastate=None, flags=None) -> None:
        self.keycodes.append(keycode)
        self.metastates.append(metastate)

    # raw WebDriver command + script execution
    def execute(self, command, params=None):
        self.w3c_actions.append((command, params))
        return {"value": None}

    def execute_script(self, script: str, *args):
        self.scripts.append((script, args))
        return None

    # recording
    def start_recording_screen(self) -> None:
        self.record_started = True

    def stop_recording_screen(self) -> str:
        self.record_stop_count += 1
        if self.stop_error is not None:
            raise self.stop_error
        return self._recording_b64

    # screen
    def get_screenshot_as_file(self, path: str) -> bool:
        with open(path, "wb") as f:
            f.write(b"png")
        self.screenshots.append(path)
        return True

    def get_window_size(self) -> dict:
        return {"width": 1080, "height": 1920}

    def swipe(self, *args) -> None:
        self.swipes.append(args)

    # elements
    def find_element(self, by: str, value: str) -> FakeElement:
        self.finds.append((by, value))
        return self.element

    # lifecycle
    def quit(self) -> None:
        self.quit_called += 1


class CountingSourceDriver(FakeDriver):
    """FakeDriver that counts ``page_source`` fetches (fresh-tree assertions).

    The needed attributes are declared without going through
    ``FakeDriver.__init__`` so the class-level ``page_source`` property below
    is not shadowed by that init's plain instance attribute.
    """

    def __init__(self, session_id: str = "sess-count") -> None:
        self.session_id = session_id
        self.keycodes: list = []
        self.metastates: list = []
        self.swipes: list = []
        self.screenshots: list = []
        self.w3c_actions: list = []
        self.scripts: list = []
        self.finds: list = []
        self.quit_called = 0
        self.record_started = False
        self.record_stop_count = 0
        self.stop_error: Exception | None = None
        self.element = FakeElement()
        self._recording_b64 = base64.b64encode(b"fake-mp4-bytes").decode()
        self.page_source_fetches = 0

    @property
    def page_source(self) -> str:
        self.page_source_fetches += 1
        return FakeDriver.PAGE_SOURCE


class StubAppiumOptions:
    def __init__(self) -> None:
        self.capabilities: dict = {}

    def set_capability(self, name: str, value) -> None:
        self.capabilities[name] = value


def install_stub_appium(monkeypatch, remote=None) -> dict:
    """Inject a minimal ``appium`` package into sys.modules.

    The stub exposes exactly the surface the mobile package lazy-imports from
    appium: ``webdriver.Remote`` and ``options.common.base.AppiumOptions``
    (gestures and keys use selenium / driver methods, not appium submodules).
    Each module gets a real
    ``ModuleSpec`` so ``importlib.util.find_spec("appium")`` succeeds.
    """
    names = (
        "appium",
        "appium.webdriver",
        "appium.options",
        "appium.options.common",
        "appium.options.common.base",
    )
    modules = {}
    for name in names:
        module = types.ModuleType(name)
        module.__spec__ = importlib.machinery.ModuleSpec(name, None)
        modules[name] = module

    appium_mod = modules["appium"]
    webdriver_mod = modules["appium.webdriver"]
    options_mod = modules["appium.options"]
    options_common_mod = modules["appium.options.common"]
    options_base_mod = modules["appium.options.common.base"]

    def _remote_not_allowed(*args, **kwargs):
        raise AssertionError("webdriver.Remote must not be called in offline tests; inject _driver_factory instead")

    webdriver_mod.Remote = staticmethod(remote if remote is not None else _remote_not_allowed)
    appium_mod.webdriver = webdriver_mod
    appium_mod.options = options_mod
    options_mod.common = options_common_mod
    options_common_mod.base = options_base_mod
    options_base_mod.AppiumOptions = StubAppiumOptions

    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return modules


def install_stub_selenium(monkeypatch) -> list:
    """Inject a minimal ``selenium.webdriver.common.action_chains`` package.

    The iOS key-press paths lazy-import selenium's ``ActionChains``; the stub
    records every ``send_keys`` payload in the returned list (in call order).
    """
    sent: list = []

    class StubActionChains:
        def __init__(self, driver) -> None:
            self.driver = driver

        def send_keys(self, *keys) -> "StubActionChains":
            sent.append(keys)
            return self

        def perform(self) -> None:
            return None

    names = ("selenium", "selenium.webdriver", "selenium.webdriver.common", "selenium.webdriver.common.action_chains")
    selenium_mod = types.ModuleType("selenium")
    webdriver_mod = types.ModuleType("selenium.webdriver")
    common_mod = types.ModuleType("selenium.webdriver.common")
    action_chains_mod = types.ModuleType("selenium.webdriver.common.action_chains")
    action_chains_mod.ActionChains = StubActionChains
    selenium_mod.webdriver = webdriver_mod
    webdriver_mod.common = common_mod
    common_mod.action_chains = action_chains_mod

    for name in names:
        module = {"selenium": selenium_mod, "selenium.webdriver": webdriver_mod, "selenium.webdriver.common": common_mod, "selenium.webdriver.common.action_chains": action_chains_mod}[name]
        module.__spec__ = importlib.machinery.ModuleSpec(name, None)
        monkeypatch.setitem(sys.modules, name, module)
    return sent


def touch_sequences(driver: "FakeDriver") -> list:
    """Return, per W3C actions payload sent, the touch pointer's ``(type, ...)`` steps.

    Each step is ``(type, x, y)`` for ``pointerMove``, ``("pause", duration_ms)``
    for pauses and ``(type,)`` otherwise, so tests can assert the exact gesture.
    """
    sequences = []
    for _command, params in driver.w3c_actions:
        for source in params["actions"]:
            if source["type"] != "pointer":
                continue
            assert source["parameters"] == {"pointerType": "touch"}
            steps = []
            for action in source["actions"]:
                if action["type"] == "pointerMove":
                    steps.append(("pointerMove", action["x"], action["y"]))
                elif action["type"] == "pause":
                    steps.append(("pause", action["duration"]))
                else:
                    steps.append((action["type"],))
            sequences.append(steps)
    return sequences


def hide_appium(monkeypatch) -> None:
    """Make ``importlib.util.find_spec`` report the Appium client as absent.

    The dev env installs the real client (``mobile`` extra), so tests that need
    "client missing" must say so explicitly instead of relying on its absence.
    """
    real_find_spec = importlib.util.find_spec

    def fake(name, *args, **kwargs):
        if name == "appium" or name.startswith("appium."):
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake)


def purge_mobile_modules() -> None:
    """Drop the mobile package (and submodules) from sys.modules."""
    for name in _MOBILE_MODULE_NAMES:
        sys.modules.pop(name, None)


def import_mobile_package():
    """Re-import the mobile package from scratch so the gating re-evaluates."""
    purge_mobile_modules()
    import importlib

    return importlib.import_module("testzeus_hercules.core.mobile")


@pytest.fixture
def mobile_env(monkeypatch):
    """Enable mobile automation with a stubbed Appium client."""
    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    install_stub_appium(monkeypatch)
    return monkeypatch


@pytest.fixture
def clean_manager_state():
    """Reset AppiumManager singletons around a test."""
    AppiumManager._instances.clear()
    AppiumManager._default_instance = None
    yield AppiumManager
    AppiumManager._instances.clear()
    AppiumManager._default_instance = None


@pytest.fixture
def fake_manager(mobile_env, tmp_path, clean_manager_state, monkeypatch):
    """An AppiumManager with an injected FakeDriver session and tmp artifact dirs.

    Mobile tools bind their manager to the current test id (proof artifacts
    are keyed by it), so the test id is pinned to the fixture's stake "t1"
    and restored afterwards.
    """
    monkeypatch.setattr(get_global_conf(), "_default_test_id", "t1")
    driver = FakeDriver()
    manager = AppiumManager(stake_id="t1", _driver_factory=lambda: driver)
    videos_dir = tmp_path / "videos"
    screenshots_dir = tmp_path / "screenshots"
    videos_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    manager._video_dir = str(videos_dir)
    manager._screenshots_dir = str(screenshots_dir)
    # Deterministic gates (config defaults could be overridden by the environment).
    manager._record_video = True
    manager._take_screenshots = True
    manager.driver = driver
    yield manager, driver
    manager.shutdown_pool()


@pytest.fixture
def registry_guard():
    """Snapshot the global tool registry and restore it after the test.

    Note: importing the mobile tools module (here or via the gated package
    import) registers its @tool entries globally. Those entries are inert for
    all non-mobile code paths (nothing else queries 'mobile_nav_agent'), and
    gating tests call :func:`drop_mobile_registrations` first so their
    assertions measure only what the gate itself did.
    """
    from testzeus_hercules.core.tools.tool_registry import tool_registry

    snapshot = {agent: list(entries) for agent, entries in tool_registry.items()}
    yield tool_registry
    tool_registry.clear()
    for agent, entries in snapshot.items():
        tool_registry[agent] = list(entries)


def drop_mobile_registrations(tool_registry) -> None:
    """Remove entries registered under 'mobile_nav_agent' (setup helper for gating tests)."""
    tool_registry.pop("mobile_nav_agent", None)
