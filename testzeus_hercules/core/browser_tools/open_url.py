import inspect
from typing import Annotated

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["navigation_nav_agent"],
    description="Opens specified URL in browser. Returns new page URL or error message.",
    name="openurl",
)
def openurl(
    url: Annotated[
        str,
        "URL to navigate to. Value must include the protocol (http:// or https://).",
    ],
    timeout: Annotated[int, "Additional wait time in seconds after initial load."] = 3,
) -> Annotated[str, "Returns the result of this request in text form"]:
    """
    Open a URL in the browser.

    Args:
        url: The URL to navigate to (must include protocol)
        timeout: Additional wait time in seconds after initial load

    Returns:
        str: Result message indicating success or failure
    """
    try:
        logger.info(f"Opening URL: {url}")
        browser_manager = PlaywrightManager()
        page = browser_manager.get_current_page()

        # Navigate to URL
        page.goto(url)

        # Wait for network idle
        page.wait_for_load_state("networkidle")

        # Additional wait if specified
        if timeout > 0:
            page.wait_for_timeout(timeout * 1000)  # Convert to milliseconds

        return f"Successfully opened URL: {url}"

    except PlaywrightTimeoutError as e:
        error_msg = f"Timeout while opening URL {url}: {str(e)}"
        logger.error(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"Error opening URL {url}: {str(e)}"
        logger.error(error_msg)
        return error_msg


def ensure_protocol(url: str) -> str:
    """
    Ensures that a URL has a protocol (http:// or https://). If it doesn't have one,
    https:// is added by default.

    Parameters:
    - url: The URL to check and modify if necessary.

    Returns:
    - A URL string with a protocol.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url  # Default to http if no protocol is specified
        logger.info(
            f"Added 'https://' protocol to URL because it was missing. New URL is: {url}"
        )
    return url
