import asyncio
from typing import Annotated, cast
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.remote.webdriver import WebDriver
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

@tool(
    agent_names=["navigation_nav_agent"],
    description="Performs long press on element identified by identifier.",
    name="long_press_by_id"
)
async def long_press_by_id(
    duration: Annotated[int, "Duration of long press in milliseconds"] = 1000,
    resource_id: Annotated[str, "Resource ID of the element (Android: resource-id, iOS: name)"] = None,
    accessibility_id: Annotated[str, "Accessibility ID of the element (Android: content-desc, iOS: accessibilityIdentifier)"] = None,
    bounds: Annotated[str, "Element bounds in format '[x1,y1][x2,y2]'"] = None,
    wait_before_action: Annotated[float, "Wait time before action in seconds"] = 0.0,
) -> Annotated[str, "Long press result"]:
    """
    Perform a long press on an element identified by its resource ID, accessibility ID, or bounds.
    
    Args:
        duration (int): Duration to hold the press in milliseconds (default: 1000)
        resource_id (str, optional): Resource ID of the element (Android: resource-id, iOS: name)
        accessibility_id (str, optional): Accessibility ID of the element (Android: content-desc, iOS: accessibilityIdentifier)
        bounds (str, optional): Element bounds in format '[x1,y1][x2,y2]'
        wait_before_action (float): Optional wait time before the action
        
    Raises:
        RuntimeError: If no identifier is provided (resource_id, accessibility_id, or bounds)
        RuntimeError: If no active Appium session
        Exception: If element cannot be found or long press cannot be performed
        
    Returns:
        str: Message indicating success of the long press action
    """
    if not resource_id and not accessibility_id and not bounds:
        raise RuntimeError("At least one of resource_id, accessibility_id, or bounds must be provided")

    logger.info(
        "Executing long_press_by_id with " +
        (f'resource_id: "{resource_id}", ' if resource_id else "") +
        (f'accessibility_id: "{accessibility_id}", ' if accessibility_id else "") +
        (f'bounds: "{bounds}", ' if bounds else "") +
        f"duration: {duration}ms"
    )
    add_event(EventType.INTERACTION, EventData(detail="long_press_by_id"))

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

    # Perform long press using ActionChains
    loop = asyncio.get_running_loop()
    driver = cast(WebDriver, appium_manager.driver)
    
    def perform_long_press():
        action = ActionChains(driver)
        # Move to element and perform long press
        action.move_to_element(element)
        action.click_and_hold()
        action.pause(duration / 1000.0)  # Convert ms to seconds
        action.release()
        return action.perform()

    await loop.run_in_executor(None, perform_long_press)
    
    # Wait for any animations or state changes
    await asyncio.sleep(get_global_conf().get_delay_time())
    
    success_msg = (
        "Successfully performed long press " +
        (f'on element with Resource ID: "{resource_id}", ' if resource_id else "") +
        (f'Accessibility ID: "{accessibility_id}", ' if accessibility_id else "") +
        (f'Bounds: "{bounds}", ' if bounds else "") +
        f"for {duration}ms. Any context menus or responses to the long press have been processed."
    )
    logger.info(success_msg)
    return success_msg