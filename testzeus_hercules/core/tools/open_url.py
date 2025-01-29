import inspect
from typing import Annotated

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger


@tool(agent_names=["browser_nav_agent"], description="""Opens specified URL in browser. Returns new page URL or error message. ALL TOOL ARGUMENTS ARE MANDATORY""", name="openurl")
async def openurl(
    url: Annotated[
        str,
        "URL to navigate to. Value must include the protocol (http:// or https://).",
    ],
    timeout: Annotated[int, "Additional wait time in seconds after initial load."] = 3,
) -> Annotated[str, "Returns the result of this request in text form"]:
    """
    Opens a specified URL in the active browser instance. Waits for an initial load event, then waits for either
    the 'domcontentloaded' event or a configurable timeout, whichever comes first.

    Parameters:
    - url: The URL to navigate to.
    - timeout: Additional time in seconds to wait after the initial load before considering the navigation successful.

    Returns:
    - URL of the new page.
    """
    logger.info(f"Opening URL: {url}")
    browser_manager = PlaywrightManager()
    await browser_manager.get_browser_context()
    page = await browser_manager.get_current_page()
    try:
        url = ensure_protocol(url)
        if page.url == url:
            logger.info(f"Current page URL is the same as the new URL: {url}. No need to refresh.")
            try:
                title = await page.title()
                return f"Page already loaded: {url}, Title: {title}"  # type: ignore
            except Exception as e:
                logger.error(f"An error occurred while getting the page title: {e}, but will continue to load the page.")

        # Navigate to the URL with a short timeout to ensure the initial load starts
        function_name = inspect.currentframe().f_code.co_name  # type: ignore

        await browser_manager.take_screenshots(f"{function_name}_start", page)

        await page.goto(url, timeout=timeout * 10000)  # type: ignore
        await browser_manager.take_screenshots(f"{function_name}_end", page)
        # Get the page title
        title = await page.title()
        url = page.url
        # wait for the network to idle
        # wait for the network to idle
        await page.wait_for_load_state()
        await browser_manager.wait_for_page_and_frames_load()
        return f"Page loaded: {url}, Title: {title}"  # type: ignore
    except PlaywrightTimeoutError as pte:
        logger.warning(f"Initial navigation to {url} failed: {pte}. Will try to continue anyway.")  # happens more often than not, but does not seem to be a problem
        return f"Timeout error opening URL: {url}"
    except Exception as e:
        logger.error(f"An error occurred while opening the URL: {url}. Error: {e}")
        import traceback

        traceback.print_exc()
        return f"Error opening URL: {url}"


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
        logger.info(f"Added 'https://' protocol to URL because it was missing. New URL is: {url}")
    return url
