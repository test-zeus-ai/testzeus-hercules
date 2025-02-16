"""
iOS-specific gesture implementations for TestZeus-Hercules.
"""

import asyncio
from typing import Optional, cast
from appium.webdriver.webdriver import WebDriver
from selenium.webdriver.common.action_chains import ActionChains
from testzeus_hercules.utils.logger import logger

async def perform_pinch(
    driver: WebDriver,
    scale: float = 0.5,
    velocity: float = 1.0,
    element_id: Optional[str] = None
) -> None:
    """
    Perform a pinch gesture (zoom in/out).
    
    Args:
        driver: WebDriver instance
        scale: Scale factor. Values > 1 zoom in, values < 1 zoom out
        velocity: Speed of the pinch gesture (default: 1.0)
        element_id: Optional element to perform the gesture on
    """
    args = {
        'scale': scale,
        'velocity': velocity
    }
    if element_id:
        args['element'] = element_id
        
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: driver.execute_script('mobile: pinch', args)
    )
    logger.info(f"Performed pinch with scale {scale} and velocity {velocity}")

async def perform_force_touch(
    driver: WebDriver,
    x: Optional[int] = None,
    y: Optional[int] = None,
    element_id: Optional[str] = None,
    pressure: float = 0.8,
    duration: float = 0.5
) -> None:
    """
    Perform a force touch (3D Touch) gesture.
    
    Args:
        driver: WebDriver instance
        x: X coordinate for the force touch (if not using element_id)
        y: Y coordinate for the force touch (if not using element_id)
        element_id: Optional element to perform force touch on
        pressure: Pressure level (0.0 to 1.0, default 0.8)
        duration: Duration of the force touch in seconds (default 0.5)
    """
    args = {
        'pressure': pressure,
        'duration': duration
    }
    
    if element_id:
        args['element'] = element_id
    elif x is not None and y is not None:
        args['x'] = x
        args['y'] = y
    else:
        raise ValueError("Either element_id or both x and y coordinates must be provided")
        
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: driver.execute_script('mobile: forceTouch', args)
    )
    logger.info(f"Performed force touch with pressure {pressure} and duration {duration}")

async def perform_double_tap(
    driver: WebDriver,
    x: Optional[int] = None,
    y: Optional[int] = None,
    element_id: Optional[str] = None,
    duration: float = 0.1
) -> None:
    """
    Perform a double tap gesture.
    
    Args:
        driver: WebDriver instance
        x: X coordinate for the double tap (if not using element_id)
        y: Y coordinate for the double tap (if not using element_id)
        element_id: Optional element to perform double tap on
        duration: Duration between taps in seconds (default 0.1)
    """
    args = {'duration': duration}
    if element_id:
        args['element'] = element_id
    elif x is not None and y is not None:
        args['x'] = x
        args['y'] = y
    else:
        raise ValueError("Either element_id or both x and y coordinates must be provided")
        
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: driver.execute_script('mobile: doubleTap', args)
    )
    logger.info("Performed double tap gesture")

async def perform_haptic(
    driver: WebDriver,
    type: str = "selection"
) -> None:
    """
    Trigger haptic feedback.
    
    Args:
        driver: WebDriver instance
        type: Type of haptic feedback ('selection', 'light', 'medium', 'heavy')
    """
    valid_types = ['selection', 'light', 'medium', 'heavy']
    if type not in valid_types:
        raise ValueError(f"Invalid haptic type. Must be one of: {valid_types}")
        
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: driver.execute_script('mobile: performIoHapticFeedback', {'type': type})
    )
    logger.info(f"Performed haptic feedback of type: {type}")

async def perform_alert_action(
    driver: WebDriver,
    action: str,
    button_label: Optional[str] = None
) -> None:
    """
    Handle iOS system alerts.
    
    Args:
        driver: WebDriver instance
        action: Action to perform ('accept', 'dismiss', 'getButtons', 'click')
        button_label: Button label text (required when action is 'click')
    """
    valid_actions = ['accept', 'dismiss', 'getButtons', 'click']
    if action not in valid_actions:
        raise ValueError(f"Invalid alert action. Must be one of: {valid_actions}")
        
    args = {'action': action}
    if action == 'click':
        if not button_label:
            raise ValueError("button_label is required when action is 'click'")
        args['button'] = button_label
        
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: driver.execute_script('mobile: alert', args)
    )
    logger.info(f"Performed alert action: {action}")
    return result