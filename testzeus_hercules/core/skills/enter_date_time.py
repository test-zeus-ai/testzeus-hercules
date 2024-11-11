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
class SetInputValueEntry:
    """
    Represents an entry for setting a value in an input element.

    Attributes:
        query_selector (str): A valid DOM selector query. Use the mmid attribute.
        value (str): The value to set in the input element.
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
    name="set_date_time_value",
    description="Used to set date, time values in an input element identified by a selector, its strictly for time or date fields and should not be used for other input fields.",
)
async def set_date_time_value(
    entry: Annotated[
        SetInputValueEntry,
        "An object containing 'query_selector' (DOM selector query using mmid attribute e.g. [mmid='114']) and 'value' (the value to set in the input element).",
    ]
) -> Annotated[str, "Explanation of the outcome of this operation."]:
    """
    Sets a value in an input element identified by a selector.

    This function sets the specified value in an input element (type 'date', 'time', or other types)
    identified by the given selector. It uses the Playwright library to interact with the browser.

    Args:
        entry (SetInputValueEntry): An object containing 'query_selector' (DOM selector query using mmid attribute)
                                    and 'value' (the value to set in the input element).

    Returns:
        str: Explanation of the outcome of this operation.

    Example:
        entry = SetInputValueEntry(query_selector='#dateInput', value='2023-10-10')
        result = await set_date_time_value(entry)
    """
    add_event(EventType.INTERACTION, EventData(detail="SetInputValue"))
    logger.info(f"Setting input value: {entry}")
    query_selector: str = entry["query_selector"]
    input_value: str = entry["value"]

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

    result = await do_set_date_time_value(page, query_selector, input_value)
    await asyncio.sleep(
        0.1
    )  # sleep for 100ms to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    await browser_manager.notify_user(
        result["summary_message"], message_type=MessageType.ACTION
    )
    if dom_changes_detected:
        return f"{result['detailed_message']}.\nAs a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of setting input value '{input_value}' is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def do_set_date_time_value(
    page: Page, selector: str, input_value: str
) -> dict[str, str]:
    """
    Performs the input value setting operation on an input element.

    This function sets the specified value in an input element identified by the given selector.
    It handles elements within the regular DOM, Shadow DOM, and iframes.

    Args:
        page (Page): The Playwright Page object representing the browser tab.
        selector (str): The selector string used to locate the target element.
        input_value (str): The value to set in the input element.

    Returns:
        dict[str, str]: Explanation of the outcome of this operation represented as a dictionary with 'summary_message' and 'detailed_message'.

    Example:
        result = await do_set_date_time_value(page, '#dateInput', '2023-10-10')
    """
    try:
        logger.debug(
            f"Looking for selector {selector} to set input value: {input_value}"
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

        # Find the input element
        element = await find_element(page, selector)
        if element is None:
            error = f"Error: Selector '{selector}' not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        logger.info(f"Found selector '{selector}' to set input value")

        # Get the element's tag name and type to determine how to interact with it
        tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
        input_type = await element.evaluate("el => el.type")

        if tag_name == "input" and input_type in ["date", "time", "datetime-local"]:
            # For date, time, or datetime-local inputs, set the value directly
            await element.fill(input_value)
            element_outer_html = await get_element_outer_html(element, page)
            success_msg = f"Success. Value '{input_value}' set in the input with selector '{selector}'"
            return {
                "summary_message": success_msg,
                "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
            }
        else:
            error = f"Error: Input type '{input_type}' not supported for setting value."
            return {"summary_message": error, "detailed_message": error}
    except Exception as e:
        traceback.print_exc()
        error = f"Error setting input value in selector '{selector}'."
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}


@skill(
    name="bulk_set_date_time_value",
    description="Sets values in multiple date, time elements using a bulk operation. only used for date or time fields.",
)
async def bulk_set_date_time_value(
    entries: Annotated[
        List[dict[str, str]],
        "List of objects, each containing 'query_selector' and 'value'.",
    ]  # noqa: UP006
) -> Annotated[
    List[dict[str, str]],
    "List of dictionaries, each containing 'query_selector' and the result of the operation.",
]:  # noqa: UP006
    """
    Sets values in multiple input elements using a bulk operation.

    This function sets values in multiple input elements using a bulk operation.
    It takes a list of dictionaries, where each dictionary contains a 'query_selector' and 'value' pair.
    The function internally calls the 'set_date_time_value' function to perform the operation for each entry.

    Args:
        entries: List of objects, each containing 'query_selector' and 'value'.

    Returns:
        List of dictionaries, each containing 'query_selector' and the result of the operation.

    Example:
        entries = [
            {"query_selector": "#dateInput", "value": "2023-10-10"},
            {"query_selector": "#timeInput", "value": "12:30"}
        ]
        results = await bulk_set_date_time_value(entries)
    """
    add_event(EventType.INTERACTION, EventData(detail="BulkSetInputValue"))
    results: List[dict[str, str]] = []  # noqa: UP006
    logger.info("Executing bulk set input value command")
    for entry in entries:
        query_selector = entry["query_selector"]
        input_value = entry["value"]
        logger.info(
            f"Setting input value: '{input_value}' in element with selector: '{query_selector}'"
        )
        result = await set_date_time_value(
            SetInputValueEntry(query_selector=query_selector, value=input_value)
        )

        results.append({"query_selector": query_selector, "result": result})

    return results
