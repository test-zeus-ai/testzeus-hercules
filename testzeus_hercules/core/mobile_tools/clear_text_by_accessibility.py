from typing import Annotated, Dict
import time
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["mobile_nav_agent"],
    description="Clear text from an element using its accessibility ID.",
    name="clear_text_by_id",
)
def clear_text_by_id(
    res_id: Annotated[str, "Resource ID of the element."] = "",
    accessibility_id: Annotated[str, "Accessibility ID of the element."] = "",
    wait_before_action: Annotated[
        float, "Time to wait before clearing text (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the text clearing operation."]:
    """
    Clear text from an element using its resource ID or accessibility ID.
    """
    try:
        appium_manager = AppiumManager()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Clear the text
        appium_manager.clear_text_by_id(
            res_id=res_id,
            accessibility_id=accessibility_id,
        )

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        return {
            "status": "success",
            "message": f"Cleared text from element with res_id='{res_id}' or accessibility_id='{accessibility_id}'",
        }

    except Exception as e:
        logger.error(f"Error in clear_text_by_id: {str(e)}")
        return {"error": str(e)}
