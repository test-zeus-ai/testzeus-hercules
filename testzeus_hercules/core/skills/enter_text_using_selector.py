import asyncio
import inspect
import traceback
from dataclasses import dataclass
from typing import List  # noqa: UP035
from typing import Annotated

from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.skills.press_key_combination import press_key_combination
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe, unsubscribe
from testzeus_hercules.utils.js_helper import block_ads
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.ui_messagetype import MessageType
from playwright.async_api import Page


@dataclass
class EnterTextEntry:
    """
    Represents an entry for text input.

    Attributes:
        query_selector (str): A valid DOM selector query. Use the mmid attribute.
        text (str): The text to enter in the element identified by the query_selector.
    """

    query_selector: str
    text: str

    def __getitem__(self, key: str) -> str:
        if key == "query_selector":
            return self.query_selector
        elif key == "text":
            return self.text
        else:
            raise KeyError(f"{key} is not a valid key")


async def custom_fill_element(page: Page, selector: str, text_to_enter: str):
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
        result = await page.evaluate(
            """(inputParams) => {
                    const selector = inputParams.selector;
                    let text_to_enter = inputParams.text_to_enter.trim();

                    // Helper function to search for an element in regular DOM, shadow DOMs, and iframes
                    const findElementInShadowDOMAndIframes = (parent, selector) => {
                        // Try to find the element in the current DOM context (either document or shadowRoot)
                        let element = parent.querySelector(selector);

                        if (element) {
                            return element; // Element found in the current context
                        }

                        // If not found, look inside shadow roots and iframes of elements in this context
                        const elements = parent.querySelectorAll('*');
                        for (const el of elements) {
                            // Search inside shadow DOMs
                            if (el.shadowRoot) {
                                element = findElementInShadowDOMAndIframes(el.shadowRoot, selector);
                                if (element) {
                                    return element; // Element found in shadow DOM
                                }
                            }
                            // Search inside iframes
                            if (el.tagName.toLowerCase() === 'iframe') {
                                let iframeDocument;
                                try {
                                    iframeDocument = el.contentDocument || el.contentWindow.document;
                                } catch (e) {
                                    // Cannot access cross-origin iframe; skip to the next element
                                    continue;
                                }
                                if (iframeDocument) {
                                    element = findElementInShadowDOMAndIframes(iframeDocument, selector);
                                    if (element) {
                                        return element; // Element found inside iframe
                                    }
                                }
                            }
                        }

                        return null; // Element not found
                    };

                    // Start by searching in the regular document (DOM)
                    const element = findElementInShadowDOMAndIframes(document, selector);

                    if (!element) {
                        throw new Error(`Element not found: ${selector}`);
                    }

                    // Set the value for the element
                    element.value = text_to_enter;

                    return `Value set for ${selector}`;
                }

        """,
            {"selector": selector, "text_to_enter": text_to_enter},
        )
        logger.debug(f"custom_fill_element result: {result}")
    except Exception as e:
        logger.error(
            f"Error in custom_fill_element, Selector: {selector}, Text: {text_to_enter}. Error: {str(e)}"
        )
        raise


async def entertext(
    entry: Annotated[
        EnterTextEntry,
        "An object containing 'query_selector' (DOM selector query using mmid attribute e.g. [mmid='114']) and 'text' (text to enter on the element).",
    ]
) -> Annotated[str, "Explanation of the outcome of this operation."]:
    """
    Enters text into a DOM element identified by a CSS selector.

    This function enters the specified text into a DOM element identified by the given CSS selector.
    It uses the Playwright library to interact with the browser and perform the text entry operation.
    The function supports both direct setting of the 'value' property and simulating keyboard typing.

    Args:
        entry (EnterTextEntry): An object containing 'query_selector' (DOM selector query using mmid attribute)
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

    await browser_manager.highlight_element(query_selector, True)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    subscribe(detect_dom_changes)

    await page.evaluate(
        """
        (selector) => {
                // Helper function to search for an element in regular DOM, shadow DOMs, and iframes
                const findElementInShadowDOMAndIframes = (parent, selector) => {
                    // First, try to find the element in the current DOM context (either document or shadowRoot)
                    let element = parent.querySelector(selector);
                    
                    if (element) {
                        return element; // Element found in the current context
                    }
                    
                    // If not found, look inside shadow roots and iframes of elements in this context
                    const elements = parent.querySelectorAll('*');
                    for (const el of elements) {
                        // Search inside shadow DOMs
                        if (el.shadowRoot) {
                            element = findElementInShadowDOMAndIframes(el.shadowRoot, selector);
                            if (element) {
                                return element; // Element found in shadow DOM
                            }
                        }
                        // Search inside iframes
                        if (el.tagName.toLowerCase() === 'iframe') {
                            let iframeDocument;
                            try {
                                // Access the iframe's document if it's same-origin
                                iframeDocument = el.contentDocument || el.contentWindow.document;
                            } catch (e) {
                                // Cannot access cross-origin iframe; skip to the next element
                                continue;
                            }
                            if (iframeDocument) {
                                element = findElementInShadowDOMAndIframes(iframeDocument, selector);
                                if (element) {
                                    return element; // Element found inside iframe
                                }
                            }
                        }
                    }

                    return null; // Element not found
                };

                // Start by searching in the regular document (DOM)
                const element = findElementInShadowDOMAndIframes(document, selector);

                if (element) {
                    element.value = ''; // Clear the value if element is found
                } else {
                    console.error('Element not found:', selector);
                }
            }

        """,
        query_selector,
    )

    result = await do_entertext(page, query_selector, text_to_enter)
    await asyncio.sleep(
        0.1
    )  # sleep for 100ms to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    await browser_manager.notify_user(
        result["summary_message"], message_type=MessageType.ACTION
    )
    if dom_changes_detected:
        return f"{result['detailed_message']}.\n As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of entering text {text_to_enter} is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def do_entertext(
    page: Page, selector: str, text_to_enter: str, use_keyboard_fill: bool = True
):
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

        # Helper function to handle both regular DOM and Shadow DOM
        async def find_element_in_shadow_dom(page: Page, selector: str):
            # Try to find the element in the regular DOM first
            element = await page.query_selector(selector)

            if element:
                return element

            # If the element is not found, recursively search inside shadow DOMs
            element = await page.evaluate(
                """
                    (selector) => {
                        const findElementInShadowDOMAndIframes = (parent, selector) => {
                            // Try to find the element in the current context
                            let element = parent.querySelector(selector);
                            if (element) {
                                return element; // Element found in the current context
                            }

                            // Search inside shadow DOMs and iframes
                            const elements = parent.querySelectorAll('*');
                            for (const el of elements) {
                                // Search inside shadow DOMs
                                if (el.shadowRoot) {
                                    element = findElementInShadowDOMAndIframes(el.shadowRoot, selector);
                                    if (element) {
                                        return element; // Element found in shadow DOM
                                    }
                                }
                                // Search inside iframes
                                if (el.tagName.toLowerCase() === 'iframe') {
                                    let iframeDocument;
                                    try {
                                        // Access the iframe's document if it's same-origin
                                        iframeDocument = el.contentDocument || el.contentWindow.document;
                                    } catch (e) {
                                        // Cannot access cross-origin iframe; skip to the next element
                                        continue;
                                    }
                                    if (iframeDocument) {
                                        element = findElementInShadowDOMAndIframes(iframeDocument, selector);
                                        if (element) {
                                            return element; // Element found inside iframe
                                        }
                                    }
                                }
                            }
                            return null; // Element not found
                        };
                        return findElementInShadowDOMAndIframes(document, selector);
                    }

                """,
                selector,
            )

            return element

        # Find the element in the DOM or Shadow DOM
        elem = await find_element_in_shadow_dom(page, selector)

        if elem is None:
            error = f"Error: Selector {selector} not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        logger.info(f"Found selector {selector} to enter text")
        element_outer_html = await get_element_outer_html(elem, page)

        if use_keyboard_fill:
            await elem.focus()
            await asyncio.sleep(0.1)
            await press_key_combination("Control+A")
            await asyncio.sleep(0.1)
            await press_key_combination("Backspace")
            await asyncio.sleep(0.1)
            logger.debug(f"Focused element with selector {selector} to enter text")
            await page.keyboard.type(text_to_enter, delay=1)
        else:
            await custom_fill_element(page, selector, text_to_enter)

        await elem.focus()
        logger.info(
            f'Success. Text "{text_to_enter}" set successfully in the element with selector {selector}'
        )
        success_msg = f'Success. Text "{text_to_enter}" set successfully in the element with selector {selector}'
        return {
            "summary_message": success_msg,
            "detailed_message": f"{success_msg} and outer HTML: {element_outer_html}.",
        }

    except Exception as e:
        traceback.print_exc()
        error = f"Error entering text in selector {selector}."
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}


async def bulk_enter_text(
    entries: Annotated[
        List[dict[str, str]],
        "List of objects, each containing 'query_selector' and 'text'.",
    ]  # noqa: UP006
) -> Annotated[
    List[dict[str, str]],
    "List of dictionaries, each containing 'query_selector' and the result of the operation.",
]:  # noqa: UP006
    """
    Enters text into multiple DOM elements using a bulk operation.

    This function enters text into multiple DOM elements using a bulk operation.
    It takes a list of dictionaries, where each dictionary contains a 'query_selector' and 'text' pair.
    The function internally calls the 'entertext' function to perform the text entry operation for each entry.

    Args:
        entries: List of objects, each containing 'query_selector' and 'text'.

    Returns:
        List of dictionaries, each containing 'query_selector' and the result of the operation.

    Example:
        entries = [
            {"query_selector": "#username", "text": "test_user"},
            {"query_selector": "#password", "text": "test_password"}
        ]
        results = await bulk_enter_text(entries)

    Note:
        - Each entry in the 'entries' list should be a dictionary with 'query_selector' and 'text' keys.
        - The result is a list of dictionaries, where each dictionary contains the 'query_selector' and the result of the operation.
    """
    add_event(EventType.INTERACTION, EventData(detail="bulk_enter_text"))
    results: List[dict[str, str]] = []  # noqa: UP006
    logger.info("Executing bulk Enter Text Command")
    for entry in entries:
        query_selector = entry["query_selector"]
        text_to_enter = entry["text"]
        logger.info(
            f"Entering text: {text_to_enter} in element with selector: {query_selector}"
        )
        result = await entertext(
            EnterTextEntry(query_selector=query_selector, text=text_to_enter)
        )

        results.append({"query_selector": query_selector, "result": result})

    return results
