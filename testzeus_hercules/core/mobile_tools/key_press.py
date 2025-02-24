from typing import Annotated, Dict
import time
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["mobile_nav_agent"],
    description="Press a key on the mobile device.",
    name="press_key",
)
def press_key(
    key_name: Annotated[str, "Name of the key to press."],
    wait_before_action: Annotated[
        float, "Time to wait before pressing (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the key press operation."]:
    """
    Press a key on the mobile device.
    """
    try:
        appium_manager = AppiumManager()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Perform the key press
        appium_manager.press_key(key_name)

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        return {
            "status": "success",
            "message": f"Pressed key: {key_name}",
        }

    except Exception as e:
        logger.error(f"Error in press_key: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["mobile_nav_agent"],
    description="Press a hardware key on the mobile device.",
    name="press_hardware_key",
)
def press_hardware_key(
    key_name: Annotated[str, "Name of the hardware key to press."],
    wait_before_action: Annotated[
        float, "Time to wait before pressing (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the hardware key press operation."]:
    """
    Press a hardware key on the mobile device.
    """
    try:
        appium_manager = AppiumManager()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Perform the hardware key press
        appium_manager.press_hardware_key(key_name)

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        return {
            "status": "success",
            "message": f"Pressed hardware key: {key_name}",
        }

    except Exception as e:
        logger.error(f"Error in press_hardware_key: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["mobile_nav_agent"],
    description="Send a sequence of key presses.",
    name="send_key_sequence",
)
def send_key_sequence(
    key_sequence: Annotated[str, "Sequence of keys to press."],
    wait_before_action: Annotated[
        float, "Time to wait before sending sequence (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the key sequence operation."]:
    """
    Send a sequence of key presses.
    """
    try:
        appium_manager = AppiumManager()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Split the sequence and press each key
        keys = key_sequence.split()
        for key in keys:
            appium_manager.press_key(key)
            time.sleep(0.1)  # Small delay between keys

        # Wait after sequence
        time.sleep(get_global_conf().get_delay_time())

        return {
            "status": "success",
            "message": f"Sent key sequence: {key_sequence}",
        }

    except Exception as e:
        logger.error(f"Error in send_key_sequence: {str(e)}")
        return {"error": str(e)}
