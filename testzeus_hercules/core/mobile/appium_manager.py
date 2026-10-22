"""Appium session manager for mobile device automation.

Mirrors the :class:`~testzeus_hercules.core.playwright_manager.PlaywrightManager`
shape: one singleton instance per ``stake_id``, a bounded worker pool so the
asyncio event loop is never blocked by synchronous driver calls, and
proof-artifact directories under the run's proof path.

Design notes (deliberate departures from the classic implementation):

- The Appium client is imported **lazily inside methods**, so this module can
  be imported (and fully unit-tested) without the Appium client installed.
- ``_driver_factory`` is a constructor injection seam for tests: when given,
  it is used instead of building ``webdriver.Remote`` and no Appium import is
  attempted.
- Gestures use W3C pointer actions (``selenium.webdriver.common.actions``) and
  ``mobile:`` scripts, never the removed ``TouchAction``/``key_down`` APIs.
- No local Appium server spawn and no emulator boot: ``APPIUM_SERVER_URL``
  (default ``http://127.0.0.1:4723``) must point at a running Appium server.
"""

import asyncio
import base64
import importlib.util
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import cpu_count
from typing import Any, Callable, Dict, List, Optional

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.mobile.keymaps import (
    ANDROID_KEYCODES,
    android_hardware_keycode,
    android_keycode,
    android_metastate,
    expand_key_sequence,
    ios_unicode_key,
)
from testzeus_hercules.core.mobile.settings import (
    get_apk_path,
    get_app_path,
    get_appium_capabilities,
    get_appium_server_url,
    get_automation_name,
    get_device_name,
    get_device_os,
    get_device_uuid,
    get_platform_version,
    warn_if_platform_name_set,
)
from testzeus_hercules.utils.logger import logger

APPIUM_INSTALL_HINT = "Mobile automation requires the Appium client. Install with: pip install 'testzeus-hercules[mobile]'"

# Attribute-name substitution tables: platform-specific accessibility-tree
# attributes are normalized to the common legend keys used by read_screen.
ANDROID_ATTRIBUTE_MAP = {
    "content-desc": "label",
    "text": "value",
    "resource-id": "id",
    "bounds": "bounds",
    "class": "type",
}
# XCUITest page sources carry ``name`` (the accessibility identifier, which
# WDA falls back to the label when no identifier is set), ``label`` (the
# visible text) and the geometry as separate ``x``/``y``/``width``/``height``
# attributes (no ``frame``/``bounds``). Bounds are rebuilt from the geometry
# by :func:`_ios_bounds` in the Android ``[x1,y1][x2,y2]`` format.
IOS_ATTRIBUTE_MAP = {
    "label": "label",
    "value": "value",
    "name": "id",
    "type": "type",
}

ANDROID_INPUT_CLASSES = {
    "android.widget.EditText",
    "android.widget.AutoCompleteTextView",
    "android.widget.MultiAutoCompleteTextView",
}
IOS_INPUT_TYPES = {
    "XCUIElementTypeTextField",
    "XCUIElementTypeSecureTextField",
    "XCUIElementTypeSearchField",
    "XCUIElementTypeTextView",
}

# iOS hardware buttons are routed through the ``mobile: pressButton`` script,
# using the documented XCUITest names (home, volumeUp, volumeDown). iOS has no
# hardware back button: ``press_back`` taps the navigation-bar back button or
# swipes from the left screen edge instead.
IOS_HARDWARE_BUTTONS = {
    "volume_up": "volumeUp",
    "volume_down": "volumeDown",
    "home": "home",
}
# Only a navigation-bar button that looks like a back button is tapped: the
# UIKit back button is exposed as ``BackButton`` / ``Back`` (or a name that
# contains it). The first nav-bar button can just as well be Edit / Cancel /
# Close, which must never be tapped as "back"; those screens fall through to
# the left-edge swipe instead.
IOS_BACK_BUTTON_CLASS_CHAIN = (
    "**/XCUIElementTypeNavigationBar/XCUIElementTypeButton" '[`(name == "Back" OR label == "Back" OR name CONTAINS "Back" OR name CONTAINS "back")' ' AND NOT (label IN {"Edit", "Cancel", "Close"})`]'
)
_IOS_CLASS_CHAIN_LOCATOR = "-ios class chain"

# Accessibility dumps are a few hundred KB at most; refuse absurd payloads.
MAX_PAGE_SOURCE_CHARS = 10_000_000
_XML_DTD_MARKERS = ("<!doctype", "<!entity")

_ACCESSIBILITY_ID_LOCATOR = "accessibility id"
_ID_LOCATOR = "id"
_ANDROID_UIAUTOMATOR_LOCATOR = "-android uiautomator"
_IOS_PREDICATE_LOCATOR = "-ios predicate string"


def _escape_quoted(value: str) -> str:
    """Escape backslashes and double quotes for a double-quoted selector literal.

    Both UiAutomator's ``UiSelector`` string arguments and NSPredicate string
    literals use backslash escaping inside double quotes.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def element_locators(identifier: str, platform: str) -> "list[tuple[str, str]]":
    """Return the ordered ``(strategy, selector)`` lookups tried for an identifier.

    Android: accessibility id (content-desc), resource-id (``By.ID``; the
    UiAutomator2 server accepts both bare and package-qualified ids), then the
    visible text. iOS: accessibility id (``name``), then the visible ``label``.
    """
    escaped = _escape_quoted(identifier)
    if platform == "ios":
        return [
            (_ACCESSIBILITY_ID_LOCATOR, identifier),
            (_IOS_PREDICATE_LOCATOR, f'label == "{escaped}"'),
        ]
    return [
        (_ACCESSIBILITY_ID_LOCATOR, identifier),
        (_ID_LOCATOR, identifier),
        (_ANDROID_UIAUTOMATOR_LOCATOR, f'new UiSelector().text("{escaped}")'),
    ]


class MobileEnvironmentError(RuntimeError):
    """Raised when a mobile session cannot be established."""


class AppiumClientMissingError(MobileEnvironmentError):
    """Raised when the Appium client package is not importable."""


def _appium_client_available() -> bool:
    try:
        return importlib.util.find_spec("appium") is not None
    except Exception:  # pragma: no cover - defensive: broken installs
        return False


def _parse_page_source(page_source: Optional[str]) -> ET.Element:
    """Parse a driver page source with entity expansion ruled out.

    Appium page sources never carry a DTD, so documents declaring one (the
    carrier of entity-expansion payloads) and oversized documents are rejected
    before reaching the XML parser.
    """
    text = page_source or ""
    if len(text) > MAX_PAGE_SOURCE_CHARS:
        raise ET.ParseError(f"page source too large ({len(text)} characters)")
    lowered = text.lower()
    if any(marker in lowered for marker in _XML_DTD_MARKERS):
        raise ET.ParseError("page source declares a DTD/entities, which is not allowed")
    return ET.fromstring(text)


def parse_accessibility_tree(page_source: str, platform: str) -> Dict[str, Any]:
    """Parse a driver ``page_source`` XML dump into a normalized tree dict.

    Platform-specific attribute names are substituted with the common legend
    keys (``type``/``label``/``value``/``id``/``bounds``) and input fields are
    flagged using the per-platform input-class sets.
    """
    attr_map = IOS_ATTRIBUTE_MAP if platform == "ios" else ANDROID_ATTRIBUTE_MAP
    input_classes = IOS_INPUT_TYPES if platform == "ios" else ANDROID_INPUT_CLASSES

    try:
        root = _parse_page_source(page_source)
    except ET.ParseError as e:
        logger.warning("Failed to parse accessibility tree page source: %s", e)
        return {"platform": platform, "node_count": 0, "nodes": [], "error": str(e)}

    nodes: List[Dict[str, Any]] = []
    for element in root.iter():
        attrs = element.attrib
        if platform == "ios":
            raw_type = attrs.get("type", element.tag)
            node_type = raw_type.replace("XCUIElementType", "") if raw_type else element.tag
            is_input = element.tag in input_classes
            clickable = attrs.get("enabled", "true") == "true"
        else:
            raw_class = attrs.get("class", element.tag)
            node_type = raw_class.split(".")[-1]
            is_input = raw_class in input_classes
            clickable = attrs.get("clickable") == "true"

        node: Dict[str, Any] = {"type": node_type, "label": "", "value": "", "id": "", "bounds": ""}
        for attr_name, legend_key in attr_map.items():
            raw_value = attrs.get(attr_name, "")
            if legend_key in node and raw_value and not node[legend_key]:
                node[legend_key] = raw_value
        if platform == "ios":
            node["bounds"] = _ios_bounds(attrs)
        node["input"] = is_input
        node["clickable"] = clickable

        if node["label"] or node["value"] or node["id"] or node["input"] or node["clickable"]:
            nodes.append(node)

    return {"platform": platform, "node_count": len(nodes), "nodes": nodes}


def _ios_bounds(attrs: Dict[str, str]) -> str:
    """Build ``[x1,y1][x2,y2]`` bounds from XCUITest ``x``/``y``/``width``/``height``."""
    try:
        x, y = int(float(attrs["x"])), int(float(attrs["y"]))
        width, height = int(float(attrs["width"])), int(float(attrs["height"]))
    except (KeyError, ValueError):
        return ""
    return f"[{x},{y}][{x + width},{y + height}]"


def render_screen_legend(tree: Dict[str, Any]) -> str:
    """Render a normalized accessibility tree as compact legend text."""
    nodes = tree.get("nodes", [])
    platform = tree.get("platform", "unknown")
    header = f"Device screen ({platform}) - {len(nodes)} visible elements. " "Use on-screen labels / accessibility ids as element identifiers. Input fields are marked [input]."
    if tree.get("error"):
        return f"{header}\nError reading screen: {tree['error']}. Retry read_mobile_screen once, then report the failure."
    lines = [header]
    for index, node in enumerate(nodes):
        description = str(node.get("type", "unknown"))
        if node.get("label"):
            description += f' "{node["label"]}"'
        if node.get("value"):
            description += f' value="{node["value"]}"'
        if node.get("id"):
            description += f" id={node['id']}"
        if node.get("input"):
            description += " [input]"
        if node.get("clickable"):
            description += " [clickable]"
        if node.get("bounds"):
            description += f" @{node['bounds']}"
        lines.append(f"[{index}] {description}")
    return "\n".join(lines)


class AppiumManager:
    """
    Manages Appium sessions for mobile device automation. Supports stake_id-based singleton instances.
    """

    _instances: Dict[str, "AppiumManager"] = {}
    _default_instance: Optional["AppiumManager"] = None

    def __new__(cls, *args, stake_id: Optional[str] = None, **kwargs) -> "AppiumManager":
        # If no stake_id provided and we have a default instance, return it
        if stake_id is None:
            if cls._default_instance is None:
                instance = super().__new__(cls)
                instance.__initialized = False
                cls._default_instance = instance
                cls._instances["0"] = instance
                logger.debug("Created default AppiumManager instance with stake_id '0'")
            return cls._default_instance

        # If stake_id provided, get or create instance for that stake_id
        if stake_id not in cls._instances:
            instance = super().__new__(cls)
            instance.__initialized = False
            cls._instances[stake_id] = instance
            logger.debug(f"Created new AppiumManager instance for stake_id '{stake_id}'")
            # If this is the first instance ever, make it the default and
            # mirror it under the "0" key too, so stake-less lookups and
            # closes stay symmetric with the stake-scoped one.
            if cls._default_instance is None:
                cls._default_instance = instance
                cls._instances["0"] = instance
        return cls._instances[stake_id]

    @classmethod
    def get_instance(cls, stake_id: Optional[str] = None) -> "AppiumManager":
        """Get AppiumManager instance for given stake_id, or default instance if none provided."""
        if stake_id is None:
            if cls._default_instance is None:
                return cls()
            return cls._default_instance
        if stake_id not in cls._instances:
            return cls(stake_id=stake_id)
        return cls._instances[stake_id]

    @classmethod
    def close_instance(cls, stake_id: Optional[str] = None) -> None:
        """Close and remove an AppiumManager instance, ending its device session.

        The teardown is synchronous and best-effort (recording saved, driver
        quit) so shutdown paths that never called the async ``quit_session``
        — e.g. ``BaseRunner.shutdown`` after an aborted scenario — cannot leak
        the device session until ``newCommandTimeout`` expires.

        ``stake_id=None`` targets the default instance wherever it is stored
        (the "0" mirror included), keeping ``get_instance(None)`` and
        ``close_instance(None)`` symmetric: after closing, the next default
        lookup creates a fresh manager instead of binding to a stale one.
        """
        target_id = stake_id if stake_id is not None else "0"
        instance = cls._instances.get(target_id)
        if instance is None:
            return
        # End the driver session before dropping the singleton.
        try:
            instance._teardown_session_sync()
        except Exception as e:
            logger.debug("Error tearing down Appium session: %s", e)
        # The default instance may be mirrored under several keys ("0" plus
        # the stake key that created it); drop it from every one of them.
        for key in [key for key, value in cls._instances.items() if value is instance]:
            cls._instances.pop(key)
        try:
            instance.shutdown_pool()
        except Exception as e:
            logger.debug("Error shutting down Appium worker pool: %s", e)
        if instance is cls._default_instance:
            cls._default_instance = None
            if cls._instances:
                cls._default_instance = next(iter(cls._instances.values()))
                cls._instances.setdefault("0", cls._default_instance)

    @classmethod
    def close_all_instances(cls) -> None:
        """Close all AppiumManager instances."""
        for stake_id in list(cls._instances.keys()):
            cls.close_instance(stake_id)

    def __init__(
        self,
        *args,
        stake_id: Optional[str] = None,
        _driver_factory: Optional[Callable[[], Any]] = None,
        **kwargs,
    ) -> None:
        if getattr(self, "_AppiumManager__initialized", False):
            return  # Already inited, no-op

        self.__initialized = True
        self.stake_id = stake_id or "0"
        self.driver: Optional[Any] = None
        self._driver_factory = _driver_factory
        self._session_capabilities: Dict[str, Any] = {}
        self._accessibility_tree_cache: Optional[Dict[str, Any]] = None
        self._latest_video_path: Optional[str] = None
        self._thread_pool: Optional[ThreadPoolExecutor] = None
        self._create_lock: Optional[asyncio.Lock] = None
        self._create_lock_loop: Optional[asyncio.AbstractEventLoop] = None

        self._record_video = get_global_conf().should_record_video()
        self._take_screenshots = get_global_conf().should_take_screenshots()

        proof_path = self._resolve_proof_path()
        self._screenshots_dir = os.path.join(proof_path, "screenshots")
        self._video_dir = os.path.join(proof_path, "videos")

        logger.debug(
            "AppiumManager init - stake_id=%s, device_os=%s, server_url=%s, record_video=%s",
            self.stake_id,
            get_device_os(),
            get_appium_server_url(),
            self._record_video,
        )

    def _resolve_proof_path(self) -> str:
        try:
            return get_global_conf().get_proof_path(test_id=self.stake_id)
        except Exception as e:
            logger.warning("Could not resolve proof path for mobile artifacts (%s); using temp fallback", e)
            import tempfile

            return os.path.join(tempfile.gettempdir(), "hercules_mobile_proofs", self.stake_id)

    # ------------------------------------------------------------------
    # Worker pool (all synchronous driver calls run off the event loop)
    # ------------------------------------------------------------------

    def _get_thread_pool(self) -> ThreadPoolExecutor:
        if self._thread_pool is None:
            worker_count = min(8, max(4, cpu_count()))
            self._thread_pool = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="AppiumUI")
        return self._thread_pool

    async def _run_in_ui_thread(self, func: Callable[[], Any], identifier: str) -> Any:
        start_time = time.time()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(self._get_thread_pool(), func)
        logger.info(f"[APPIUM_DRIVER_TIMING] Completed driver interaction: {identifier} in {time.time() - start_time:.2f} seconds")
        return result

    def shutdown_pool(self, wait: bool = False) -> None:
        """Shut down this instance's worker pool (idempotent)."""
        if self._thread_pool is not None:
            self._thread_pool.shutdown(wait=wait)
            self._thread_pool = None

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def build_default_capabilities(self) -> Dict[str, Any]:
        """Build capabilities from the environment-driven settings."""
        from testzeus_hercules.core.mobile.capabilities import build_capabilities

        warn_if_platform_name_set()
        return build_capabilities(
            get_device_os(),
            device_name=get_device_name(),
            automation_name=get_automation_name(),
            app=get_app_path() or get_apk_path(),
            platform_version=get_platform_version(),
            udid=get_device_uuid(),
            extra=get_appium_capabilities(),
        )

    def _get_create_lock(self) -> asyncio.Lock:
        """Return the session-creation lock bound to the running event loop."""
        loop = asyncio.get_running_loop()
        if self._create_lock is None or self._create_lock_loop is not loop:
            self._create_lock = asyncio.Lock()
            self._create_lock_loop = loop
        return self._create_lock

    async def create_session(self, capabilities: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create an Appium session against the configured server URL.

        Serialized per manager so concurrent callers cannot each open (and
        leak) a device session. When ``_driver_factory`` was injected (tests),
        it is used instead of ``webdriver.Remote`` and no Appium import is
        attempted. A driver that comes back without a session id is quit
        before the error is raised.
        """
        async with self._get_create_lock():
            if self.driver is not None:
                logger.info("Appium session already active for stake_id %s; reusing it", self.stake_id)
                return {"session_id": str(getattr(self.driver, "session_id", "")), "capabilities": self._session_capabilities, "reused": True}

            caps = capabilities if capabilities is not None else self.build_default_capabilities()

            def _connect() -> Any:
                if self._driver_factory is not None:
                    return self._driver_factory()
                if not _appium_client_available():
                    raise AppiumClientMissingError(APPIUM_INSTALL_HINT)
                from appium import webdriver
                from appium.options.common.base import AppiumOptions

                options = AppiumOptions()
                for cap_name, cap_value in caps.items():
                    # Values pass through unchanged: dict/list capabilities
                    # (processArguments, otherApps, ...) must stay JSON
                    # structures. A None value unsets the capability.
                    options.set_capability(cap_name, cap_value)
                return webdriver.Remote(command_executor=get_appium_server_url(), options=options)

            driver = await self._run_in_ui_thread(_connect, "create_session")
            session_id = str(getattr(driver, "session_id", "") or "")
            if not session_id:
                # Do not leak a half-open session: end it before reporting.
                try:
                    await self._run_in_ui_thread(driver.quit, "quit_sessionless_driver")
                except Exception as e:
                    logger.warning("Error quitting Appium driver that reported no session id: %s", e)
                raise MobileEnvironmentError("Appium session did not report a session id; check the Appium server logs")

            self.driver = driver
            self._session_capabilities = dict(caps)
            self._accessibility_tree_cache = None

            os.makedirs(self._screenshots_dir, exist_ok=True)
            os.makedirs(self._video_dir, exist_ok=True)

            # Auto-start screen recording for the run (gated + best-effort).
            await self.start_screen_recording()

            logger.info("Appium session created for stake_id %s (session_id=%s)", self.stake_id, session_id)
            return {"session_id": session_id, "capabilities": self._session_capabilities, "reused": False}

    async def quit_session(self) -> None:
        """Stop recording (best-effort) and end the Appium session."""
        driver = self.driver
        if driver is None:
            logger.info("No active Appium session to quit for stake_id %s", self.stake_id)
            return
        try:
            await self.stop_screen_recording()
        except Exception as e:
            logger.warning("Best-effort stop of screen recording failed during quit: %s", e)
        try:
            await self._run_in_ui_thread(driver.quit, "quit_session")
        except Exception as e:
            logger.warning("Error quitting Appium driver: %s", e)
        self.driver = None
        self._session_capabilities = {}
        self._accessibility_tree_cache = None

    def _teardown_session_sync(self) -> None:
        """Best-effort synchronous session teardown for the shutdown path.

        ``quit_session`` is async and only reached through the close tool, so
        an aborted scenario would otherwise leave the device session (and its
        recording) alive until ``newCommandTimeout`` expires. This runs the
        driver calls directly on the caller's thread: recording is stopped and
        saved best-effort, then the driver is quit. Every failure is logged
        and never raised.
        """
        driver = self.driver
        if driver is None:
            return
        if self._record_video:
            try:
                recording_data = driver.stop_recording_screen()
            except Exception as e:
                logger.debug("Best-effort stop of screen recording during teardown failed: %s", e)
            else:
                if recording_data:
                    try:
                        self._write_recording(recording_data)
                    except Exception as e:
                        logger.debug("Could not save screen recording during teardown: %s", e)
        try:
            driver.quit()
        except Exception as e:
            logger.debug("Error quitting Appium driver during teardown: %s", e)
        self.driver = None
        self._session_capabilities = {}
        self._accessibility_tree_cache = None

    # ------------------------------------------------------------------
    # Screen recording (Appium built-in API)
    # ------------------------------------------------------------------

    async def start_screen_recording(self) -> None:
        if not self._record_video:
            logger.debug("Screen recording is disabled by configuration (RECORD_VIDEO); skipping start")
            return
        if not self.driver:
            logger.warning("Cannot start screen recording without an active Appium session")
            return
        try:
            await self._run_in_ui_thread(lambda: self.driver.start_recording_screen(), "start_screen_recording")
        except Exception as e:
            logger.warning("Failed to start screen recording: %s", e)

    async def stop_screen_recording(self) -> Optional[str]:
        """Stop recording and write the video under ``<proof>/videos/``.

        Best-effort: errors are logged and ``None`` is returned instead of
        being raised, so session teardown never fails because of recording.
        """
        if not self._record_video:
            logger.debug("Screen recording is disabled by configuration (RECORD_VIDEO); skipping stop")
            return None
        if not self.driver:
            logger.warning("Cannot stop screen recording without an active Appium session")
            return None
        try:
            recording_data = await self._run_in_ui_thread(lambda: self.driver.stop_recording_screen(), "stop_screen_recording")
        except Exception as e:
            logger.warning("Failed to stop screen recording: %s", e)
            return None
        if not recording_data:
            logger.info("No recording data returned; skipping video artifact")
            return None
        return self._write_recording(recording_data)

    def _write_recording(self, recording_data: str) -> str:
        """Decode base64 recording data and write it under ``<proof>/videos/``."""
        video_bytes = base64.b64decode(recording_data)
        os.makedirs(self._video_dir, exist_ok=True)
        epoch = int(time.time())
        video_name = f"{self.stake_id}_{epoch}.mp4" if self.stake_id else f"recording_{epoch}.mp4"
        video_path = os.path.join(self._video_dir, video_name)
        with open(video_path, "wb") as f:
            f.write(video_bytes)
        self._latest_video_path = video_path
        logger.info("Screen recording saved to %s", video_path)
        return video_path

    def get_latest_video_path(self) -> Optional[str]:
        """Return the most recently written recording path, if any."""
        return self._latest_video_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_driver(self) -> Any:
        if not self.driver:
            raise MobileEnvironmentError("No active Appium session. Please initialize a session first.")
        return self.driver

    def _touch_press(self, x: int, y: int, hold_ms: int = 0) -> None:
        """Press (and optionally hold) at a point with one W3C touch pointer.

        Replaces the removed ``TouchAction``: a ``pointerMove`` / ``pointerDown`` /
        ``pause`` / ``pointerUp`` sequence sent via the W3C actions endpoint,
        which UiAutomator2 and XCUITest both implement.
        """
        from selenium.webdriver.common.actions import interaction
        from selenium.webdriver.common.actions.action_builder import ActionBuilder
        from selenium.webdriver.common.actions.pointer_input import PointerInput

        touch = PointerInput(interaction.POINTER_TOUCH, "touch")
        builder = ActionBuilder(self.driver, mouse=touch, duration=0)
        pointer = builder.pointer_action
        pointer.move_to_location(int(x), int(y))
        pointer.pointer_down()
        pointer.pause(max(0, int(hold_ms)) / 1000)
        pointer.pointer_up()
        builder.perform()

    def _clear_accessibility_cache(self) -> None:
        self._accessibility_tree_cache = None

    async def _maybe_screenshot(self, label: str) -> None:
        if not self._take_screenshots or not self.driver:
            return
        try:
            await self.take_screenshot(label)
        except Exception as e:
            logger.debug("Skipping %s screenshot: %s", label, e)

    # ------------------------------------------------------------------
    # Element interaction (accessibility id, then platform fallbacks)
    # ------------------------------------------------------------------

    @staticmethod
    def _locate(driver: Any, identifier: str) -> Any:
        """Find an element by identifier, trying each :func:`element_locators` strategy in order."""
        last_error: Optional[Exception] = None
        for strategy, selector in element_locators(identifier, get_device_os()):
            try:
                return driver.find_element(strategy, selector)
            except Exception as e:
                last_error = e
        raise MobileEnvironmentError(f"No element found for identifier {identifier!r} (tried accessibility id and platform fallbacks): {last_error}")

    async def find_element(self, accessibility_id: str) -> Any:
        driver = self._require_driver()
        return await self._run_in_ui_thread(lambda: self._locate(driver, accessibility_id), f"find_element({accessibility_id})")

    async def click_element(self, accessibility_id: str) -> None:
        driver = self._require_driver()
        await self._maybe_screenshot("click_element_start")

        def _do() -> None:
            element = self._locate(driver, accessibility_id)
            element.click()

        try:
            await self._run_in_ui_thread(_do, f"click_element({accessibility_id})")
        finally:
            self._clear_accessibility_cache()
        await self._maybe_screenshot("click_element_end")

    async def enter_text(self, accessibility_id: str, text: str) -> None:
        driver = self._require_driver()
        await self._maybe_screenshot("enter_text_start")

        def _do() -> None:
            element = self._locate(driver, accessibility_id)
            element.send_keys(text)

        try:
            await self._run_in_ui_thread(_do, f"enter_text({accessibility_id})")
        finally:
            self._clear_accessibility_cache()
        await self._maybe_screenshot("enter_text_end")

    async def clear_text(self, accessibility_id: str) -> None:
        driver = self._require_driver()
        await self._maybe_screenshot("clear_text_start")

        def _do() -> None:
            element = self._locate(driver, accessibility_id)
            element.clear()

        try:
            await self._run_in_ui_thread(_do, f"clear_text({accessibility_id})")
        finally:
            self._clear_accessibility_cache()
        await self._maybe_screenshot("clear_text_end")

    async def long_press_element(self, accessibility_id: str, duration_ms: int = 1000) -> None:
        driver = self._require_driver()
        await self._maybe_screenshot("long_press_start")

        def _do() -> None:
            element = self._locate(driver, accessibility_id)
            center_x, center_y = _element_center(element)
            self._touch_press(center_x, center_y, hold_ms=duration_ms)

        try:
            await self._run_in_ui_thread(_do, f"long_press_element({accessibility_id})")
        finally:
            self._clear_accessibility_cache()
        await self._maybe_screenshot("long_press_end")

    # ------------------------------------------------------------------
    # Gestures
    # ------------------------------------------------------------------

    async def perform_tap(self, x: int, y: int) -> None:
        driver = self._require_driver()
        await self._maybe_screenshot("tap_start")

        def _do() -> None:
            self._touch_press(int(x), int(y))

        try:
            await self._run_in_ui_thread(_do, f"perform_tap({x},{y})")
        finally:
            self._clear_accessibility_cache()
        await self._maybe_screenshot("tap_end")

    async def perform_swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: int = 800) -> None:
        driver = self._require_driver()
        await self._maybe_screenshot("swipe_start")

        def _do() -> None:
            driver.swipe(int(start_x), int(start_y), int(end_x), int(end_y), duration_ms)

        try:
            await self._run_in_ui_thread(_do, "perform_swipe")
        finally:
            self._clear_accessibility_cache()
        await self._maybe_screenshot("swipe_end")

    async def scroll(self, direction: str, percent: float = 0.4, duration_ms: int = 800) -> None:
        """Scroll the viewport. ``direction`` is ``up`` or ``down``.

        ``down`` reveals content below: the finger drags from the lower part
        of the screen to the upper part. ``up`` reveals content above: the
        finger drags downward.
        """
        viewport = await self.get_viewport_size()
        width, height = viewport["width"], viewport["height"]
        center_x = width // 2
        span = max(1, int(height * percent))
        if direction == "up":
            start_y, end_y = int(height * 0.3), min(height - 1, int(height * 0.3) + span)
        else:
            start_y, end_y = int(height * 0.7), max(0, int(height * 0.7) - span)
        await self.perform_swipe(center_x, start_y, center_x, end_y, duration_ms)

    async def scroll_up(self) -> None:
        await self.scroll("up")

    async def scroll_down(self) -> None:
        await self.scroll("down")

    # ------------------------------------------------------------------
    # Device keys
    # ------------------------------------------------------------------

    async def press_key(self, key_name: str) -> None:
        """Press a keyboard key by name (enter, tab, space, arrows...)."""
        driver = self._require_driver()
        if get_device_os() == "ios":
            key_char = ios_unicode_key(key_name)

            def _do_ios() -> None:
                from selenium.webdriver.common.action_chains import ActionChains

                ActionChains(driver).send_keys(key_char).perform()

            await self._run_in_ui_thread(_do_ios, f"press_key({key_name})")
        else:
            keycode = android_keycode(key_name)
            await self._run_in_ui_thread(lambda: driver.press_keycode(keycode), f"press_key({key_name})")
        self._clear_accessibility_cache()

    async def press_hardware_key(self, key_name: str) -> None:
        """Press a hardware button by name (volume_up, power, home...)."""
        driver = self._require_driver()
        if get_device_os() == "ios":
            normalized = key_name.strip().lower()
            if normalized not in IOS_HARDWARE_BUTTONS:
                raise ValueError(f"Hardware key {key_name!r} is not supported on iOS. Supported: {sorted(IOS_HARDWARE_BUTTONS)}")

            def _do_ios() -> None:
                driver.execute_script("mobile: pressButton", {"name": IOS_HARDWARE_BUTTONS[normalized]})

            await self._run_in_ui_thread(_do_ios, f"press_hardware_key({key_name})")
            self._clear_accessibility_cache()
            return
        keycode = android_hardware_keycode(key_name)
        await self._run_in_ui_thread(lambda: driver.press_keycode(keycode), f"press_hardware_key({key_name})")
        self._clear_accessibility_cache()

    async def press_key_sequence(self, sequence: str) -> None:
        """Press a key sequence such as ``"Control+A,Enter"``."""
        driver = self._require_driver()
        segments = expand_key_sequence(sequence)
        if get_device_os() == "ios":
            # iOS only supports keys with a unicode keyboard mapping; fail
            # fast instead of silently skipping segments (which used to
            # report success for sequences that were never fully sent).
            unmapped = sorted({_keycode_display_name(main_code) for _modifier_codes, main_code in segments if not _keycode_to_unicode_char(main_code)})
            if unmapped:
                raise ValueError(f"Keys not supported in key sequences on iOS: {', '.join(unmapped)}. " "Use press_key with a supported key or an in-app alternative.")
            # Modifier combinations cannot be sent through the iOS keyboard
            # path; refuse them instead of silently pressing the bare key.
            if any(modifier_codes for modifier_codes, _main_code in segments):
                raise ValueError(f"Modifier key combinations (e.g. Control+A) are not supported in key sequences on iOS: {sequence!r}. " "Use an in-app alternative.")

            def _do_ios() -> None:
                from selenium.webdriver.common.action_chains import ActionChains

                for _modifier_codes, main_code in segments:
                    ActionChains(driver).send_keys(_keycode_to_unicode_char(main_code)).perform()

            await self._run_in_ui_thread(_do_ios, f"press_key_sequence({sequence})")
        else:

            def _do_android() -> None:
                # The Appium client has no key_down/key_up: modifiers travel as
                # the meta-state mask of the main key press.
                for modifier_codes, main_code in segments:
                    if modifier_codes:
                        driver.press_keycode(main_code, metastate=android_metastate(modifier_codes))
                    else:
                        driver.press_keycode(main_code)

            await self._run_in_ui_thread(_do_android, f"press_key_sequence({sequence})")
        self._clear_accessibility_cache()

    async def press_back(self) -> None:
        driver = self._require_driver()
        if get_device_os() == "ios":

            await self._run_in_ui_thread(lambda: self._ios_navigate_back(driver), "press_back")
        else:
            await self._run_in_ui_thread(lambda: driver.press_keycode(android_hardware_keycode("back")), "press_back")
        self._clear_accessibility_cache()

    def _ios_navigate_back(self, driver: Any) -> None:
        """iOS has no back button: tap a nav-bar *back* button, else swipe from the left edge.

        Only a button identified as a back button (see
        ``IOS_BACK_BUTTON_CLASS_CHAIN``) is tapped, never a leading Edit /
        Cancel / Close button.
        """
        try:
            driver.find_element(_IOS_CLASS_CHAIN_LOCATOR, IOS_BACK_BUTTON_CLASS_CHAIN).click()
            return
        except Exception as e:
            logger.debug("No navigation-bar back button found (%s); using the edge-swipe back gesture", e)
        size = driver.get_window_size()
        width, height = int(size["width"]), int(size["height"])
        driver.swipe(1, height // 2, max(2, int(width * 0.7)), height // 2, 300)

    async def press_home(self) -> None:
        driver = self._require_driver()
        if get_device_os() == "ios":

            def _do_ios() -> None:
                driver.execute_script("mobile: pressButton", {"name": "home"})

            await self._run_in_ui_thread(_do_ios, "press_home")
        else:
            await self._run_in_ui_thread(lambda: driver.press_keycode(android_hardware_keycode("home")), "press_home")
        self._clear_accessibility_cache()

    # ------------------------------------------------------------------
    # Screen reading / snapshots
    # ------------------------------------------------------------------

    async def get_accessibility_tree(self, use_cache: bool = True) -> Dict[str, Any]:
        if use_cache and self._accessibility_tree_cache is not None:
            return self._accessibility_tree_cache
        driver = self._require_driver()
        page_source = await self._run_in_ui_thread(lambda: driver.page_source, "get_accessibility_tree")
        tree = parse_accessibility_tree(page_source, get_device_os())
        self._accessibility_tree_cache = tree
        return tree

    def clear_accessibility_tree_cache(self) -> None:
        self._accessibility_tree_cache = None

    async def read_screen(self, use_cache: bool = True, refresh: bool = False) -> str:
        """Render the screen legend. ``refresh=True`` bypasses the cached tree.

        The cache is cleared by every mutating action, but cannot notice
        changes the app makes by itself (loading, animation); ``refresh``
        forces a fresh page-source fetch for those cases.
        """
        tree = await self.get_accessibility_tree(use_cache and not refresh)
        return render_screen_legend(tree)

    async def take_screenshot(self, name: Optional[str] = None) -> Optional[str]:
        driver = self._require_driver()
        os.makedirs(self._screenshots_dir, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "mobile_screenshot")
        screenshot_path = os.path.join(self._screenshots_dir, f"{safe_name}_{int(time.time())}.png")
        await self._run_in_ui_thread(lambda: driver.get_screenshot_as_file(screenshot_path), f"take_screenshot({safe_name})")
        logger.info("Screenshot saved to %s", screenshot_path)
        return screenshot_path

    async def get_viewport_size(self) -> Dict[str, int]:
        driver = self._require_driver()
        size = await self._run_in_ui_thread(lambda: driver.get_window_size(), "get_viewport_size")
        return {"width": int(size["width"]), "height": int(size["height"])}


def _element_center(element: Any) -> "tuple[int, int]":
    """Return the center point of a located element from its location and size."""
    location = element.location
    size = element.size
    return int(location["x"] + size["width"] / 2), int(location["y"] + size["height"] / 2)


def _keycode_display_name(keycode: int) -> str:
    """Best-effort human-readable name for an Android keycode (error messages)."""
    for name, code in ANDROID_KEYCODES.items():
        if code == keycode:
            return name
    return f"keycode {keycode}"


def _keycode_to_unicode_char(keycode: int) -> str:
    """Best-effort reverse mapping of an Android keycode back to a character."""
    for name, code in ANDROID_KEYCODES.items():
        if code == keycode and name in ("enter", "return"):
            return "\ue007"
    if 29 <= keycode <= 54:
        return chr(ord("a") + keycode - 29)
    if 7 <= keycode <= 16:
        return chr(ord("0") + keycode - 7)
    return ""
