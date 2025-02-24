from typing import Annotated, Dict
import time
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["mobile_nav_agent"],
    description="Press the back button on the device.",
    name="press_back_button",
)
def press_back_button(
    wait_before_action: Annotated[
        float, "Time to wait before pressing (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the back button press operation."]:
    """
    Press the back button on the device.
    """
    try:
        appium_manager = AppiumManager()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Press back button
        appium_manager.press_back()

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        return {
            "status": "success",
            "message": "Pressed back button",
        }

    except Exception as e:
        logger.error(f"Error in press_back_button: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["mobile_nav_agent"],
    description="Press the home button on the device.",
    name="press_home_button",
)
def press_home_button(
    wait_before_action: Annotated[
        float, "Time to wait before pressing (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the home button press operation."]:
    """
    Press the home button on the device.
    """
    try:
        appium_manager = AppiumManager()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Press home button
        appium_manager.press_home()

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        return {
            "status": "success",
            "message": "Pressed home button",
        }

    except Exception as e:
        logger.error(f"Error in press_home_button: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["mobile_nav_agent"],
    description="Press the app switch button on the device.",
    name="press_app_switch_button",
)
def press_app_switch_button(
    wait_before_action: Annotated[
        float, "Time to wait before pressing (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the app switch button press operation."]:
    """
    Press the app switch button on the device.
    """
    try:
        appium_manager = AppiumManager()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Press app switch button
        appium_manager.press_app_switch()

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        return {
            "status": "success",
            "message": "Pressed app switch button",
        }

    except Exception as e:
        logger.error(f"Error in press_app_switch_button: {str(e)}")
        return {"error": str(e)}
