import asyncio
import inspect
import traceback
from dataclasses import dataclass
from typing import List  # noqa: UP035
from typing import Annotated

from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.skills.skill_registry import skill
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe, unsubscribe
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.ui_messagetype import MessageType
from playwright.async_api import ElementHandle, Page


@dataclass
class SelectOptionEntry:
    """
    Represents an entry for selecting an option in a dropdown or spinner.

    Attributes:
        query_selector (str): A valid DOM selector query. Use the mmid attribute.
        value (str): The value or text of the option to select.
    """

    query_selector: str
    value: str

    def __getitem__(self, key: str) -> str:
        if key == "query_selector":
            return self.query_selector
        elif key == "value":
            return self.value
        else:
            raise KeyError(f"{key} is not a valid key")


@skill(
    name="select_option",
    description="used to Selects an option from a dropdown or spinner.",
)
async def select_option(
    entry: Annotated[
        SelectOptionEntry,
        "An object containing 'query_selector' (DOM selector query using mmid attribute e.g. [mmid='114']) and 'value' (the value or text of the option to select).",
    ]
) -> Annotated[str, "Explanation of the outcome of of dropdown/spinner selection."]:
    """
    Selects an option from a dropdown or spinner identified by a CSS selector.

    This function selects the specified option in a dropdown or spinner element identified by the given CSS selector.
    It uses the Playwright library to interact with the browser and perform the selection operation.

    Args:
        entry (SelectOptionEntry): An object containing 'query_selector' (DOM selector query using mmid attribute)
                                   and 'value' (the value or text of the option to select).

    Returns:
        str: Explanation of the outcome of of dropdown/spinner selection.

    Example:
        entry = SelectOptionEntry(query_selector='#country', value='United States')
        result = await select_option(entry)
    """
    add_event(EventType.INTERACTION, EventData(detail="SelectOption"))
    logger.info(f"Selecting option: {entry}")
    query_selector: str = entry["query_selector"]
    option_value: str = entry["value"]

    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        return "Error: No active page found. OpenURL command opens a new page."

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)

    await browser_manager.highlight_element(query_selector, True)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    subscribe(detect_dom_changes)

    result = await do_select_option(page, query_selector, option_value)
    await asyncio.sleep(
        0.1
    )  # sleep for 100ms to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    await browser_manager.notify_user(
        result["summary_message"], message_type=MessageType.ACTION
    )
    if dom_changes_detected:
        return f"{result['detailed_message']}.\nAs a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of selecting option '{option_value}' is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def do_select_option(
    page: Page, selector: str, option_value: str
) -> dict[str, str]:
    """
    Performs the option selection operation on a dropdown or spinner element.

    This function selects the specified option in a dropdown or spinner element identified by the given CSS selector.
    It handles elements within the regular DOM, Shadow DOM, and iframes.

    Args:
        page (Page): The Playwright Page object representing the browser tab.
        selector (str): The CSS selector string used to locate the target element.
        option_value (str): The value or text of the option to select.

    Returns:
        dict[str, str]: Explanation of the outcome of of dropdown/spinner selection represented as a dictionary with 'summary_message' and 'detailed_message'.

    Example:
        result = await do_select_option(page, '#country', 'United States')
    """
    try:
        logger.debug(
            f"Looking for selector {selector} to select option: {option_value}"
        )

        # Helper function to find element in DOM, Shadow DOM, or iframes
        async def find_element(page: Page, selector: str) -> ElementHandle:
            # Try to find the element in the regular DOM first
            element = await page.query_selector(selector)
            if element:
                return element

            # If not found, search inside Shadow DOM and iframes
            element = await page.evaluate_handle(
                """
                (selector) => {
                    const findElementInShadowDOMAndIframes = (parent, selector) => {
                        let element = parent.querySelector(selector);
                        if (element) {
                            return element;
                        }
                        const elements = parent.querySelectorAll('*');
                        for (const el of elements) {
                            if (el.shadowRoot) {
                                element = findElementInShadowDOMAndIframes(el.shadowRoot, selector);
                                if (element) {
                                    return element;
                                }
                            }
                            if (el.tagName.toLowerCase() === 'iframe') {
                                let iframeDocument;
                                try {
                                    iframeDocument = el.contentDocument || el.contentWindow.document;
                                } catch (e) {
                                    continue;
                                }
                                if (iframeDocument) {
                                    element = findElementInShadowDOMAndIframes(iframeDocument, selector);
                                    if (element) {
                                        return element;
                                    }
                                }
                            }
                        }
                        return null;
                    };
                    return findElementInShadowDOMAndIframes(document, selector);
                }
                """,
                selector,
            )
            if element:
                return element.as_element()

            return None

        # Find the dropdown or spinner element
        element = await find_element(page, selector)
        if element is None:
            error = f"Error: Selector '{selector}' not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        logger.info(f"Found selector '{selector}' to select option")

        # Get the element's tag name to determine how to interact with it
        tag_name = await element.evaluate("el => el.tagName.toLowerCase()")

        if tag_name == "select":
            # For <select> elements, use Playwright's select_option method
            await element.select_option(label=option_value)
            element_outer_html = await get_element_outer_html(element, page)
            success_msg = f"Success. Option '{option_value}' selected in the dropdown with selector '{selector}'"
            return {
                "summary_message": success_msg,
                "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
            }
        elif tag_name == "input":
            # For input elements like spinners, set the value directly
            input_type = await element.evaluate("el => el.type")
            if input_type in ["number", "range"]:
                await element.fill(option_value)
                element_outer_html = await get_element_outer_html(element, page)
                success_msg = f"Success. Value '{option_value}' set in the input with selector '{selector}'"
                return {
                    "summary_message": success_msg,
                    "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
                }
            else:
                error = f"Error: Input type '{input_type}' not supported for selection."
                return {"summary_message": error, "detailed_message": error}
        else:
            # Handle custom dropdowns or spinners
            await element.click()
            await asyncio.sleep(0.5)  # Wait for options to appear

            # Try to select the option
            option_selector = f"{selector} option"

            # Find the option elements
            options = await page.query_selector_all(option_selector)
            option_found = False
            for option in options:
                option_text = await option.inner_text()
                option_value_attr = await option.get_attribute("value")
                if (
                    option_text.strip() == option_value
                    or option_value_attr == option_value
                ):
                    await option.click()
                    option_found = True
                    break

            if option_found:
                element_outer_html = await get_element_outer_html(element, page)
                success_msg = f"Success. Option '{option_value}' selected in the element with selector '{selector}'"
                return {
                    "summary_message": success_msg,
                    "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
                }
            else:
                error = f"Error: Option '{option_value}' not found in the element with selector '{selector}'."
                return {"summary_message": error, "detailed_message": error}
    except Exception as e:
        traceback.print_exc()
        error = f"Error selecting option in selector '{selector}'."
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}


@skill(
    name="bulk_select_option",
    description="used to Select an option of multiple dropdowns or spinners in one shot",
)
async def bulk_select_option(
    entries: Annotated[
        List[dict[str, str]],
        "List of objects, each containing 'query_selector' and 'value'.",
    ]  # noqa: UP006
) -> Annotated[
    List[dict[str, str]],
    "List of dictionaries, each containing 'query_selector' and the result of the operation.",
]:  # noqa: UP006
    """
    Selects options in multiple dropdowns or spinners using a bulk operation.

    This function selects options in multiple elements using a bulk operation.
    It takes a list of dictionaries, where each dictionary contains a 'query_selector' and 'value' pair.
    The function internally calls the 'select_option' function to perform the selection operation for each entry.

    Args:
        entries: List of objects, each containing 'query_selector' and 'value'.

    Returns:
        List of dictionaries, each containing 'query_selector' and the result of the operation.

    Example:
        entries = [
            {"query_selector": "#country", "value": "United States"},
            {"query_selector": "#language", "value": "English"}
        ]
        results = await bulk_select_option(entries)
    """
    add_event(EventType.INTERACTION, EventData(detail="BulkSelectOption"))
    results: List[dict[str, str]] = []  # noqa: UP006
    logger.info("Executing bulk select option command")
    for entry in entries:
        query_selector = entry["query_selector"]
        option_value = entry["value"]
        logger.info(
            f"Selecting option: '{option_value}' in element with selector: '{query_selector}'"
        )
        result = await select_option(
            SelectOptionEntry(query_selector=query_selector, value=option_value)
        )

        results.append({"query_selector": query_selector, "result": result})

    return results
