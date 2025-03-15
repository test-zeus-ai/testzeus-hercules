import inspect
from typing import Annotated
import asyncio
import traceback
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.browser_logger import get_browser_logger
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["browser_nav_agent"],
    description="""Opens specified URL in browser. Returns new page URL or error message.""",
    name="openurl",
)
async def openurl(
    url: Annotated[
        str,
        "URL to navigate to. Value must include the protocol (http:// or https://).",
    ],
    timeout: Annotated[int, "Additional wait time in seconds after initial load."] = 3,
    force_new_tab: Annotated[
        bool, "Force opening in a new tab instead of reusing existing ones."
    ] = False,
) -> Annotated[str, "Returns the result of this request in text form"]:
    logger.info(f"Opening URL: {url} (force_new_tab={force_new_tab})")
    browser_manager = PlaywrightManager()
    await browser_manager.get_browser_context()

    # Use the new reuse_or_create_tab method to get a page
    page = await browser_manager.reuse_or_create_tab(force_new_tab=force_new_tab)
    logger.info(
        f"{'Using new tab' if force_new_tab else 'Reusing existing tab when possible'} for navigation to {url}"
    )

    # Initialize browser logger
    browser_logger = get_browser_logger(get_global_conf().get_proof_path())

    try:
        # Special handling for browser-specific URLs that need special treatment
        special_browser_urls = [
            "about:blank",
            "about:newtab",
            "chrome://newtab/",
            "edge://newtab/",
        ]
        if url.strip().lower() in special_browser_urls:
            special_url = url.strip().lower()
            logger.info(f"Navigating to special browser URL: {special_url}")

            try:
                # Handle these special URLs with JavaScript navigation instead of goto
                await page.evaluate(f"window.location.href = '{special_url}'")
                await page.wait_for_load_state("domcontentloaded")
            except Exception as e:

                traceback.print_exc()
                logger.warning(
                    f"JavaScript navigation to {special_url} failed: {e}. Trying alternative method."
                )
                # Fallback method: For about: URLs, try direct goto without adding protocol
                try:
                    if special_url.startswith("about:"):
                        await page.goto(special_url, timeout=timeout * 1000)
                    else:
                        # For chrome:// and other browser URLs, try setting empty content first
                        await page.set_content("<html><body></body></html>")
                        await page.evaluate(f"window.location.href = '{special_url}'")
                except Exception as fallback_err:

                    traceback.print_exc()
                    logger.error(
                        f"All navigation methods to {special_url} failed: {fallback_err}"
                    )
                    # Continue anyway - we'll try to get the title

            title = await page.title()

            # Log successful navigation
            await browser_logger.log_browser_interaction(
                tool_name="openurl",
                action="navigate",
                interaction_type="navigation",
                success=True,
                additional_data={
                    "url": special_url,
                    "title": title,
                    "from_cache": False,
                    "status": "loaded",
                    "force_new_tab": force_new_tab,
                },
            )

            return f"Navigated to {special_url}, Title: {title}"

        url = ensure_protocol(url)
        if page.url == url:
            logger.info(
                f"Current page URL is the same as the new URL: {url}. No need to refresh."
            )
            try:
                title = await page.title()
                # Log successful navigation (from cache)
                await browser_logger.log_browser_interaction(
                    tool_name="openurl",
                    action="navigate",
                    interaction_type="navigation",
                    success=True,
                    additional_data={
                        "url": url,
                        "title": title,
                        "from_cache": True,
                        "status": "already_loaded",
                        "force_new_tab": force_new_tab,
                    },
                )
                return f"Page already loaded: {url}, Title: {title}"  # type: ignore
            except Exception as e:

                traceback.print_exc()
                logger.error(
                    f"An error occurred while getting the page title: {e}, but will continue to load the page."
                )

        # Navigate to the URL with a short timeout to ensure the initial load starts
        function_name = inspect.currentframe().f_code.co_name  # type: ignore

        await browser_manager.take_screenshots(f"{function_name}_start", page)

        response = await page.goto(url, timeout=timeout * 10000)  # type: ignore
        await browser_manager.take_screenshots(f"{function_name}_end", page)

        # Get navigation details
        title = await page.title()
        final_url = page.url
        status = response.status if response else None
        ok = response.ok if response else False

        # Wait for the page to load
        try:
            await browser_manager.wait_for_load_state_if_enabled(
                page=page, state="domcontentloaded"
            )

            # Additional wait time if specified
            if timeout > 0:
                await asyncio.sleep(timeout)
        except Exception as e:

            traceback.print_exc()
            logger.error(f"An error occurred while waiting for the page to load: {e}")

        # Wait for the network to idle
        await browser_manager.wait_for_page_and_frames_load()

        # Log successful navigation
        await browser_logger.log_browser_interaction(
            tool_name="openurl",
            action="navigate",
            interaction_type="navigation",
            success=True,
            additional_data={
                "url": url,
                "final_url": final_url,
                "title": title,
                "status_code": status,
                "ok": ok,
                "from_cache": False,
                "force_new_tab": force_new_tab,
            },
        )

        return f"Page loaded: {final_url}, Title: {title}"  # type: ignore

    except PlaywrightTimeoutError as pte:
        # Log navigation timeout
        await browser_logger.log_browser_interaction(
            tool_name="openurl",
            action="navigate",
            interaction_type="navigation",
            success=False,
            error_message=str(pte),
            additional_data={
                "url": url,
                "error_type": "timeout",
                "timeout_seconds": timeout,
                "force_new_tab": force_new_tab,
            },
        )
        logger.warning(
            f"Initial navigation to {url} failed: {pte}. Will try to continue anyway."
        )  # happens more often than not, but does not seem to be a problem
        return f"Timeout error opening URL: {url}"

    except Exception as e:

        traceback.print_exc()
        # Log navigation error
        await browser_logger.log_browser_interaction(
            tool_name="openurl",
            action="navigate",
            interaction_type="navigation",
            success=False,
            error_message=str(e),
            additional_data={
                "url": url,
                "error_type": type(e).__name__,
                "force_new_tab": force_new_tab,
            },
        )
        logger.error(f"An error occurred while opening the URL: {url}. Error: {e}")

        traceback.print_exc()
        return f"Error opening URL: {url}"


def ensure_protocol(url: str) -> str:
    """
    Ensures that a URL has a protocol (http:// or https://). If it doesn't have one,
    https:// is added by default.

    Special browser URLs like about:blank, chrome://, etc. are preserved as-is.

    Parameters:
    - url: The URL to check and modify if necessary.

    Returns:
    - A URL string with a protocol.
    """
    # List of special browser URL schemes that should not be modified
    special_schemes = [
        "about:",
        "chrome:",
        "edge:",
        "brave:",
        "firefox:",
        "safari:",
        "data:",
        "file:",
        "view-source:",
    ]

    # Check if the URL starts with any special scheme
    if any(url.startswith(scheme) for scheme in special_schemes):
        logger.debug(f"URL uses a special browser scheme, preserving as-is: {url}")
        return url

    # Regular URL handling
    if not url.startswith(("http://", "https://")):
        url = "https://" + url  # Default to https if no protocol is specified
        logger.info(
            f"Added 'https://' protocol to URL because it was missing. New URL is: {url}"
        )
    return url
