import asyncio
import inspect
import traceback
from typing import Annotated

import playwright.async_api
from playwright.async_api import ElementHandle, Page
from testzeus_hercules.config import get_global_conf  # Add this import
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe  # type: ignore
from testzeus_hercules.utils.dom_mutation_observer import unsubscribe  # type: ignore
from testzeus_hercules.utils.logger import logger


@tool(agent_names=["browser_nav_agent"], description="""Hovers over element by md. Returns tooltip details. ALL TOOL ARGUMENTS ARE MANDATORY""", name="hover")
async def hover(
    selector: Annotated[str, "selector using md attribute, eg:[md='114'] md is ID"],
    wait_before_execution: Annotated[float, "Wait time in seconds before hover"] = 0.0,
) -> Annotated[str, "Result of hover action with tooltip text"]:
    """
    Executes a hover action on the element matching the given query selector string within the currently open web page.
    If there is no page open, it will raise a ValueError. An optional wait time can be specified before executing the hover logic. Use this to wait for the page to load especially when the last action caused the DOM/Page to load.

    Parameters:
    - selector: The query selector string to identify the element for the hover action.
    - wait_before_execution: Optional wait time in seconds before executing the hover event logic. Defaults to 0.0 seconds.

    Returns:
    - Success message if the hover was successful, appropriate error message otherwise.
    """
    logger.info(f'Executing HoverElement with "{selector}" as the selector')
    add_event(EventType.INTERACTION, EventData(detail="hover"))
    # Initialize PlaywrightManager and get the active browser page
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()

    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)

    await browser_manager.highlight_element(selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    subscribe(detect_dom_changes)
    result = await do_hover(page, selector, wait_before_execution)
    await asyncio.sleep(get_global_conf().get_delay_time())  # sleep to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)
    await page.wait_for_load_state()
    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"Success: {result['summary_message']}.\nAs a consequence of this action, new elements have appeared in view: {dom_changes_detected}. You may need further interaction. Get all_fields DOM to complete the interaction, if needed, also the tooltip data is already in the message"
    return result["detailed_message"]


async def do_hover(page: Page, selector: str, wait_before_execution: float) -> dict[str, str]:
    """
    Executes the hover action on the element with the given selector within the provided page,
    including searching within iframes and shadow DOMs if necessary.

    Parameters:
    - page: The Playwright page instance.
    - selector: The query selector string to identify the element for the hover action.
    - wait_before_execution: Optional wait time in seconds before executing the hover event logic.

    Returns:
    dict[str,str] - Explanation of the outcome of this operation represented as a dictionary with 'summary_message' and 'detailed_message'.
    """
    logger.info(f'Executing HoverElement with "{selector}" as the selector. Wait time before execution: {wait_before_execution} seconds.')

    # Wait before execution if specified
    if wait_before_execution > 0:
        await asyncio.sleep(wait_before_execution)

    try:
        logger.info(f'Executing HoverElement with "{selector}" as the selector. Waiting for the element to be attached and visible.')

        # Attempt to find the element on the main page or in shadow DOMs and iframes
        browser_manager = PlaywrightManager()

        element = await browser_manager.find_element(selector, page)
        if element is None:
            raise ValueError(f'Element with selector: "{selector}" not found')

        logger.info(f'Element with selector: "{selector}" is found. Scrolling it into view if needed.')
        try:
            await element.scroll_into_view_if_needed(timeout=200)
            logger.info(f'Element with selector: "{selector}" is scrolled into view. Waiting for the element to be visible.')
        except Exception:
            # If scrollIntoView fails, just move on
            pass

        try:
            await element.wait_for_element_state("visible", timeout=200)
            logger.info(f'Element with selector: "{selector}" is visible. Hovering over the element.')
        except Exception:
            # If the element is not visible, try to hover over it anyway
            pass

        element_tag_name = await element.evaluate("element => element.tagName.toLowerCase()")
        element_outer_html = await get_element_outer_html(element, page, element_tag_name)

        await perform_playwright_hover(element, selector)

        # Wait briefly to allow any tooltips to appear
        await asyncio.sleep(0.2)

        # Capture tooltip information
        tooltip_text = await get_tooltip_text(page)

        msg = f'Executed hover action on element with selector: "{selector}".'
        if tooltip_text:
            msg += f' Tooltip shown: "{tooltip_text}".'

        return {
            "summary_message": msg,
            "detailed_message": f"{msg} The hovered element's outer HTML is: {element_outer_html}.",
        }
    except Exception as e:
        logger.error(f'Unable to hover over element with selector: "{selector}". Error: {e}')
        traceback.print_exc()
        msg = f'Unable to hover over element with selector: "{selector}" since the selector is invalid or the element is not interactable. Consider retrieving the DOM again.'
        return {"summary_message": msg, "detailed_message": f"{msg}. Error: {e}"}


async def get_tooltip_text(page: Page) -> str:
    # JavaScript code to find tooltip elements
    js_code = """
    () => {
        // Search for elements with role="tooltip"
        let tooltip = document.querySelector('[role="tooltip"]');
        if (tooltip && tooltip.innerText) {
            return tooltip.innerText.trim();
        }

        // Search for common tooltip classes
        let tooltipClasses = ['tooltip', 'ui-tooltip', 'tooltip-inner'];
        for (let cls of tooltipClasses) {
            tooltip = document.querySelector('.' + cls);
            if (tooltip && tooltip.innerText) {
                return tooltip.innerText.trim();
            }
        }

        return '';
    }
    """
    try:
        tooltip_text = await page.evaluate(js_code)
        return tooltip_text
    except Exception as e:
        logger.error(f"Error retrieving tooltip text: {e}")
        return ""


async def perform_playwright_hover(element: ElementHandle, selector: str) -> None:
    """
    Performs a hover action on the element using Playwright's hover method.

    Parameters:
    - element: The Playwright ElementHandle instance representing the element to be hovered over.
    - selector: The query selector string of the element.

    Returns:
    - None
    """
    logger.info("Performing Playwright hover on element with selector: %s", selector)
    await element.hover(force=True, timeout=200)
