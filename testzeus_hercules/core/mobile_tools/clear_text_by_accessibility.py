import asyncio
from typing import Annotated
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

@tool(
    agent_names=["navigation_nav_agent"],
    description="Clears text from element identified by identifier.",
    name="clear_text_by_id"
)
async def clear_text_by_id(
    resource_id: Annotated[str, "Resource ID of the element (Android: resource-id, iOS: name)"] = None,
    accessibility_id: Annotated[str, "Accessibility ID of the element (Android: content-desc, iOS: accessibilityIdentifier)"] = None,
    bounds: Annotated[str, "Element bounds in format '[x1,y1][x2,y2]'"] = None,
    wait_before_action: Annotated[float, "Wait time before action in seconds"] = 0.0,
) -> Annotated[str, "Clear text result"]:
    """
    Clear text from an element identified by its resource ID, accessibility ID, or bounds.
    
    Args:
        resource_id (str, optional): Resource ID of the element (Android: resource-id, iOS: name)
        accessibility_id (str, optional): Accessibility ID of the element (Android: content-desc, iOS: accessibilityIdentifier)
        bounds (str, optional): Element bounds in format '[x1,y1][x2,y2]'
        wait_before_action (float): Optional wait time before the action
        
    Raises:
        RuntimeError: If no identifier is provided (resource_id, accessibility_id, or bounds)
        RuntimeError: If no active Appium session
        Exception: If element cannot be found or cleared
        
    Returns:
        str: Message indicating success of the clear operation
    """
    if not resource_id and not accessibility_id and not bounds:
        raise RuntimeError("At least one of resource_id, accessibility_id, or bounds must be provided")
        
    logger.info(
        "Executing clear_text_by_id with " +
        (f'resource_id: "{resource_id}", ' if resource_id else "") +
        (f'accessibility_id: "{accessibility_id}", ' if accessibility_id else "") +
        (f'bounds: "{bounds}"' if bounds else "")
    )
    add_event(EventType.INTERACTION, EventData(detail="clear_text_by_id"))

    # Initialize AppiumManager
    appium_manager = AppiumManager()
    
    if not appium_manager.driver:
        raise RuntimeError("No active Appium session. Please initialize a session first.")

    # Optional wait before action
    if wait_before_action > 0:
        await asyncio.sleep(wait_before_action)

    # Find element using enhanced find_element_best_match
    element = await appium_manager.find_element_best_match(
        res_id=resource_id,
        accessibility_id=accessibility_id,
        bounds=bounds
    )
    
    # Clear text from the found element
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, element.clear)
    
    # Wait for any animations or state changes
    await asyncio.sleep(get_global_conf().get_delay_time())
    
    success_msg = (
        "Successfully cleared text from element. " +
        (f'Resource ID: "{resource_id}", ' if resource_id else "") +
        (f'Accessibility ID: "{accessibility_id}", ' if accessibility_id else "") +
        (f'Bounds: "{bounds}", ' if bounds else "") +
        "The text field has been emptied and any UI updates have been processed."
    )
    logger.info(success_msg)
    return success_msg