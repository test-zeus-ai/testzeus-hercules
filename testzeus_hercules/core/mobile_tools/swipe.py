import asyncio
from typing import Annotated
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

@tool(
    agent_names=["navigation_nav_agent"],
    description="Performs swipe gesture from one point to another.",
    name="swipe"
)
async def swipe(
    start_x: Annotated[int, "Starting X coordinate"],
    start_y: Annotated[int, "Starting Y coordinate"],
    end_x: Annotated[int, "Ending X coordinate"], 
    end_y: Annotated[int, "Ending Y coordinate"],
    duration: Annotated[int, "Duration of swipe in milliseconds"] = 800,
    wait_before_action: Annotated[float, "Wait time before action in seconds"] = 0.0,
) -> Annotated[str, "Swipe result"]:
    """
    Perform a swipe gesture from one point to another.
    
    Args:
        start_x (int): Starting X coordinate
        start_y (int): Starting Y coordinate
        end_x (int): Ending X coordinate
        end_y (int): Ending Y coordinate
        duration (int): Duration of the swipe in milliseconds (default: 800)
        wait_before_action (float): Optional wait time before the action
        
    Returns:
        str: Message indicating success or failure of the swipe action
    """
    logger.info(
        f'Executing swipe from ({start_x}, {start_y}) to ({end_x}, {end_y}) '
        f'with duration {duration}ms'
    )
    add_event(EventType.INTERACTION, EventData(detail="swipe"))

    # Initialize AppiumManager
    appium_manager = AppiumManager()
    
    if not appium_manager.driver:
        logger.error("No Appium session available for swipe action.")
        return "Error: No active Appium session. Please initialize a session first."

    # Validate coordinates
    viewport = await appium_manager.get_viewport_size()
    if viewport:
        for name, coord in [
            ("start_x", start_x), 
            ("start_y", start_y),
            ("end_x", end_x),
            ("end_y", end_y)
        ]:
            if (
                (name.endswith('x') and (coord < 0 or coord > viewport['width'])) or
                (name.endswith('y') and (coord < 0 or coord > viewport['height']))
            ):
                error_msg = (
                    f"Invalid {name} coordinate: {coord}. Must be within viewport dimensions: "
                    f"width={viewport['width']}, height={viewport['height']}"
                )
                logger.error(error_msg)
                return f"Error: {error_msg}"

    # Optional wait before action
    if wait_before_action > 0:
        await asyncio.sleep(wait_before_action)

    try:
        # Perform swipe action - screenshots are handled by AppiumManager
        await appium_manager.perform_swipe(start_x, start_y, end_x, end_y, duration)
        
        # Wait for any animations or state changes
        await asyncio.sleep(get_global_conf().get_delay_time())
        
        direction = ""
        if abs(end_x - start_x) > abs(end_y - start_y):
            direction = "horizontal" if end_x > start_x else "horizontal reverse"
        else:
            direction = "vertical" if end_y > start_y else "vertical reverse"
            
        success_msg = (
            f'Successfully performed {direction} swipe from ({start_x}, {start_y}) to ({end_x}, {end_y}) '
            f'over {duration}ms. Any scrolling, animations, or UI updates have been processed.'
        )
        logger.info(success_msg)
        return success_msg

    except Exception as e:
        error_msg = (
            f'Failed to perform swipe from ({start_x}, {start_y}) to ({end_x}, {end_y}). '
            f'This might indicate invalid coordinates or a connection issue. Error: {str(e)}'
        )
        logger.error(error_msg)
        return f"Error: {error_msg}"