import asyncio
import inspect
import traceback
from typing import Annotated, Dict, List

from playwright.async_api import Page
from testzeus_hercules.config import get_global_conf  # Add this import
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.prompts import LLM_PROMPTS
from testzeus_hercules.core.tools.press_key_combination import press_key_combination
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe, unsubscribe
from testzeus_hercules.utils.js_helper import block_ads, get_js_with_element_finder
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.ui_messagetype import MessageType


async def custom_fill_element(page: Page, selector: str, text_to_enter: str) -> None:
    """
    Sets the value of a DOM element to a specified text without triggering keyboard input events.

    This function directly sets the 'value' property of a DOM element identified by the given CSS selector,
    effectively changing its current value to the specified text. This approach bypasses the need for
    simulating keyboard typing, providing a more efficient and reliable way to fill in text fields,
    especially in automated testing scenarios where speed and accuracy are paramount.

    Args:
        page (Page): The Playwright Page object representing the browser tab in which the operation will be performed.
        selector (str): The CSS selector string used to locate the target DOM element. The function will apply the
                        text change to the first element that matches this selector.
        text_to_enter (str): The text value to be set in the target element. Existing content will be overwritten.

    Example:
        await custom_fill_element(page, '#username', 'test_user')

    Note:
        This function does not trigger input-related events (like 'input' or 'change'). If application logic
        relies on these events being fired, additional steps may be needed to simulate them.
    """
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

        result = await page.evaluate(
            get_js_with_element_finder(js_code),
            {"selector": selector, "text_to_enter": text_to_enter},
        )
        logger.debug(f"custom_fill_element result: {result}")
    except Exception as e:
        logger.error(f"Error in custom_fill_element, Selector: {selector}, Text: {text_to_enter}. Error: {str(e)}")
        raise


# @tool(
#     agent_names=["browser_nav_agent"],
#     description="""Enters text in element by md. Text-only operation without Enter press.""",
#     name="entertext"
# )
async def entertext(
    entry: Annotated[
        dict,
        "An dict containing'query_selector' (selector query using md attribute e.g. [md='114'] md is ID) and 'text' (text to enter on the element).",
    ]
) -> Annotated[str, "Text entry result"]:
    """
    Enters text into a DOM element identified by a CSS selector.

    This function enters the specified text into a DOM element identified by the given CSS selector.
    It uses the Playwright library to interact with the browser and perform the text entry operation.
    The function supports both direct setting of the 'value' property and simulating keyboard typing.

    Args:
        entry (EnterTextEntry): An dict containing'query_selector' (selector query using md attribute)
                                and 'text' (text to enter on the element).

    Returns:
        str: Explanation of the outcome of this operation.

    Example:
        entry = EnterTextEntry(query_selector='#username', text='test_user')
        result = await entertext(entry)

    Note:
        - The 'query_selector' should be a valid CSS selector that uniquely identifies the target element.
        - The 'text' parameter specifies the text to be entered into the element.
        - The function uses the PlaywrightManager to manage the browser instance.
        - If no active page is found, an error message is returned.
        - The function internally calls the 'do_entertext' function to perform the text entry operation.
        - The 'do_entertext' function applies a pulsating border effect to the target element during the operation.
        - The 'use_keyboard_fill' parameter in 'do_entertext' determines whether to simulate keyboard typing or not.
        - If 'use_keyboard_fill' is set to True, the function uses the 'page.keyboard.type' method to enter the text.
        - If 'use_keyboard_fill' is set to False, the function uses the 'custom_fill_element' method to enter the text.
    """
    add_event(EventType.INTERACTION, EventData(detail="EnterText"))
    logger.info(f"Entering text: {entry}")
    query_selector: str = entry["query_selector"]
    text_to_enter: str = entry["text"]

    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    # await page.route("**/*", block_ads)
    if page is None:  # type: ignore
        return "Error: No active page found. OpenURL command opens a new page."

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)

    await browser_manager.highlight_element(query_selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    subscribe(detect_dom_changes)

    await page.evaluate(
        get_js_with_element_finder(
            """
        (selector) => {
            /*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/
            const element = findElementInShadowDOMAndIframes(document, selector);
            if (element) {
                element.value = '';
            } else {
                console.error('Element not found:', selector);
            }
        }
        """
        ),
        query_selector,
    )

    result = await do_entertext(page, query_selector, text_to_enter)
    await asyncio.sleep(get_global_conf().get_delay_time())  # sleep to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)
    await page.wait_for_load_state()
    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"{result['detailed_message']}.\n As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of entering text {text_to_enter} is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def do_entertext(page: Page, selector: str, text_to_enter: str, use_keyboard_fill: bool = True) -> dict[str, str]:
    """
    Performs the text entry operation on a DOM or Shadow DOM element.

    This function performs the text entry operation on a DOM element identified by the given CSS selector.
    It applies a pulsating border effect to the element during the operation for visual feedback.
    The function supports both direct setting of the 'value' property and simulating keyboard typing.

    Args:
        page (Page): The Playwright Page object representing the browser tab in which the operation will be performed.
        selector (str): The CSS selector string used to locate the target DOM element.
        text_to_enter (str): The text value to be set in the target element. Existing content will be overwritten.
        use_keyboard_fill (bool, optional): Determines whether to simulate keyboard typing or not.
                                            Defaults to False.

    Returns:
        dict[str, str]: Explanation of the outcome of this operation represented as a dictionary with 'summary_message' and 'detailed_message'.

    Example:
        result = await do_entertext(page, '#username', 'test_user')

    Note:
        - The 'use_keyboard_fill' parameter determines whether to simulate keyboard typing or not.
        - If 'use_keyboard_fill' is set to True, the function uses the 'page.keyboard.type' method to enter the text.
        - If 'use_keyboard_fill' is set to False, the function uses the 'custom_fill_element' method to enter the text.
    """
    try:
        logger.debug(f"Looking for selector {selector} to enter text: {text_to_enter}")

        browser_manager = PlaywrightManager()
        elem = await browser_manager.find_element(selector, page)
        if not elem:
            error = f"Error: Selector {selector} not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        logger.info(f"Found selector {selector} to enter text")
        element_outer_html = await get_element_outer_html(elem, page)

        if use_keyboard_fill:
            await elem.focus()
            await asyncio.sleep(0.05)
            await press_key_combination("Control+A")
            await asyncio.sleep(0.05)
            await press_key_combination("Delete")
            await asyncio.sleep(0.05)
            logger.debug(f"Focused element with selector {selector} to enter text")
            await page.keyboard.type(text_to_enter, delay=1)
        else:
            await custom_fill_element(page, selector, text_to_enter)

        await elem.focus()
        await page.wait_for_load_state()
        logger.info(f'Success. Text "{text_to_enter}" set successfully in the element with selector {selector}')
        success_msg = f'Success. Text "{text_to_enter}" set successfully in the element with selector {selector}'
        return {
            "summary_message": success_msg,
            "detailed_message": f"{success_msg} and outer HTML: {element_outer_html}.",
        }

    except Exception as e:
        traceback.print_exc()
        error = f"Error entering text in selector {selector}."
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}


@tool(
    agent_names=["browser_nav_agent"],
    name="bulk_enter_text",
    description="Enters text into multiple DOM elements using a bulk operation. An dict containing'query_selector' (selector query using md attribute e.g. [md='114'] md is ID) and 'text' (text to enter on the element). ALL TOOL ARGUMENTS ARE MANDATORY",
)
async def bulk_enter_text(
    entries: Annotated[
        List[dict],
        "List of dictionaries containing 'query_selector' and 'text' key-value pairs, dict containing 'query_selector' (selector query using md attribute e.g. [md='114'] md is ID) and 'value' (the value or text of the option to select). MANDATORY FIELD",
    ]
) -> Annotated[
    List[str],
    "List of results from the entertext operation for each entry",
]:
    """
    Enters text into multiple DOM elements using a bulk operation.

    This function enters text into multiple DOM elements using a bulk operation.
    It takes a list of dictionaries, where each contains 'query_selector' and 'text' keys.
    The function internally calls the 'entertext' function to perform the text entry operation for each entry.

    Args:
        entries: List of dictionaries containing 'query_selector' and 'text'.

    Returns:
        List of results from the entertext operation for each entry.

    Example:
        entries = [
            {"query_selector": "#username", "text": "test_user"},
            {"query_selector": "#password", "text": "test_password"}
        ]
        results = await bulk_enter_text(entries)

    Note:
        - Each entry in the 'entries' list should be an instance of EnterTextEntry.
        - The result is a list of strings returned by the 'entertext' function for each entry.
    """
    add_event(EventType.INTERACTION, EventData(detail="bulk_enter_text"))
    results: List[str] = []  # noqa: UP006
    logger.info("Executing bulk Enter Text Command")
    for entry in entries:
        logger.info(f"Entering text: {entry['text']} in element with selector: {entry['query_selector']}")
        result = await entertext(entry)
        results.append(result)
    return results
