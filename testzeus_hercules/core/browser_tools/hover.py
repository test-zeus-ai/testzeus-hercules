import time
from typing import Annotated, Dict, Optional

from playwright.sync_api import ElementHandle, Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["hover_nav_agent"],
    description="Hover over an element using a selector.",
    name="hover",
)
def hover(
    selector: Annotated[str, "CSS selector for the element to hover over."],
    wait_before_execution: Annotated[
        float, "Time to wait before hovering (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the hover operation."]:
    """
    Hover over an element using a selector.
    """
    function_name = "hover"
    browser_manager = PlaywrightManager()
    page = browser_manager.get_current_page()

    try:
        browser_manager.take_screenshots(f"{function_name}_start", page)
        browser_manager.highlight_element(selector)

        result = do_hover(page, selector, wait_before_execution)
        time.sleep(
            get_global_conf().get_delay_time()
        )  # sleep to allow the mutation observer to detect changes

        # Wait for any network activity to complete
        page.wait_for_load_state("networkidle")
        browser_manager.take_screenshots(f"{function_name}_end", page)

        return result
    except Exception as e:
        logger.error(f"Error in {function_name}: {str(e)}")
        return {"error": str(e)}


def do_hover(page: Page, selector: str, wait_before_execution: float) -> Dict[str, str]:
    """
    Helper function to perform the actual hover operation.
    Example:
    ```python
    result = do_hover(page, '#menu-item', 0.5)
    ```
    """
    try:
        # Wait before execution if specified
        if wait_before_execution > 0:
            time.sleep(wait_before_execution)

        browser_manager = PlaywrightManager()
        element = browser_manager.find_element(selector, page)
        if not element:
            return {"error": f"Element not found with selector: {selector}"}

        # Ensure element is visible and scrolled into view
        element.scroll_into_view_if_needed(timeout=200)
        element.wait_for_element_state("visible", timeout=200)

        # Get element HTML before hover for logging
        element_tag_name = element.evaluate("element => element.tagName.toLowerCase()")
        element_outer_html = get_element_outer_html(element, page, element_tag_name)

        # Perform the hover action
        perform_playwright_hover(element, selector)

        # Wait for any tooltip to appear
        time.sleep(0.2)

        # Try to get tooltip text
        tooltip_text = get_tooltip_text(page)

        return {
            "success": True,
            "message": f"Successfully hovered over element: {element_outer_html}",
            "tooltip": tooltip_text,
        }
    except Exception as e:
        logger.error(f"Error in do_hover: {str(e)}")
        return {"error": str(e)}


def get_tooltip_text(page: Page) -> str:
    """
    Try to get tooltip text using various common tooltip attributes and classes.
    """
    js_code = """
    () => {
        // Common tooltip selectors
        const tooltipSelectors = [
            '[role="tooltip"]',
            '.tooltip',
            '[data-tooltip]',
            '[aria-label]',
            '[title]'
        ];

        for (const selector of tooltipSelectors) {
            const tooltipElement = document.querySelector(selector);
            if (tooltipElement) {
                // Try different ways to get the tooltip text
                return tooltipElement.textContent ||
                       tooltipElement.getAttribute('data-tooltip') ||
                       tooltipElement.getAttribute('aria-label') ||
                       tooltipElement.getAttribute('title') ||
                       '';
            }
        }
        return '';
    }
    """
    try:
        tooltip_text = page.evaluate(js_code)
        return tooltip_text.strip() if tooltip_text else ""
    except Exception as e:
        logger.error(f"Error getting tooltip text: {str(e)}")
        return ""


def perform_playwright_hover(element: ElementHandle, selector: str) -> None:
    """
    Perform the hover action using Playwright's hover method.

    Args:
        element: The Playwright ElementHandle to hover over
        selector: The selector string (for error reporting)
    """
    try:
        element.hover(force=True, timeout=200)
    except Exception as e:
        logger.error(
            f"Error hovering over element with selector '{selector}': {str(e)}"
        )
        raise
