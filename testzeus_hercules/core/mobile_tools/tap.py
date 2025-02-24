from typing import Annotated, Dict
import time
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["mobile_nav_agent"],
    description="Tap at specific coordinates on the screen.",
    name="tap",
)
def tap(
    x: Annotated[float, "X coordinate for the tap."],
    y: Annotated[float, "Y coordinate for the tap."],
    wait_before_action: Annotated[
        float, "Time to wait before tapping (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the tap operation."]:
    """
    Tap at specific coordinates on the screen.
    """
    try:
        appium_manager = AppiumManager()

        # Get viewport size to validate coordinates
        viewport = appium_manager.get_viewport_size()
        if viewport:
            if x > viewport["width"] or y > viewport["height"]:
                return {
                    "error": f"Coordinates ({x}, {y}) are outside viewport bounds ({viewport['width']}, {viewport['height']})"
                }

        # Wait before tapping if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Perform the tap
        appium_manager.perform_tap(int(x), int(y))

        # Wait after tap
        time.sleep(get_global_conf().get_delay_time())

        return {
            "status": "success",
            "message": f"Tapped at coordinates ({x}, {y})",
        }

    except Exception as e:
        logger.error(f"Error in tap: {str(e)}")
        return {"error": str(e)}
