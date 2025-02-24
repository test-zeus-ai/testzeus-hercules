from typing import Annotated, Dict
import time
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["mobile_nav_agent"],
    description="Perform a swipe gesture between two points on the screen.",
    name="swipe",
)
def swipe(
    start_x: Annotated[float, "Starting X coordinate for the swipe."],
    start_y: Annotated[float, "Starting Y coordinate for the swipe."],
    end_x: Annotated[float, "Ending X coordinate for the swipe."],
    end_y: Annotated[float, "Ending Y coordinate for the swipe."],
    duration: Annotated[int, "Duration of the swipe in milliseconds."] = 800,
    wait_before_action: Annotated[
        float, "Time to wait before swiping (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the swipe operation."]:
    """
    Perform a swipe gesture between two points on the screen.
    """
    try:
        appium_manager = AppiumManager()

        # Get viewport size to validate coordinates
        viewport = appium_manager.get_viewport_size()
        if viewport:
            width, height = viewport["width"], viewport["height"]
            if any(coord > width for coord in [start_x, end_x]) or any(
                coord > height for coord in [start_y, end_y]
            ):
                return {
                    "error": f"Coordinates are outside viewport bounds ({width}, {height})"
                }

        # Wait before swiping if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Perform the swipe
        appium_manager.perform_swipe(
            int(start_x), int(start_y), int(end_x), int(end_y), duration
        )

        # Wait after swipe
        time.sleep(get_global_conf().get_delay_time())

        return {
            "status": "success",
            "message": f"Swiped from ({start_x}, {start_y}) to ({end_x}, {end_y})",
        }

    except Exception as e:
        logger.error(f"Error in swipe: {str(e)}")
        return {"error": str(e)}
