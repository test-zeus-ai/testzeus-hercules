import asyncio
from typing import Annotated
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

@tool(
    agent_names=["navigation_nav_agent"],
    description="Press a keyboard key like Enter, Tab, Space, etc.",
    name="press_key"
)
async def press_key(
    key_name: Annotated[str, "Name of the key to press (e.g., Enter, Tab, Space)"],
    wait_before_action: Annotated[float, "Wait time before action in seconds"] = 0.0,
) -> Annotated[str, "Key press result"]:
    """
    Press a keyboard key by name.
    
    Args:
        key_name (str): Name of the key to press (Enter, Tab, Space, etc.)
        wait_before_action (float): Optional wait time before the action
        
    Returns:
        str: Message indicating success or failure of the key press
    """
    logger.info(f'Executing press_key with key: "{key_name}"')
    add_event(EventType.INTERACTION, EventData(detail="press_key"))

    # Initialize AppiumManager
    appium_manager = AppiumManager()
    
    if not appium_manager.driver:
        raise RuntimeError("No active Appium session. Please initialize a session first.")

    # Optional wait before action
    if wait_before_action > 0:
        await asyncio.sleep(wait_before_action)

    # Perform key press action
    await appium_manager.press_key(key_name)
    
    # Wait for any animations or state changes
    await asyncio.sleep(get_global_conf().get_delay_time())
    
    success_msg = f'Successfully pressed key: "{key_name}". Any UI updates have been processed.'
    logger.info(success_msg)
    return success_msg

@tool(
    agent_names=["navigation_nav_agent"],
    description="Press a hardware key like volume up/down, power, etc.",
    name="press_hardware_key"
)
async def press_hardware_key(
    key_name: Annotated[str, "Name of hardware key (volume_up, volume_down, power, etc.)"],
    wait_before_action: Annotated[float, "Wait time before action in seconds"] = 0.0,
) -> Annotated[str, "Hardware key press result"]:
    """
    Press a hardware key by name.
    
    Args:
        key_name (str): Name of hardware key (volume_up, volume_down, power, etc.)
        wait_before_action (float): Optional wait time before the action
        
    Returns:
        str: Message indicating success or failure of the hardware key press
    """
    logger.info(f'Executing press_hardware_key with key: "{key_name}"')
    add_event(EventType.INTERACTION, EventData(detail="press_hardware_key"))

    # Initialize AppiumManager
    appium_manager = AppiumManager()
    
    if not appium_manager.driver:
        raise RuntimeError("No active Appium session. Please initialize a session first.")

    # Optional wait before action
    if wait_before_action > 0:
        await asyncio.sleep(wait_before_action)

    # Perform hardware key press action
    await appium_manager.press_hardware_key(key_name)
    
    # Wait for any animations or state changes
    await asyncio.sleep(get_global_conf().get_delay_time())
    
    success_msg = f'Successfully pressed hardware key: "{key_name}". Any system responses have been processed.'
    logger.info(success_msg)
    return success_msg

@tool(
    agent_names=["navigation_nav_agent"],
    description="Send a sequence of key combinations (e.g., 'Control+A,Control+C' for select all and copy) only from keygboard, not for text fields.",
    name="send_key_sequence"
)
async def send_key_sequence(
    key_sequence: Annotated[str, "Sequence of keys or key combinations separated by comma (e.g., 'Control+A,Control+C')"],
    wait_between_keys: Annotated[float, "Wait time between each key combination in seconds"] = 0.1,
    wait_before_action: Annotated[float, "Wait time before starting the sequence in seconds"] = 0.0,
) -> Annotated[str, "Key sequence result"]:
    """
    Send a sequence of key combinations.
    
    Args:
        key_sequence (str): Comma-separated sequence of key combinations (e.g., "Control+A,Control+C")
        wait_between_keys (float): Time to wait between key combinations in seconds
        wait_before_action (float): Optional wait time before starting the sequence
        
    Returns:
        str: Message indicating success or failure of the key sequence
    """
    logger.info(f'Executing send_key_sequence with sequence: "{key_sequence}"')
    add_event(EventType.INTERACTION, EventData(detail="send_key_sequence"))

    # Initialize AppiumManager
    appium_manager = AppiumManager()
    
    if not appium_manager.driver:
        raise RuntimeError("No active Appium session. Please initialize a session first.")

    # Optional wait before action
    if wait_before_action > 0:
        await asyncio.sleep(wait_before_action)

    # Split the sequence into individual key combinations
    combinations = [combo.strip() for combo in key_sequence.split(",")]
    
    for i, combination in enumerate(combinations):
        # Process each key combination
        try:
            # For each combination (e.g., "Control+A"), press all keys
            keys = combination.split("+")
            
            # Press each key in the combination
            await appium_manager.press_key_combination(combination)
            
            # If this isn't the last combination, wait the specified time
            if i < len(combinations) - 1:
                await asyncio.sleep(wait_between_keys)
                
        except Exception as e:
            error_msg = f'Failed to execute key combination "{combination}": {str(e)}'
            logger.error(error_msg)
            return error_msg
    
    # Wait for any animations or state changes
    await asyncio.sleep(get_global_conf().get_delay_time())
    
    success_msg = f'Successfully executed key sequence: "{key_sequence}". Any UI updates have been processed.'
    logger.info(success_msg)
    return success_msg