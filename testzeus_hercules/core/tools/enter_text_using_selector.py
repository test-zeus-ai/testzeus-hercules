import asyncio
import inspect
import traceback
from typing import Annotated, Dict, List, Tuple

from playwright.async_api import Page
from testzeus_hercules.config import get_global_conf  # Add this import
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.prompts import LLM_PROMPTS
from testzeus_hercules.core.tools.press_key_combination import press_key_combination
from testzeus_hercules.core.browser_logger import get_browser_logger
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe, unsubscribe
from testzeus_hercules.utils.js_helper import block_ads, get_js_with_element_finder
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.ui_messagetype import MessageType


async def custom_fill_element(page: Page, selector: str, text_to_enter: str) -> None:
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
        logger.error(
            f"Error in custom_fill_element, Selector: {selector}, Text: {text_to_enter}. Error: {str(e)}"
        )
        raise


async def entertext(
    entry: Annotated[
        tuple[str, str],
        "tuple containing 'selector' and 'value_to_fill' in ('selector', 'value_to_fill') format, selector is md attribute value of the dom element to interact, md is an ID and 'value_to_fill' is the value or text of the option to select",
    ]
) -> Annotated[str, "Text entry result"]:
    add_event(EventType.INTERACTION, EventData(detail="EnterText"))
    logger.info(f"Entering text: {entry}")

    selector: str = entry[0]
    text_to_enter: str = entry[1]

    if "md=" not in selector:
        selector = f"[md='{selector}']"

    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    # await page.route("**/*", block_ads)
    if page is None:  # type: ignore
        return "Error: No active page found. OpenURL command opens a new page."

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)

    await browser_manager.highlight_element(selector)

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
        selector,
    )

    result = await do_entertext(page, selector, text_to_enter)
    await asyncio.sleep(
        get_global_conf().get_delay_time()
    )  # sleep to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)
    await page.wait_for_load_state()
    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"{result['detailed_message']}.\n As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of entering text {text_to_enter} is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def do_entertext(
    page: Page, selector: str, text_to_enter: str, use_keyboard_fill: bool = True
) -> dict[str, str]:
    """
    Performs the text entry operation on a DOM or Shadow DOM element.

    This function performs the text entry operation on a DOM element identified by the given CSS selector.
    It applies a pulsating border effect to the element during the operation for visual feedback.
    The function supports both direct setting of the 'value' property and simulating keyboard typing.

    Args:
        page (Page): The Playwright Page object representing the browser tab in which the operation will be performed.
        selector (str): The CSS selector string used to locate the target DOM element.
        text_to_enter (str): The text value to be set in the target element. Existing content will be overwritten.
        use_keyboard_fill (bool, optional): Whether to simulate keyboard typing or not.
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
        elem = await browser_manager.find_element(
            selector, page, element_name="entertext"
        )
        if not elem:
            # Initialize selector logger with proof path
            selector_logger = get_browser_logger(get_global_conf().get_proof_path())
            # Log failed selector interaction
            await selector_logger.log_selector_interaction(
                tool_name="entertext",
                selector=selector,
                action="input",
                selector_type="css" if "md=" in selector else "custom",
                success=False,
                error_message=f"Error: Selector {selector} not found. Unable to continue.",
            )
            error = f"Error: Selector {selector} not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        logger.info(f"Found selector {selector} to enter text")
        element_outer_html = await get_element_outer_html(elem, page)

        # Initialize selector logger with proof path
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
        # Get alternative selectors and element attributes for logging
        alternative_selectors = await selector_logger.get_alternative_selectors(
            elem, page
        )
        element_attributes = await selector_logger.get_element_attributes(elem)

        if use_keyboard_fill:
            await elem.focus()
            await asyncio.sleep(0.01)
            await press_key_combination("Control+A")
            await asyncio.sleep(0.01)
            await press_key_combination("Delete")
            await asyncio.sleep(0.01)
            logger.debug(f"Focused element with selector {selector} to enter text")
            await page.keyboard.type(text_to_enter, delay=1)
        else:
            await custom_fill_element(page, selector, text_to_enter)

        await elem.focus()
        await page.wait_for_load_state()

        # Log successful selector interaction
        await selector_logger.log_selector_interaction(
            tool_name="entertext",
            selector=selector,
            action="input",
            selector_type="css" if "md=" in selector else "custom",
            alternative_selectors=alternative_selectors,
            element_attributes=element_attributes,
            success=True,
            additional_data={
                "text_entered": text_to_enter,
                "input_method": "keyboard" if use_keyboard_fill else "javascript",
            },
        )

        logger.info(
            f'Success. Text "{text_to_enter}" set successfully in the element with selector {selector}'
        )
        success_msg = f'Success. Text "{text_to_enter}" set successfully in the element with selector {selector}'
        return {
            "summary_message": success_msg,
            "detailed_message": f"{success_msg} and outer HTML: {element_outer_html}.",
        }

    except Exception as e:
        # Initialize selector logger with proof path
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
        # Log failed selector interaction
        await selector_logger.log_selector_interaction(
            tool_name="entertext",
            selector=selector,
            action="input",
            selector_type="css" if "md=" in selector else "custom",
            success=False,
            error_message=str(e),
        )

        traceback.print_exc()
        error = f"Error entering text in selector {selector}."
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}


@tool(
    agent_names=["browser_nav_agent"],
    name="bulk_enter_text",
    description="Enters text into multiple DOM elements using a bulk operation. An dict containing'selector' (selector query using md attribute e.g. [md='114'] md is ID) and 'text' (text to enter on the element)",
)
async def bulk_enter_text(
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
        if len(entry) != 2:
            logger.error(f"Invalid entry format: {entry}. Expected [selector, value]")
            continue
        result = await entertext(
            (entry[0], entry[1])
        )  # Create tuple with explicit values
        results.append({"selector": entry[0], "result": result})

    return results
