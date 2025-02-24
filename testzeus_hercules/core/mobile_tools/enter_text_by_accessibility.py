from typing import Annotated, Dict
import time
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["mobile_nav_agent"],
    description="Enter text into an element using its accessibility ID.",
    name="enter_text_by_id",
)
def enter_text_by_id(
    text: Annotated[str, "Text to enter."],
    res_id: Annotated[str, "Resource ID of the element."] = "",
    accessibility_id: Annotated[str, "Accessibility ID of the element."] = "",
    wait_before_action: Annotated[
        float, "Time to wait before entering text (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the text entry operation."]:
    """
    Enter text into an element using its resource ID or accessibility ID.
    """
    try:
        appium_manager = AppiumManager()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Enter the text
        appium_manager.enter_text_by_id(
            text=text,
            res_id=res_id,
            accessibility_id=accessibility_id,
        )

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        return {
            "status": "success",
            "message": f"Entered text into element with res_id='{res_id}' or accessibility_id='{accessibility_id}'",
        }

    except Exception as e:
        logger.error(f"Error in enter_text_by_id: {str(e)}")
        return {"error": str(e)}
