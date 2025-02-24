from typing import Annotated, Dict
import time
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["mobile_nav_agent"],
    description="Long press on an element using its accessibility ID.",
    name="long_press_by_id",
)
def long_press_by_id(
    res_id: Annotated[str, "Resource ID of the element to long press."] = "",
    accessibility_id: Annotated[
        str, "Accessibility ID of the element to long press."
    ] = "",
    duration: Annotated[int, "Duration of the long press in milliseconds."] = 1000,
    wait_before_action: Annotated[
        float, "Time to wait before long pressing (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the long press operation."]:
    """
    Long press on an element using its resource ID or accessibility ID.
    """
    try:
        appium_manager = AppiumManager()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Perform the long press
        appium_manager.long_press_by_id(
            res_id=res_id,
            accessibility_id=accessibility_id,
            duration=duration,
        )

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        return {
            "status": "success",
            "message": f"Long pressed element with res_id='{res_id}' or accessibility_id='{accessibility_id}' for {duration}ms",
        }

    except Exception as e:
        logger.error(f"Error in long_press_by_id: {str(e)}")
        return {"error": str(e)}
