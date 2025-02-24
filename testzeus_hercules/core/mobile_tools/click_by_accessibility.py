from typing import Annotated, Dict
import time
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["mobile_nav_agent"],
    description="Click on an element using its accessibility ID.",
    name="click_by_id",
)
def click_by_id(
    res_id: Annotated[str, "Resource ID of the element to click."] = "",
    accessibility_id: Annotated[str, "Accessibility ID of the element to click."] = "",
    wait_before_click: Annotated[
        float, "Time to wait before clicking (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the click operation."]:
    """
    Click on an element using its resource ID or accessibility ID.
    """
    try:
        appium_manager = AppiumManager()

        # Wait before clicking if specified
        if wait_before_click > 0:
            time.sleep(wait_before_click)

        # Perform the click
        appium_manager.click_by_id(
            res_id=res_id,
            accessibility_id=accessibility_id,
        )

        # Wait after click
        time.sleep(get_global_conf().get_delay_time())

        return {
            "status": "success",
            "message": f"Clicked element with res_id='{res_id}' or accessibility_id='{accessibility_id}'",
        }

    except Exception as e:
        logger.error(f"Error in click_by_id: {str(e)}")
        return {"error": str(e)}
