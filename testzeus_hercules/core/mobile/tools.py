"""Mobile device automation tools (registered for ``mobile_nav_agent``).

Every tool follows the shared tool conventions: it reports interactions via
``add_event``, returns a human-readable string, and NEVER raises — failures
(no session, missing Appium client, validation errors) come back as graceful
error strings so the agent can recover.

This module is imported (and therefore registered into the tool registry)
only when mobile automation is enabled — see ``core/mobile/__init__.py``.
Importing it directly is always safe: the Appium client is imported lazily
inside the manager.
"""

import asyncio
import importlib.util
from typing import Annotated, Optional

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.mobile.appium_manager import (
    APPIUM_INSTALL_HINT,
    AppiumManager,
)
from testzeus_hercules.core.mobile.settings import get_device_os, get_platform_name
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

NO_SESSION_MESSAGE = "Error: No active Appium session. Please initialize a session first with start_mobile_session."
INSTALL_HINT_MESSAGE = f"Error: {APPIUM_INSTALL_HINT}"


def _appium_client_available() -> bool:
    try:
        return importlib.util.find_spec("appium") is not None
    except Exception:  # pragma: no cover - defensive
        return False


def _current_manager() -> AppiumManager:
    """Return the AppiumManager bound to the current test id.

    Proof artifacts (screenshots, recordings) are keyed by the manager's
    stake, mirroring how browser evidence keys proofs: binding to the
    current test id keeps them under that run's proof folder instead of a
    stake-less ``proofs/0`` directory. Singleton semantics are preserved —
    the same test id always yields the same manager.
    """
    return AppiumManager(stake_id=get_global_conf().get_default_test_id())


def _ensure_ready(manager: AppiumManager) -> Optional[str]:
    """Return a graceful error string when mobile automation cannot proceed."""
    if not _appium_client_available():
        return INSTALL_HINT_MESSAGE
    if manager.driver is None:
        return NO_SESSION_MESSAGE
    return None


def _format_error(action: str, error: Exception) -> str:
    logger.error("Mobile tool %s failed: %s", action, error, exc_info=True)
    return f"Error: {action} failed: {error}"


def _validate_coordinates(viewport: dict, **coords: int) -> Optional[str]:
    """Validate point coordinates against the current viewport."""
    width, height = viewport.get("width", 0), viewport.get("height", 0)
    for coord_name, value in coords.items():
        axis = "x" if coord_name.startswith("x") else "y"
        limit = width if axis == "x" else height
        if not (0 <= value < limit):
            return f"Error: Coordinate {coord_name}={value} is outside the device viewport " f"({width}x{height}). Read the screen and retry within bounds."
    return None


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Starts a mobile device automation session via Appium using the configured environment settings. Must be called before any other mobile tool.""",
    name="start_mobile_session",
)
async def start_mobile_session() -> Annotated[str, "Session start result"]:
    add_event(EventType.INTERACTION, EventData(detail="start_mobile_session"))
    try:
        if not _appium_client_available():
            return INSTALL_HINT_MESSAGE
        manager = _current_manager()
        result = await manager.create_session()
        platform = get_platform_name()
        suffix = "" if result.get("reused") else " Screen recording starts automatically if RECORD_VIDEO is enabled."
        return f"Successfully started mobile session on {platform} (session_id: {result.get('session_id')}).{suffix}"
    except Exception as e:
        return _format_error("start_mobile_session", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Closes the active mobile device automation session. Stops screen recording (best-effort) before quitting.""",
    name="close_mobile_session",
)
async def close_mobile_session() -> Annotated[str, "Session close result"]:
    add_event(EventType.INTERACTION, EventData(detail="close_mobile_session"))
    try:
        manager = _current_manager()
        if not _appium_client_available():
            return INSTALL_HINT_MESSAGE
        if manager.driver is None:
            return NO_SESSION_MESSAGE
        await manager.quit_session()
        video_note = f" Recording saved to: {manager.get_latest_video_path()}" if manager.get_latest_video_path() else ""
        return f"Successfully closed the mobile session.{video_note}"
    except Exception as e:
        return _format_error("close_mobile_session", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Performs tap action at the specified screen coordinates (pixels).""",
    name="tap",
)
async def tap(
    x: Annotated[int, "X coordinate on the device screen (pixels)"],
    y: Annotated[int, "Y coordinate on the device screen (pixels)"],
    wait_before_action: Annotated[float, "Wait time before performing the tap (seconds)"] = 0.0,
) -> Annotated[str, "Tap action result"]:
    add_event(EventType.INTERACTION, EventData(detail="tap"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        if wait_before_action > 0:
            await asyncio.sleep(wait_before_action)
        viewport = await manager.get_viewport_size()
        invalid = _validate_coordinates(viewport, x=x, y=y)
        if invalid:
            return invalid
        await manager.perform_tap(x, y)
        await asyncio.sleep(get_global_conf().get_delay_time())
        return f"Successfully performed tap at coordinates: ({x}, {y}). Read the screen to verify the outcome."
    except Exception as e:
        return _format_error("tap", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Performs a swipe gesture from a start point to an end point (pixels).""",
    name="swipe",
)
async def swipe(
    start_x: Annotated[int, "Start X coordinate (pixels)"],
    start_y: Annotated[int, "Start Y coordinate (pixels)"],
    end_x: Annotated[int, "End X coordinate (pixels)"],
    end_y: Annotated[int, "End Y coordinate (pixels)"],
    duration_ms: Annotated[int, "Swipe duration in milliseconds"] = 800,
) -> Annotated[str, "Swipe action result"]:
    add_event(EventType.INTERACTION, EventData(detail="swipe"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        viewport = await manager.get_viewport_size()
        invalid = _validate_coordinates(viewport, start_x=start_x, start_y=start_y, end_x=end_x, end_y=end_y)
        if invalid:
            return invalid
        await manager.perform_swipe(start_x, start_y, end_x, end_y, duration_ms)
        await asyncio.sleep(get_global_conf().get_delay_time())
        return f"Successfully performed swipe from ({start_x}, {start_y}) to ({end_x}, {end_y})."
    except Exception as e:
        return _format_error("swipe", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Scrolls the current view up (reveals content above the visible area).""",
    name="scroll_up",
)
async def scroll_up() -> Annotated[str, "Scroll action result"]:
    add_event(EventType.INTERACTION, EventData(detail="scroll_up"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        await manager.scroll_up()
        await asyncio.sleep(get_global_conf().get_delay_time())
        return "Successfully scrolled up. Read the screen to check for newly revealed elements."
    except Exception as e:
        return _format_error("scroll_up", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Scrolls the current view down (reveals content below the visible area).""",
    name="scroll_down",
)
async def scroll_down() -> Annotated[str, "Scroll action result"]:
    add_event(EventType.INTERACTION, EventData(detail="scroll_down"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        await manager.scroll_down()
        await asyncio.sleep(get_global_conf().get_delay_time())
        return "Successfully scrolled down. Read the screen to check for newly revealed elements."
    except Exception as e:
        return _format_error("scroll_down", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Long presses the element identified by its accessibility id.""",
    name="long_press_element",
)
async def long_press_element(
    accessibility_id: Annotated[str, "Accessibility id of the target element from the screen legend"],
    duration_ms: Annotated[int, "Press-and-hold duration in milliseconds"] = 1000,
) -> Annotated[str, "Long press action result"]:
    add_event(EventType.INTERACTION, EventData(detail="long_press_element"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        await manager.long_press_element(accessibility_id, duration_ms)
        await asyncio.sleep(get_global_conf().get_delay_time())
        return f"Successfully long pressed element with accessibility id '{accessibility_id}' for {duration_ms} ms."
    except Exception as e:
        return _format_error("long_press_element", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Clicks the element identified by its accessibility id.""",
    name="click_element",
)
async def click_element(accessibility_id: Annotated[str, "Accessibility id of the target element from the screen legend"]) -> Annotated[str, "Click action result"]:
    add_event(EventType.INTERACTION, EventData(detail="click_element"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        await manager.click_element(accessibility_id)
        await asyncio.sleep(get_global_conf().get_delay_time())
        return f"Successfully clicked element with accessibility id '{accessibility_id}'."
    except Exception as e:
        return _format_error("click_element", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Enters text into the element identified by its accessibility id.""",
    name="enter_text",
)
async def enter_text(
    accessibility_id: Annotated[str, "Accessibility id of the target input field from the screen legend"],
    text: Annotated[str, "Text to enter into the field"],
) -> Annotated[str, "Text entry result"]:
    add_event(EventType.INTERACTION, EventData(detail="enter_text"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        await manager.enter_text(accessibility_id, text)
        await asyncio.sleep(get_global_conf().get_delay_time())
        return f"Successfully entered text into element with accessibility id '{accessibility_id}'."
    except Exception as e:
        return _format_error("enter_text", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Clears the text of the element identified by its accessibility id.""",
    name="clear_text",
)
async def clear_text(accessibility_id: Annotated[str, "Accessibility id of the target input field from the screen legend"]) -> Annotated[str, "Text clearing result"]:
    add_event(EventType.INTERACTION, EventData(detail="clear_text"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        await manager.clear_text(accessibility_id)
        await asyncio.sleep(get_global_conf().get_delay_time())
        return f"Successfully cleared text of element with accessibility id '{accessibility_id}'."
    except Exception as e:
        return _format_error("clear_text", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Presses a keyboard key by name. Supported: enter, tab, space, backspace, delete, escape, up, down, left, right.""",
    name="press_key",
)
async def press_key(key_name: Annotated[str, "Keyboard key name (e.g. enter, tab, space, backspace, escape, up, down, left, right)"]) -> Annotated[str, "Key press result"]:
    add_event(EventType.INTERACTION, EventData(detail="press_key"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        await manager.press_key(key_name)
        await asyncio.sleep(get_global_conf().get_delay_time())
        return f"Successfully pressed key '{key_name}'."
    except Exception as e:
        return _format_error("press_key", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Presses a hardware button by name. Supported: volume_up, volume_down, power, camera, call, end_call, menu, back, home, app_switch.""",
    name="press_hardware_key",
)
async def press_hardware_key(key_name: Annotated[str, "Hardware button name (e.g. volume_up, volume_down, power, home, back, app_switch)"]) -> Annotated[str, "Hardware key press result"]:
    add_event(EventType.INTERACTION, EventData(detail="press_hardware_key"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        await manager.press_hardware_key(key_name)
        await asyncio.sleep(get_global_conf().get_delay_time())
        return f"Successfully pressed hardware key '{key_name}'."
    except Exception as e:
        return _format_error("press_hardware_key", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Presses a key sequence such as 'Control+A,Enter'. Segments are separated by commas; modifiers within a segment by '+'.""",
    name="press_key_sequence",
)
async def press_key_sequence(sequence: Annotated[str, "Key sequence, e.g. 'Control+A,Enter'"]) -> Annotated[str, "Key sequence result"]:
    add_event(EventType.INTERACTION, EventData(detail="press_key_sequence"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        await manager.press_key_sequence(sequence)
        await asyncio.sleep(get_global_conf().get_delay_time())
        return f"Successfully pressed key sequence '{sequence}'."
    except Exception as e:
        return _format_error("press_key_sequence", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Presses the device BACK button/navigation.""",
    name="press_back_button",
)
async def press_back_button() -> Annotated[str, "Back navigation result"]:
    add_event(EventType.INTERACTION, EventData(detail="press_back_button"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        await manager.press_back()
        await asyncio.sleep(get_global_conf().get_delay_time())
        return "Successfully pressed the back button."
    except Exception as e:
        return _format_error("press_back_button", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Presses the device HOME button.""",
    name="press_home_button",
)
async def press_home_button() -> Annotated[str, "Home navigation result"]:
    add_event(EventType.INTERACTION, EventData(detail="press_home_button"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        await manager.press_home()
        await asyncio.sleep(get_global_conf().get_delay_time())
        return "Successfully pressed the home button."
    except Exception as e:
        return _format_error("press_home_button", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Reads the current device screen as a compact accessibility-tree legend with element identifiers, values and bounds. Use this before/after actions to understand the screen state. The reading is reused until an action changes the screen; pass refresh=true to force a fresh reading.""",
    name="read_mobile_screen",
)
async def read_mobile_screen(
    refresh: Annotated[bool, "Set true to bypass the cached reading and fetch the screen again (use when the app may have changed on its own)"] = False,
) -> Annotated[str, "Accessibility-tree legend of the current screen"]:
    add_event(EventType.INTERACTION, EventData(detail="read_mobile_screen"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        legend = await manager.read_screen(refresh=bool(refresh))
        return legend if legend.strip() else "The screen legend is empty; try scrolling or re-reading the screen."
    except Exception as e:
        return _format_error("read_mobile_screen", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Captures a screenshot of the current device screen and saves it under the run's proof artifacts.""",
    name="take_mobile_screenshot",
)
async def take_mobile_screenshot(name: Annotated[str, "Descriptive name for the screenshot file"] = "mobile_screenshot") -> Annotated[str, "Screenshot result"]:
    add_event(EventType.INTERACTION, EventData(detail="take_mobile_screenshot"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        screenshot_path = await manager.take_screenshot(name)
        if screenshot_path is None:
            return "Error: Unable to capture screenshot. Screenshot capture may be disabled via TAKE_SCREENSHOTS=false."
        return f"Successfully captured screenshot saved to: {screenshot_path}"
    except Exception as e:
        return _format_error("take_mobile_screenshot", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Starts screen recording on the device. A recording is also started automatically when a session begins (if RECORD_VIDEO is enabled).""",
    name="start_screen_recording",
)
async def start_screen_recording() -> Annotated[str, "Recording start result"]:
    add_event(EventType.INTERACTION, EventData(detail="start_screen_recording"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        await manager.start_screen_recording()
        return "Successfully started screen recording."
    except Exception as e:
        return _format_error("start_screen_recording", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Stops screen recording and saves the video under the run's proof artifacts. Returns the saved video path.""",
    name="stop_screen_recording",
)
async def stop_screen_recording() -> Annotated[str, "Recording stop result with saved video path"]:
    add_event(EventType.INTERACTION, EventData(detail="stop_screen_recording"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        video_path = await manager.stop_screen_recording()
        if video_path is None:
            return "Error: No recording was saved. Recording may be disabled via RECORD_VIDEO=false, or no recording was active."
        return f"Successfully stopped screen recording. Video saved to: {video_path}"
    except Exception as e:
        return _format_error("stop_screen_recording", e)


@tool(
    agent_names=["mobile_nav_agent"],
    description="""Returns the device viewport size in pixels as width x height. Use it to keep gesture coordinates within bounds.""",
    name="get_mobile_viewport_size",
)
async def get_mobile_viewport_size() -> Annotated[str, "Viewport size description"]:
    add_event(EventType.INTERACTION, EventData(detail="get_mobile_viewport_size"))
    try:
        manager = _current_manager()
        ready_error = _ensure_ready(manager)
        if ready_error:
            return ready_error
        viewport = await manager.get_viewport_size()
        return f"Device viewport size: {viewport['width']}x{viewport['height']} pixels."
    except Exception as e:
        return _format_error("get_mobile_viewport_size", e)


MOBILE_TOOLS: tuple = (
    "start_mobile_session",
    "close_mobile_session",
    "tap",
    "swipe",
    "scroll_up",
    "scroll_down",
    "long_press_element",
    "click_element",
    "enter_text",
    "clear_text",
    "press_key",
    "press_hardware_key",
    "press_key_sequence",
    "press_back_button",
    "press_home_button",
    "read_mobile_screen",
    "take_mobile_screenshot",
    "start_screen_recording",
    "stop_screen_recording",
    "get_mobile_viewport_size",
)


def get_registered_mobile_tools() -> list:
    """Return the mobile tool entries currently registered under ``mobile_nav_agent``."""
    from testzeus_hercules.core.tools.tool_registry import tool_registry

    return list(tool_registry.get("mobile_nav_agent", []))
