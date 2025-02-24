import asyncio
import inspect
import traceback
from dataclasses import dataclass
from typing import Annotated, List, Tuple, Dict

from playwright.async_api import ElementHandle, Page
from testzeus_hercules.config import get_global_conf  # Add this import
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.click_using_selector import do_click
from testzeus_hercules.core.browser_logger import get_browser_logger
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe, unsubscribe
from testzeus_hercules.utils.js_helper import get_js_with_element_finder
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.ui_messagetype import MessageType


async def select_option(
    entry: Annotated[
        tuple[str, str],
        "tuple containing 'selector' and 'value_to_fill' in ('selector', 'value_to_fill') format, selector is md attribute value of the dom element to interact, md is an ID and 'value_to_fill' is the value or text of the option to select",
    ]
) -> Annotated[str, "Explanation of the outcome of dropdown/spinner selection."]:
    add_event(EventType.INTERACTION, EventData(detail="SelectOption"))
    logger.info(f"Selecting option: {entry}")
    selector: str = entry[0]
    option_value: str = entry[1]

    if "md=" not in selector:
        selector = f"[md='{selector}']"

    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
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

    result = await do_select_option(page, selector, option_value)
    await asyncio.sleep(
        get_global_conf().get_delay_time()
    )  # sleep to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)

    await page.wait_for_load_state()

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"{result['detailed_message']}.\nAs a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of selecting option '{option_value}' is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def custom_select_option(page: Page, selector: str, option_value: str) -> None:
    """
    Selects an option in a dropdown element using JavaScript.

    Args:
        page (Page): The Playwright Page object.
        selector (str): The CSS selector for the dropdown element.
        option_value (str): The value or text of the option to select.
    """
    try:
        selector = f"{selector}"  # Ensure the selector is treated as a string
        js_code = """(inputParams) => {
            /*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/
            const selector = inputParams.selector;
            const option_value = inputParams.option_value;

            const element = findElementInShadowDOMAndIframes(document, selector);
            if (!element) {
                throw new Error(`Element not found: ${selector}`);
            }

            // Find the option to select
            let optionFound = false;
            for (const option of element.options) {
                if (option.value === option_value || option.text === option_value) {
                    element.value = option.value;
                    const event = new Event('change', { bubbles: true });
                    element.dispatchEvent(event);
                    optionFound = true;
                    break;
                }
            }

            if (!optionFound) {
                throw new Error(`Option '${option_value}' not found in element '${selector}'`);
            }

            return `Option '${option_value}' selected in element '${selector}'`;
        }"""

        result = await page.evaluate(
            get_js_with_element_finder(js_code),
            {"selector": selector, "option_value": option_value},
        )
        logger.debug(f"custom_select_option result: {result}")
    except Exception as e:
        logger.error(
            f"Error in custom_select_option, Selector: {selector}, Option: {option_value}. Error: {str(e)}"
        )
        raise


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

        browser_manager = PlaywrightManager()
        element = await browser_manager.find_element(
            selector, page, element_name="select_option"
        )
        if not element:
            # Initialize selector logger with proof path
            selector_logger = get_browser_logger(get_global_conf().get_proof_path())
            # Log failed selector interaction
            await selector_logger.log_selector_interaction(
                tool_name="select_option",
                selector=selector,
                action="select",
                selector_type="css" if "md=" in selector else "custom",
                success=False,
                error_message=f"Error: Selector '{selector}' not found. Unable to continue.",
            )
            error = f"Error: Selector '{selector}' not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        logger.info(f"Found selector '{selector}' to select option")

        # Initialize selector logger with proof path
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
        # Get alternative selectors and element attributes for logging
        alternative_selectors = await selector_logger.get_alternative_selectors(
            element, page
        )
        element_attributes = await selector_logger.get_element_attributes(element)

        # Get the element's tag name to determine how to interact with it
        tag_name = await element.evaluate("el => el.tagName.toLowerCase()")

        if tag_name == "select":
            # For <select> elements, use custom_select_option method
            await custom_select_option(page, selector, option_value)
            element_outer_html = await get_element_outer_html(element, page)

            # Log successful selector interaction for select element
            await selector_logger.log_selector_interaction(
                tool_name="select_option",
                selector=selector,
                action="select",
                selector_type="css" if "md=" in selector else "custom",
                alternative_selectors=alternative_selectors,
                element_attributes=element_attributes,
                success=True,
                additional_data={
                    "element_type": "select",
                    "selected_value": option_value,
                },
            )

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

                # Log successful selector interaction for input element
                await selector_logger.log_selector_interaction(
                    tool_name="select_option",
                    selector=selector,
                    action="input",
                    selector_type="css" if "md=" in selector else "custom",
                    alternative_selectors=alternative_selectors,
                    element_attributes=element_attributes,
                    success=True,
                    additional_data={
                        "element_type": "input",
                        "input_type": input_type,
                        "value": option_value,
                    },
                )

                success_msg = f"Success. Value '{option_value}' set in the input with selector '{selector}'"
                return {
                    "summary_message": success_msg,
                    "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
                }
            else:
                # Log failed selector interaction for unsupported input type
                await selector_logger.log_selector_interaction(
                    tool_name="select_option",
                    selector=selector,
                    action="select",
                    selector_type="css" if "md=" in selector else "custom",
                    alternative_selectors=alternative_selectors,
                    element_attributes=element_attributes,
                    success=False,
                    error_message=f"Error: Input type '{input_type}' not supported for selection.",
                )

                error = f"Error: Input type '{input_type}' not supported for selection."
                return {"summary_message": error, "detailed_message": error}
        else:
            # Handle custom dropdowns or spinners
            await do_click(page, selector, 1, "click")
            await asyncio.sleep(0.2)  # Wait for options to appear

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

                # Log successful selector interaction for custom dropdown
                await selector_logger.log_selector_interaction(
                    tool_name="select_option",
                    selector=selector,
                    action="select",
                    selector_type="css" if "md=" in selector else "custom",
                    alternative_selectors=alternative_selectors,
                    element_attributes=element_attributes,
                    success=True,
                    additional_data={
                        "element_type": "custom_dropdown",
                        "selected_value": option_value,
                    },
                )

                success_msg = f"Success. Option '{option_value}' selected in the element with selector '{selector}', Try some other tool."
                return {
                    "summary_message": success_msg,
                    "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
                }
            else:
                # Log failed selector interaction for option not found
                await selector_logger.log_selector_interaction(
                    tool_name="select_option",
                    selector=selector,
                    action="select",
                    selector_type="css" if "md=" in selector else "custom",
                    alternative_selectors=alternative_selectors,
                    element_attributes=element_attributes,
                    success=False,
                    error_message=f"Error: Option '{option_value}' not found in the element with selector '{selector}'.",
                )

                error = f"Error: Option '{option_value}' not found in the element with selector '{selector}'."
                return {"summary_message": error, "detailed_message": error}
    except Exception as e:
        # Initialize selector logger with proof path
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
        # Log failed selector interaction
        await selector_logger.log_selector_interaction(
            tool_name="select_option",
            selector=selector,
            action="select",
            selector_type="css" if "md=" in selector else "custom",
            success=False,
            error_message=str(e),
        )

        traceback.print_exc()
        error = f"Error selecting option in selector '{selector}'."
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}


@tool(
    agent_names=["browser_nav_agent"],
    name="bulk_select_option",
    description="used to Select an option in multiple dropdowns or spinners in single attempt.",
)
async def bulk_select_option(
    entries: Annotated[
        List[List[str]],
        "List of tuple containing 'selector' and 'value_to_fill' in [('selector', 'value_to_fill'), ..] format, selector is md attribute value of the dom element to interact, md is an ID and 'value_to_fill' is the value or text of the option to select",
    ]
) -> Annotated[
    List[Dict[str, str]],
    "List of dictionaries, each containing 'selector' and the result of the operation.",
]:
    add_event(EventType.INTERACTION, EventData(detail="BulkSelectOption"))
    results: List[Dict[str, str]] = []
    logger.info("Executing bulk select option command")
    for entry in entries:
        if len(entry) != 2:
            logger.error(f"Invalid entry format: {entry}. Expected [selector, value]")
            continue
        result = await select_option(
            (entry[0], entry[1])
        )  # Create tuple with explicit values
        results.append({"selector": entry[0], "result": result})

    return results
