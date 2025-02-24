from typing import Annotated, Dict

from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["url_nav_agent"],
    description="Get the current URL of the active page.",
    name="geturl",
)
def geturl() -> Annotated[Dict[str, str], "Current URL and page title."]:
    """
    Get the current URL and title of the active page.
    """
    try:
        browser_manager = PlaywrightManager()
        page = browser_manager.get_current_page()

        # Wait for the page to be ready
        page.wait_for_load_state()
        page.wait_for_load_state("domcontentloaded")

        # Get URL and title
        url = page.url
        title = page.title()

        return {
            "success": True,
            "url": url,
            "title": title,
        }
    except Exception as e:
        logger.error(f"Error getting URL: {str(e)}")
        return {"error": str(e)}
