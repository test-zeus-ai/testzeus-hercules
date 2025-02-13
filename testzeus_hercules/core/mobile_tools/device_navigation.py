import asyncio
from typing import Annotated
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

@tool(
    agent_names=["navigation_nav_agent"],
    description="Press the back button (Android) or perform back gesture (iOS).",
    name="press_back_button"
)
async def press_back_button(
    wait_before_action: Annotated[float, "Wait time before action in seconds"] = 0.0,
) -> Annotated[str, "Back button press result"]:
    """
    Press the back button or perform back navigation.
    
    Args:
        wait_before_action (float): Optional wait time before the action
        
    Returns:
        str: Message indicating success or failure of the back action
    """
    logger.info('Executing press_back_button')
    add_event(EventType.INTERACTION, EventData(detail="press_back_button"))

    # Initialize AppiumManager
    appium_manager = AppiumManager()
    
    if not appium_manager.driver:
        raise RuntimeError("No active Appium session. Please initialize a session first.")

    # Optional wait before action
    if wait_before_action > 0:
        await asyncio.sleep(wait_before_action)

    # Perform back action
    await appium_manager.press_back()
    
    # Wait for any animations or state changes
    await asyncio.sleep(get_global_conf().get_delay_time())
    
    success_msg = 'Successfully performed back action. Any navigation or UI changes have been processed.'
    logger.info(success_msg)
    return success_msg

@tool(
    agent_names=["navigation_nav_agent"],
    description="Press the home button or perform home action.",
    name="press_home_button"
)
async def press_home_button(
    wait_before_action: Annotated[float, "Wait time before action in seconds"] = 0.0,
) -> Annotated[str, "Home button press result"]:
    """
    Press the home button or perform home action.
    
    Args:
        wait_before_action (float): Optional wait time before the action
        
    Returns:
        str: Message indicating success or failure of the home action
    """
    logger.info('Executing press_home_button')
    add_event(EventType.INTERACTION, EventData(detail="press_home_button"))

    # Initialize AppiumManager
    appium_manager = AppiumManager()
    
    if not appium_manager.driver:
        raise RuntimeError("No active Appium session. Please initialize a session first.")

    # Optional wait before action
    if wait_before_action > 0:
        await asyncio.sleep(wait_before_action)

    # Perform home button action
    await appium_manager.press_home()
    
    # Wait for any animations or state changes
    await asyncio.sleep(get_global_conf().get_delay_time())
    
    success_msg = 'Successfully performed home action. Application has been minimized and home screen is visible.'
    logger.info(success_msg)
    return success_msg

@tool(
    agent_names=["navigation_nav_agent"],
    description="Open app switcher/recent apps view.",
    name="press_app_switch_button"
)
async def press_app_switch_button(
    wait_before_action: Annotated[float, "Wait time before action in seconds"] = 0.0,
) -> Annotated[str, "App switch button press result"]:
    """
    Press the app switcher button or perform app switch gesture.
    
    Args:
        wait_before_action (float): Optional wait time before the action
        
    Returns:
        str: Message indicating success or failure of the app switch action
    """
    logger.info('Executing press_app_switch_button')
    add_event(EventType.INTERACTION, EventData(detail="press_app_switch_button"))

    # Initialize AppiumManager
    appium_manager = AppiumManager()
    
    if not appium_manager.driver:
        raise RuntimeError("No active Appium session. Please initialize a session first.")

    # Optional wait before action
    if wait_before_action > 0:
        await asyncio.sleep(wait_before_action)

    # Perform app switch action
    await appium_manager.press_app_switch()
    
    # Wait for any animations or state changes
    await asyncio.sleep(get_global_conf().get_delay_time())
    
    success_msg = 'Successfully opened app switcher. Recent apps view is now visible.'
    logger.info(success_msg)
    return success_msg