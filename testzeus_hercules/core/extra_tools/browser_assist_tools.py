from typing import Annotated, Dict, Optional

from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["browser_assist_nav_agent"],
    description="Take a screenshot of the current browser state.",
    name="take_browser_screenshot",
)
def take_browser_screenshot(
    screenshot_name: Annotated[str, "Name of the screenshot file."],
) -> Annotated[Dict[str, str], "Result of the screenshot operation."]:
    """
    Take a screenshot of the current browser state.
    """
    try:
        browser_manager = PlaywrightManager()
        screenshot_stream = browser_manager.get_latest_screenshot_stream()
        if not screenshot_stream:
            page = browser_manager.get_current_page()
            browser_manager.take_screenshots("browser_screenshot", page)
            screenshot_stream = browser_manager.get_latest_screenshot_stream()

        if screenshot_stream:
            return {
                "success": True,
                "message": f"Screenshot taken successfully: {screenshot_name}",
            }
        else:
            return {"error": "Failed to take screenshot"}
    except Exception as e:
        logger.error(f"Error taking screenshot: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["browser_assist_nav_agent"],
    description="Capture the current screen state.",
    name="capture_the_screen",
)
def capture_the_screen() -> Annotated[str, "Path to screenshot"]:
    """
    Capture the current screen state.
    """
    try:
        browser_manager = PlaywrightManager()
        screenshot_stream = browser_manager.get_latest_screenshot_stream()
        if not screenshot_stream:
            page = browser_manager.get_current_page()
            browser_manager.take_screenshots("browser_snapshot", page)
            screenshot_stream = browser_manager.get_latest_screenshot_stream()

        if screenshot_stream:
            return "Screenshot captured successfully"
        else:
            return "Failed to capture screenshot"
    except Exception as e:
        logger.error(f"Error capturing screen: {str(e)}")
        return f"Error capturing screen: {str(e)}"
