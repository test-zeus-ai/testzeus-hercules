import inspect
import traceback
from typing import Annotated, Dict, List, Tuple, Optional, Union

from playwright.sync_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.prompts import LLM_PROMPTS
from testzeus_hercules.core.browser_tools.press_key_combination import (
    press_key_combination,
)
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe, unsubscribe
from testzeus_hercules.utils.js_helper import block_ads, get_js_with_element_finder
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.ui_messagetype import MessageType


def custom_fill_element(page: Page, selector: str, text_to_enter: str) -> None:
    selector = f"{selector}"  # Ensures the selector is treated as a string
    try:
        js_code = """(inputParams) => {
            /*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/
            const selector = inputParams.selector;
            let text_to_enter = inputParams.text_to_enter.trim();

            // Start by searching in the regular document (DOM)
            const element = findElementInShadowDOMAndIframes(document, selector);

            if (!element) {
                throw new Error(`Element not found: ${selector}`);
            }

            // Set the value for the element
            element.value = "";
            element.value = text_to_enter;
            element.dispatchEvent(new Event('input', { bubbles: true }));
            element.dispatchEvent(new Event('change', { bubbles: true }));

            return `Value set for ${selector}`;
        }"""

        result = page.evaluate(
            get_js_with_element_finder(js_code),
            {"selector": selector, "text_to_enter": text_to_enter},
        )
        logger.debug(f"custom_fill_element result: {result}")
    except Exception as e:
        logger.error(
            f"Error in custom_fill_element, Selector: {selector}, Text: {text_to_enter}. Error: {str(e)}"
        )
        raise


@tool(
    agent_names=["navigation_nav_agent"],
    description="""Enter text into an input field using md attribute. Returns success/failure status.""",
    name="entertext",
)
def entertext(
    entry: Annotated[
        Tuple[str, str],
        "tuple containing 'selector' and 'value_to_fill' in ('selector', 'value_to_fill') format, selector is md attribute value of the dom element to interact, md is an ID and 'value_to_fill' is the value or text of the option to select",
    ]
) -> Annotated[str, "Text entry result"]:
    add_event(EventType.INTERACTION, EventData(detail="EnterText"))

    selector, text_to_enter = entry
    query_selector = selector if "md=" in selector else f"[md='{selector}']"

    logger.info(f'Executing EnterText with "{query_selector}" as the selector')

    # Initialize PlaywrightManager and get the active browser page
    browser_manager = PlaywrightManager()
    page = browser_manager.get_current_page()

    if page is None:
        raise ValueError("No active page found. OpenURL command opens a new page.")

    function_name = "entertext"
    browser_manager.take_screenshots(f"{function_name}_start", page)
    browser_manager.highlight_element(query_selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: List[str]) -> None:
        nonlocal dom_changes_detected
        dom_changes_detected = changes

    subscribe(detect_dom_changes)
    result = do_entertext(page, query_selector, text_to_enter)

    page.wait_for_load_state()
    browser_manager.take_screenshots(f"{function_name}_end", page)
    unsubscribe(detect_dom_changes)

    if dom_changes_detected:
        return f"{result['message']}.\n As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of entering text {text_to_enter} is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["message"]


def do_entertext(page: Page, selector: str, text_to_enter: str) -> Dict[str, str]:
    """
    Enter text into an element with the given selector.

    Args:
        page: The Playwright page instance
        selector: The query selector string
        text_to_enter: The text to enter into the element

    Returns:
        Dict[str, str]: Status and message of the operation
    """
    try:
        browser_manager = PlaywrightManager()
        element = browser_manager.find_element(selector, page)

        if not element:
            return {"error": f"Element not found with selector: {selector}"}

        # Ensure element is visible and scrolled into view
        element.scroll_into_view_if_needed(timeout=200)
        element.wait_for_element_state("visible", timeout=200)

        # Get element HTML for logging
        element_outer_html = get_element_outer_html(element, page)

        # Clear existing text and enter new text
        element.fill("")
        element.type(text_to_enter)

        return {
            "status": "success",
            "message": f"Successfully entered text into element: {element_outer_html}",
        }

    except Exception as e:
        logger.error(f"Error in do_entertext: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["navigation_nav_agent"],
    name="bulk_enter_text",
    description="Enters text into multiple DOM elements using a bulk operation. An dict containing'selector' (selector query using md attribute e.g. [md='114'] md is ID) and 'text' (text to enter on the element)",
)
def bulk_enter_text(
    entries: Annotated[
        List[List[str]],
        "List of tuple containing 'selector' and 'value_to_fill' in [('selector', 'value_to_fill'), ..] format, selector is md attribute value of the dom element to interact, md is an ID and 'value_to_fill' is the value or text",
    ]
) -> Annotated[
    List[Dict[str, str]],
    "List of dictionaries, each containing 'selector' and the result of the operation.",
]:
    add_event(EventType.INTERACTION, EventData(detail="BulkSetInputValue"))
    results: List[Dict[str, str]] = []
    logger.info("Executing bulk set input value command")
    for entry in entries:
        result = entertext(entry)  # Use dictionary directly
        results.append({"selector": entry[0], "result": result})

    return results
