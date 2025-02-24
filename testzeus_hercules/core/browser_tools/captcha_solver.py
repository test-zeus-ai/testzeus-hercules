from typing import Annotated, Dict
import time
from playwright.sync_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["captcha_nav_agent"],
    description="Solve a CAPTCHA on the page.",
    name="captcha_solver",
)
def captcha_solver(
    selector: Annotated[str, "CSS selector for the CAPTCHA element."],
    wait_before_action: Annotated[
        float, "Time to wait before solving (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the CAPTCHA solving operation."]:
    """
    Solve a CAPTCHA on the page.
    """
    try:
        browser_manager = PlaywrightManager()
        page = browser_manager.get_current_page()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Solve the CAPTCHA
        result = do_solve_captcha(page, selector)

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        return result

    except Exception as e:
        logger.error(f"Error in captcha_solver: {str(e)}")
        return {"error": str(e)}


def do_solve_captcha(page: Page, selector: str) -> Dict[str, str]:
    """
    Helper function to solve the CAPTCHA.
    """
    try:
        browser_manager = PlaywrightManager()

        # Find the CAPTCHA element
        captcha = browser_manager.find_element(selector, page)
        if not captcha:
            return {"error": f"CAPTCHA element not found with selector: {selector}"}

        # TODO: Implement actual CAPTCHA solving logic here
        # This is a placeholder that just clicks the CAPTCHA element
        captcha.click()

        return {
            "status": "success",
            "message": "CAPTCHA solving attempted",
        }

    except Exception as e:
        logger.error(f"Error in do_solve_captcha: {str(e)}")
        return {"error": str(e)}
