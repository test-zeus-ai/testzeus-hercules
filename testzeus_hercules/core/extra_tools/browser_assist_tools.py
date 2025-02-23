import os
import time
from typing import Annotated, Dict, Union

from PIL import Image
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["browser_nav_agent"],
    name="take_browser_screenshot",
    description="Take a screenshot of the current browser view and save it",
)
async def take_browser_screenshot(
    screenshot_title: Annotated[str, "Title/description for this screenshot"],
) -> Union[str, Dict[str, str]]:
    """
    Take and save a screenshot of the current browser view.

    Args:
        screenshot_title: Title or description for this screenshot

    Returns:
        str: Path to saved screenshot
        dict: Error message if something fails
    """
    try:
        # Get current screenshot
        browser_manager = PlaywrightManager()
        screenshot_stream = await browser_manager.get_latest_screenshot_stream()
        if not screenshot_stream:
            page = await browser_manager.get_current_page()
            await browser_manager.take_screenshots("browser_screenshot", page)
            screenshot_stream = await browser_manager.get_latest_screenshot_stream()

        if not screenshot_stream:
            return {"error": "Failed to capture current browser view"}

        # Get the proof path for storing screenshots
        proof_path = get_global_conf().get_proof_path() or "."
        screenshots_dir = os.path.join(proof_path, "browser_screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)

        # Create a timestamped filename
        timestamp = int(time.time())
        base_filename = (
            screenshot_title.replace(" ", "_")
            .replace("/", "_")
            .replace(":", "_")
            .lower()
            + f"_{timestamp}"
        )
        screenshot_file = os.path.join(screenshots_dir, f"{base_filename}.png")

        # Save the screenshot
        screenshot = Image.open(screenshot_stream)
        screenshot.save(screenshot_file)

        logger.info(f"Screenshot saved to: {screenshot_file}")
        return f"Screenshot saved successfully to: {screenshot_file}"

    except Exception as e:
        logger.exception(f"Error taking screenshot: {e}")
        return {"error": str(e)}


@tool(
    agent_names=["browser_nav_agent"],
    name="capture_the_screen",
    description="give you the current screenshot of the browser view",
)
async def capture_the_screen() -> Annotated[str, "Path to of screenshot"]:
    """
    Take and save a snapshot of the current browser view, overwriting previous snapshot.

    Returns:
        str: Path to saved screenshot
        dict: Error message if something fails
    """
    try:

        # Get current screenshot
        browser_manager = PlaywrightManager()
        screenshot_stream = await browser_manager.get_latest_screenshot_stream()
        if not screenshot_stream:
            page = await browser_manager.get_current_page()
            await browser_manager.take_screenshots("browser_snapshot", page)
            screenshot_stream = await browser_manager.get_latest_screenshot_stream()

        if not screenshot_stream:
            return {"error": "Failed to capture current browser view"}

        # Use log_files directory
        screenshots_dir = os.path.join("log_files")
        os.makedirs(screenshots_dir, exist_ok=True)

        # Fixed filename that will be overwritten each time
        screenshot_file = os.path.join(screenshots_dir, "current_page.png")

        # Save the screenshot, overwriting if exists
        screenshot = Image.open(screenshot_stream)
        screenshot.save(screenshot_file)

        logger.info(f"Page snapshot saved to: {screenshot_file}")
        return screenshot_file

    except Exception as e:
        logger.exception(f"Error taking snapshot: {e}")
        return {"error": str(e)}
