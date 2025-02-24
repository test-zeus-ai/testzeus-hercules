"""
iOS-specific gesture implementations for TestZeus-Hercules.
"""

from typing import Annotated, Dict, Optional

from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["mobile_nav_agent"],
    description="Perform a pinch gesture on the screen.",
    name="perform_pinch",
)
def perform_pinch(
    scale: Annotated[float, "Scale factor for the pinch gesture."],
    velocity: Annotated[float, "Velocity of the pinch gesture."],
) -> Annotated[Dict[str, str], "Result of the pinch gesture operation."]:
    """
    Perform a pinch gesture on the screen.
    """
    try:
        appium_manager = AppiumManager()
        appium_manager.perform_pinch(scale, velocity)
        return {"status": "success", "message": "Pinch gesture performed successfully"}
    except Exception as e:
        logger.error(f"Error performing pinch gesture: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["mobile_nav_agent"],
    description="Perform a force touch gesture on the screen.",
    name="perform_force_touch",
)
def perform_force_touch(
    pressure: Annotated[float, "Pressure level for the force touch."],
    duration: Annotated[float, "Duration of the force touch in seconds."],
) -> Annotated[Dict[str, str], "Result of the force touch operation."]:
    """
    Perform a force touch gesture on the screen.
    """
    try:
        appium_manager = AppiumManager()
        appium_manager.perform_force_touch(pressure, duration)
        return {"status": "success", "message": "Force touch performed successfully"}
    except Exception as e:
        logger.error(f"Error performing force touch: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["mobile_nav_agent"],
    description="Perform a double tap gesture on the screen.",
    name="perform_double_tap",
)
def perform_double_tap(
    x: Annotated[float, "X coordinate for the double tap."],
    y: Annotated[float, "Y coordinate for the double tap."],
) -> Annotated[Dict[str, str], "Result of the double tap operation."]:
    """
    Perform a double tap gesture on the screen.
    """
    try:
        appium_manager = AppiumManager()
        appium_manager.perform_double_tap(x, y)
        return {"status": "success", "message": "Double tap performed successfully"}
    except Exception as e:
        logger.error(f"Error performing double tap: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["mobile_nav_agent"],
    description="Perform a haptic feedback gesture.",
    name="perform_haptic",
)
def perform_haptic(
    type: Annotated[str, "Type of haptic feedback to perform."],
) -> Annotated[Dict[str, str], "Result of the haptic feedback operation."]:
    """
    Perform a haptic feedback gesture.
    """
    try:
        appium_manager = AppiumManager()
        appium_manager.perform_haptic(type)
        return {
            "status": "success",
            "message": "Haptic feedback performed successfully",
        }
    except Exception as e:
        logger.error(f"Error performing haptic feedback: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["mobile_nav_agent"],
    description="Perform an alert action.",
    name="perform_alert_action",
)
def perform_alert_action(
    action: Annotated[str, "Action to perform on the alert (accept/dismiss)."],
    button_label: Annotated[str, "Label of the button to click."],
) -> Annotated[Dict[str, str], "Result of the alert action operation."]:
    """
    Perform an action on an alert dialog.
    """
    try:
        appium_manager = AppiumManager()
        appium_manager.perform_alert_action(action, button_label)
        return {"status": "success", "message": "Alert action performed successfully"}
    except Exception as e:
        logger.error(f"Error performing alert action: {str(e)}")
        return {"error": str(e)}
