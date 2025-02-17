import asyncio
from typing import Annotated
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

@tool(
    agent_names=["navigation_nav_agent"],
    description="Scrolls screen up by one viewport height.",
    name="scroll_up"
)
async def scroll_up(
    wait_before_action: Annotated[float, "Wait time before action in seconds"] = 0.0,
) -> Annotated[str, "Scroll result"]:
    """
    Perform a scroll up action by one viewport height.
    
    Args:
        wait_before_action (float): Optional wait time before the action
        
    Returns:
        str: Message indicating success or failure of the scroll action
    """
    logger.info('Executing scroll up action')
    add_event(EventType.INTERACTION, EventData(detail="scroll_up"))

    # Initialize AppiumManager
    appium_manager = AppiumManager()
    
    if not appium_manager.driver:
        logger.error("No Appium session available for scroll action.")
        return "Error: No active Appium session. Please initialize a session first."

    # Optional wait before action
    if wait_before_action > 0:
        await asyncio.sleep(wait_before_action)

    try:
        # Perform scroll action - screenshots are handled by AppiumManager
        scroll_successful = await appium_manager.scroll_up()
        
        # Wait for any animations or state changes
        await asyncio.sleep(get_global_conf().get_delay_time())
        
        if not scroll_successful:
            msg = "Reached the top of the scrollable area - no further scrolling possible"
            logger.info(msg)
            return msg
            
        success_msg = (
            'Successfully performed scroll up action. New content has been brought into view '
            'and any scroll animations have completed.'
        )
        logger.info(success_msg)
        return success_msg

    except Exception as e:
        error_msg = (
            'Failed to perform scroll up action. This might indicate no scrollable container '
            f'is present or there may be a connection issue. Error: {str(e)}'
        )
        logger.error(error_msg)
        return f"Error: {error_msg}"

@tool(
    agent_names=["navigation_nav_agent"], 
    description="Scrolls screen down by one viewport height.",
    name="scroll_down"
)
async def scroll_down(
    wait_before_action: Annotated[float, "Wait time before action in seconds"] = 0.0,
) -> Annotated[str, "Scroll result"]:
    """
    Perform a scroll down action by one viewport height.
    
    Args:
        wait_before_action (float): Optional wait time before the action
        
    Returns:
        str: Message indicating success or failure of the scroll action
    """
    logger.info('Executing scroll down action')
    add_event(EventType.INTERACTION, EventData(detail="scroll_down"))

    # Initialize AppiumManager
    appium_manager = AppiumManager()
    
    if not appium_manager.driver:
        logger.error("No Appium session available for scroll action.")
        return "Error: No active Appium session. Please initialize a session first."

    # Optional wait before action
    if wait_before_action > 0:
        await asyncio.sleep(wait_before_action)

    try:
        # Perform scroll action - screenshots are handled by AppiumManager
        scroll_successful = await appium_manager.scroll_down()
        
        # Wait for any animations or state changes
        await asyncio.sleep(get_global_conf().get_delay_time())
        
        if not scroll_successful:
            msg = "Reached the bottom of the scrollable area - no further scrolling possible"
            logger.info(msg)
            return msg
            
        success_msg = (
            'Successfully performed scroll down action. New content has been brought into view '
            'and any scroll animations have completed.'
        )
        logger.info(success_msg)
        return success_msg

    except Exception as e:
        error_msg = (
            'Failed to perform scroll down action. This might indicate no scrollable container '
            f'is present or there may be a connection issue. Error: {str(e)}'
        )
        logger.error(error_msg)
        return f"Error: {error_msg}"