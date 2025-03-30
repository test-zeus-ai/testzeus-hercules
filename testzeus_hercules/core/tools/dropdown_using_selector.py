import asyncio
import inspect
import traceback
from typing import Annotated, Dict, List, Optional

from playwright.async_api import ElementHandle, Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.browser_logger import get_browser_logger
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.press_key_combination import press_key_combination
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe, unsubscribe
from testzeus_hercules.utils.logger import logger


async def select_option(
    entry: Annotated[
        tuple[str, str],
        (
            "tuple containing 'selector' and 'value_to_fill' in "
            "('selector', 'value_to_fill') format. Selector is the md attribute value "
            "of the DOM element and value_to_fill is the option text/value to select."
        ),
    ],
) -> Annotated[str, "Explanation of the outcome of dropdown/spinner selection."]:
    add_event(EventType.INTERACTION, EventData(detail="SelectOption"))
    logger.info(f"Selecting option: {entry}")
    selector: str = entry[0]
    option_value: str = entry[1]

    # If the selector doesn't contain md=, wrap it accordingly.
    if "md=" not in selector:
        selector = f"[md='{selector}']"

    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    if page is None:
        return "Error: No active page found. OpenURL command opens a new page."

    function_name = inspect.currentframe().f_code.co_name  # type: ignore
    await browser_manager.take_screenshots(f"{function_name}_start", page)
    await browser_manager.highlight_element(selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: str) -> None:
        nonlocal dom_changes_detected
        dom_changes_detected = changes

    subscribe(detect_dom_changes)
    result = await do_select_option(page, selector, option_value)
    # Wait for page to stabilize after selection
    await browser_manager.wait_for_load_state_if_enabled(page=page)
    unsubscribe(detect_dom_changes)

    await browser_manager.wait_for_load_state_if_enabled(page=page)
    await browser_manager.take_screenshots(f"{function_name}_end", page)

    # Simply return the detailed message
    return result["detailed_message"]


async def do_select_option(page: Page, selector: str, option_value: str) -> dict[str, str]:
    """
    Simplified approach to select an option in a dropdown using the element's properties.
    Uses find_element to get the element and then determines the best strategy based on
    the element's role, type, and tag name.
    """
    try:
        logger.debug(f"Looking for selector {selector} to select option: {option_value}")

        # Part 1: Find the element and get its properties
        element, properties = await find_element_select_type(page, selector)
        if not element:
            error = f"Error: Selector '{selector}' not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        # Part 2: Interact with the element to select the option
        return await interact_with_element_select_type(page, element, selector, option_value, properties)

    except Exception as e:

        traceback.print_exc()
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
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


async def find_element_select_type(page: Page, selector: str) -> tuple[Optional[ElementHandle], dict]:
    """
    Internal function to find the element and gather its properties.
    Returns the element and a dictionary of its properties.
    """
    browser_manager = PlaywrightManager()
    element = await browser_manager.find_element(selector, page, element_name="select_option")

    if not element:
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
        await selector_logger.log_selector_interaction(
            tool_name="select_option",
            selector=selector,
            action="select",
            selector_type="css" if "md=" in selector else "custom",
            success=False,
            error_message=f"Error: Selector '{selector}' not found. Unable to continue.",
        )
        return None, {}

    logger.info(f"Found selector '{selector}' to select option")
    selector_logger = get_browser_logger(get_global_conf().get_proof_path())
    alternative_selectors = await selector_logger.get_alternative_selectors(element, page)
    element_attributes = await selector_logger.get_element_attributes(element)

    # Get element properties to determine the best selection strategy
    tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
    element_role = await element.evaluate("el => el.getAttribute('role') || ''")
    element_type = await element.evaluate("el => el.type || ''")
    element_outer_html = await get_element_outer_html(element, page)

    properties = {
        "tag_name": tag_name,
        "element_role": element_role,
        "element_type": element_type,
        "element_outer_html": element_outer_html,
        "alternative_selectors": alternative_selectors,
        "element_attributes": element_attributes,
        "selector_logger": selector_logger,
    }

    return element, properties


async def interact_with_element_select_type(
    page: Page,
    element: ElementHandle,
    selector: str,
    option_value: str,
    properties: dict,
) -> dict[str, str]:
    """
    Internal function to interact with the element to select the option.
    """
    tag_name = properties["tag_name"]
    element_role = properties["element_role"]
    element_type = properties["element_type"]
    element_outer_html = properties["element_outer_html"]
    alternative_selectors = properties["alternative_selectors"]
    element_attributes = properties["element_attributes"]
    selector_logger = properties["selector_logger"]

    # Strategy 1: Standard HTML select element
    if tag_name == "select":
        await element.select_option(value=option_value)
        await page.wait_for_load_state("domcontentloaded", timeout=1000)
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

    # Strategy 2: Input elements (text, number, etc.)
    elif tag_name in ["input", "button"]:
        input_roles = ["combobox", "listbox", "dropdown", "spinner", "select"]
        input_types = [
            "number",
            "range",
            "combobox",
            "listbox",
            "dropdown",
            "spinner",
            "select",
            "option",
        ]

        if element_type in input_types or element_role in input_roles:
            await element.click()
            try:
                await element.fill(option_value)
            except Exception as e:

                # traceback.print_exc()
                logger.warning(f"Error filling input: {str(e)}, trying type instead")
                await element.type(option_value)

            if "lwc" in str(element) and "placeholder" in str(element):
                logger.info("Crazy LWC element detected")
                await asyncio.sleep(0.5)
                await press_key_combination("ArrowDown+Enter")
            else:
                await element.press("Enter")

            await page.wait_for_load_state("domcontentloaded", timeout=1000)

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
                    "input_type": element_type,
                    "value": option_value,
                },
            )
            success_msg = f"Success. Value '{option_value}' set in the input with selector '{selector}'"
            return {
                "summary_message": success_msg,
                "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
            }

    # Strategy 3: Generic click and select approach for all other elements
    # Click to open the dropdown

    logger.info(f"taking worst case scenario of selecting option for {element}, properties: {properties}")
    await element.click()
    await page.wait_for_timeout(300)  # Short wait for dropdown to appear

    # Try to find and click the option by text content
    try:
        # Use a simple text-based selector that works in most cases
        option_selector = f"text={option_value}"
        await page.click(option_selector, timeout=2000)
        await page.wait_for_load_state("domcontentloaded", timeout=1000)

        await selector_logger.log_selector_interaction(
            tool_name="select_option",
            selector=selector,
            action="click_by_text",
            selector_type="css" if "md=" in selector else "custom",
            alternative_selectors=alternative_selectors,
            element_attributes=element_attributes,
            success=True,
            additional_data={
                "element_type": tag_name,
                "selected_value": option_value,
                "method": "text_content",
            },
        )
        success_msg = f"Success. Option '{option_value}' selected by text content"
        return {
            "summary_message": success_msg,
            "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
        }
    except Exception as e:

        traceback.print_exc()
        logger.debug(f"Text-based selection failed: {str(e)}")

        # If all attempts fail, report failure
        await selector_logger.log_selector_interaction(
            tool_name="select_option",
            selector=selector,
            action="select",
            selector_type="css" if "md=" in selector else "custom",
            alternative_selectors=alternative_selectors,
            element_attributes=element_attributes,
            success=False,
            error_message=f"Could not find option '{option_value}' in the dropdown with any selection method.",
        )
        error = f"Error: Option '{option_value}' not found in the element with selector '{selector}'. Try clicking the element first and then select the option."
        return {"summary_message": error, "detailed_message": error}


@tool(
    agent_names=["browser_nav_agent"],
    name="bulk_select_option",
    description=("Used to select/search an options in multiple picklist/listbox/combobox/dropdowns/spinners in a single attempt. " "Each entry is a tuple of (selector, value_to_fill)."),
)
async def bulk_select_option(
    entries: Annotated[
        List[List[str]],
        (
            "List of tuples containing 'selector' and 'value_to_fill' in the format "
            "[('selector', 'value_to_fill'), ...]. 'selector' is the md attribute value and 'value_to_fill' is the option to select."
        ),
    ],
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
        result = await select_option((entry[0], entry[1]))
        if isinstance(result, str):
            if "new elements have appeared in view" in result and "success" in result.lower():
                success_part = result.split(".\nAs a consequence")[0]
                results.append({"selector": entry[0], "result": success_part})
            else:
                results.append({"selector": entry[0], "result": result})
        else:
            results.append({"selector": entry[0], "result": str(result)})
    return results
