import asyncio
from typing import Annotated
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

@tool(
    agent_names=["navigation_nav_agent"],
    description="Performs tap action at specified coordinates.",
    name="tap"
)
async def tap(
    x: Annotated[int, "X coordinate for tap"],
    y: Annotated[int, "Y coordinate for tap"],
    wait_before_action: Annotated[float, "Wait time before action in seconds"] = 0.0,
) -> Annotated[str, "Tap result"]:
    """
    Perform a tap action at the specified coordinates.
    
    Args:
        x (int): X coordinate for the tap action
        y (int): Y coordinate for the tap action
        wait_before_action (float): Optional wait time before the action
        
    Returns:
        str: Message indicating success or failure of the tap action
    """
    logger.info(f'Executing tap at coordinates: ({x}, {y})')
    add_event(EventType.INTERACTION, EventData(detail="tap"))

    # Initialize AppiumManager
    appium_manager = AppiumManager()
    
    if not appium_manager.driver:
        logger.error("No Appium session available for tap action.")
        return "Error: No active Appium session. Please initialize a session first."

    # Validate coordinates
    viewport = await appium_manager.get_viewport_size()
    if viewport:
        if x < 0 or x > viewport['width'] or y < 0 or y > viewport['height']:
            error_msg = (
                f"Invalid coordinates ({x}, {y}). Must be within viewport dimensions: "
                f"width={viewport['width']}, height={viewport['height']}"
            )
            logger.error(error_msg)
            return f"Error: {error_msg}"

    # Optional wait before action
    if wait_before_action > 0:
        await asyncio.sleep(wait_before_action)

    try:
        # Perform tap action - screenshots are handled by AppiumManager
        await appium_manager.perform_tap(x, y)
        
        # Wait for any animations or state changes
        await asyncio.sleep(get_global_conf().get_delay_time())
        
        success_msg = (
            f'Successfully performed tap at coordinates: ({x}, {y}). '
            f'Any touch responses or UI changes have been processed.'
        )
        logger.info(success_msg)
        return success_msg

    except Exception as e:
        error_msg = (
            f'Failed to perform tap at coordinates: ({x}, {y}). '
            f'This might indicate invalid coordinates or a connection issue. Error: {str(e)}'
        )
        logger.error(error_msg)
        return f"Error: {error_msg}"