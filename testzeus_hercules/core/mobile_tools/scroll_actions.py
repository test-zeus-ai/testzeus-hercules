from typing import Annotated, Dict
import time
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["mobile_nav_agent"],
    description="Scroll up on the screen.",
    name="scroll_up",
)
def scroll_up(
    wait_before_action: Annotated[
        float, "Time to wait before scrolling (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the scroll up operation."]:
    """
    Scroll up on the screen.
    """
    try:
        appium_manager = AppiumManager()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Perform scroll up
        success = appium_manager.scroll_up()

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        if success:
            return {
                "status": "success",
                "message": "Scrolled up successfully",
            }
        else:
            return {
                "status": "warning",
                "message": "Reached top of scrollable area",
            }

    except Exception as e:
        logger.error(f"Error in scroll_up: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["mobile_nav_agent"],
    description="Scroll down on the screen.",
    name="scroll_down",
)
def scroll_down(
    wait_before_action: Annotated[
        float, "Time to wait before scrolling (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the scroll down operation."]:
    """
    Scroll down on the screen.
    """
    try:
        appium_manager = AppiumManager()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Perform scroll down
        success = appium_manager.scroll_down()

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        if success:
            return {
                "status": "success",
                "message": "Scrolled down successfully",
            }
        else:
            return {
                "status": "warning",
                "message": "Reached bottom of scrollable area",
            }

    except Exception as e:
        logger.error(f"Error in scroll_down: {str(e)}")
        return {"error": str(e)}
