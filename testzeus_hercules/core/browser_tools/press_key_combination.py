from typing import Annotated, Dict
import time
from playwright.sync_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["keyboard_nav_agent"],
    description="Press a key combination in the browser.",
    name="press_key_combination",
)
def press_key_combination(
    key_combination: Annotated[str, "Key combination to press (e.g., Control+A)."],
    wait_before_action: Annotated[
        float, "Time to wait before pressing (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the key combination press operation."]:
    """
    Press a key combination in the browser.
    """
    try:
        browser_manager = PlaywrightManager()
        page = browser_manager.get_current_page()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Press the key combination
        success = do_press_key_combination(browser_manager, page, key_combination)

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        if success:
            return {
                "status": "success",
                "message": f"Pressed key combination: {key_combination}",
            }
        else:
            return {
                "error": f"Failed to press key combination: {key_combination}",
            }

    except Exception as e:
        logger.error(f"Error in press_key_combination: {str(e)}")
        return {"error": str(e)}


def do_press_key_combination(
    browser_manager: PlaywrightManager, page: Page, key_combination: str
) -> bool:
    """
    Helper function to perform the actual key combination press.
    """
    try:
        # Take screenshot before action
        browser_manager.take_screenshots("press_key_combination_start", page)

        # Split the combination into individual keys
        keys = key_combination.split("+")

        # Press all keys in the combination
        for key in keys[:-1]:
            page.keyboard.down(key)

        # Press and release the last key
        page.keyboard.press(keys[-1])

        # Release all held keys in reverse order
        for key in reversed(keys[:-1]):
            page.keyboard.up(key)

        # Take screenshot after action
        browser_manager.take_screenshots("press_key_combination_end", page)

        return True

    except Exception as e:
        logger.error(f"Error in do_press_key_combination: {str(e)}")
        return False
