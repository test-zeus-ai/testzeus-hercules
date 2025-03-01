import asyncio
import inspect
import traceback
from dataclasses import dataclass
from typing import Annotated, List, Tuple, Dict

from playwright.async_api import ElementHandle, Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.browser_logger import get_browser_logger
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe, unsubscribe
from testzeus_hercules.utils.js_helper import get_js_with_element_finder
from testzeus_hercules.utils.logger import logger


async def select_option(
    entry: Annotated[
        tuple[str, str],
        (
            "tuple containing 'selector' and 'value_to_fill' in "
            "('selector', 'value_to_fill') format. Selector is the md attribute value "
            "of the DOM element and value_to_fill is the option text/value to select."
        ),
    ]
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
    # Wait to allow mutation observers to detect any UI changes
    await asyncio.sleep(get_global_conf().get_delay_time())
    unsubscribe(detect_dom_changes)

    await browser_manager.wait_for_load_state_if_enabled(page=page)

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    # Simply return the detailed message
    return result["detailed_message"]


async def select_option_lightning(
    page: Page, element: ElementHandle, option_value: str
) -> bool:
    """
    Specialized approach for selecting an option in Salesforce Lightning dropdowns.
    Uses Playwright's built-in APIs for finding and clicking options.
    """
    try:
        # Log basic info about the element
        tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
        logger.debug(f"Lightning element: tag={tag_name}")

        # Click to open the dropdown
        await element.click()
        await asyncio.sleep(0.5)  # Wait for dropdown to open

        # Try Salesforce specific selectors
        for selector in [
            ".slds-listbox__option",
            ".slds-dropdown__item",
            "lightning-base-combobox-item",
        ]:
            option_locator = page.locator(selector).filter(has_text=option_value)
            if await option_locator.count() > 0:
                await option_locator.click()
                logger.info(
                    f"Successfully selected option '{option_value}' using selector {selector}"
                )
                return True

        # If none of the specific selectors worked, try any visible elements containing the text
        option_locator = page.locator(
            "xpath=//span[contains(text(), '" + option_value + "')]"
        )
        if await option_locator.count() > 0:
            await option_locator.click()
            logger.info(
                f"Successfully selected option '{option_value}' using text content"
            )
            return True

        logger.warning(
            f"Could not find option '{option_value}' using Lightning selectors"
        )
        return False

    except Exception as e:
        logger.error(f"Error in Lightning selection: {str(e)}")
        traceback.print_exc()
        return False


async def do_select_option(
    page: Page, selector: str, option_value: str
) -> dict[str, str]:
    """
    Performs option selection on a dropdown or spinner using Playwright's APIs.
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
            selector_logger = get_browser_logger(get_global_conf().get_proof_path())
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
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
        alternative_selectors = await selector_logger.get_alternative_selectors(
            element, page
        )
        element_attributes = await selector_logger.get_element_attributes(element)
        tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
        element_outer_html = await get_element_outer_html(element, page)

        # Handle standard HTML select element
        if tag_name == "select":
            # Use built-in functionality for select elements
            # Use built-in functionality for select elements
            await element.select_option(value=option_value)
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

        # Handle input types
        elif tag_name == "input":
            input_type = await element.evaluate("el => el.type")
            input_role = await element.evaluate("el => el.role")
            if input_type in [
                "number",
                "range",
                "combobox",
                "listbox",
                "dropdown",
                "spinner",
                "select",
                "option",
            ] or input_role in [
                "combobox",
                "listbox",
                "dropdown",
                "spinner",
                "select",
            ]:
                await element.click()
                await element.fill(option_value)

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

        # For all other element types (custom dropdowns, Lightning components, etc.)
        # Click to open dropdown

        await element.click()
        await asyncio.sleep(1)  # Wait for dropdown to open

        # Try using role-based selectors (most reliable approach)
        try:
            # First try option role
            option_locator = page.get_by_role("option", name=option_value)
            if await option_locator.count() > 0:
                await option_locator.click()
                await selector_logger.log_selector_interaction(
                    tool_name="select_option",
                    selector=selector,
                    action="select_role_based",
                    selector_type="css" if "md=" in selector else "custom",
                    alternative_selectors=alternative_selectors,
                    element_attributes=element_attributes,
                    success=True,
                    additional_data={
                        "element_type": tag_name,
                        "selected_value": option_value,
                        "method": "role_option",
                    },
                )
                success_msg = f"Success. Option '{option_value}' selected in the dropdown with selector '{selector}' using role-based selection"
                return {
                    "summary_message": success_msg,
                    "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
                }

            # Try with contains text
            option_locator = page.locator('[role="option"]').filter(
                has_text=option_value
            )
            if await option_locator.count() > 0:
                await option_locator.click()
                await selector_logger.log_selector_interaction(
                    tool_name="select_option",
                    selector=selector,
                    action="select_role_based",
                    selector_type="css" if "md=" in selector else "custom",
                    alternative_selectors=alternative_selectors,
                    element_attributes=element_attributes,
                    success=True,
                    additional_data={
                        "element_type": tag_name,
                        "selected_value": option_value,
                        "method": "role_option_text",
                    },
                )
                success_msg = f"Success. Option '{option_value}' selected in the dropdown with selector '{selector}' using role-based text selection"
                return {
                    "summary_message": success_msg,
                    "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
                }

            # Try other roles
            for role in ["listitem", "menuitem"]:
                if role == "listitem":
                    option_locator = page.get_by_role("listitem", name=option_value)
                else:
                    option_locator = page.get_by_role("menuitem", name=option_value)

                if await option_locator.count() > 0:
                    await option_locator.click()
                    await selector_logger.log_selector_interaction(
                        tool_name="select_option",
                        selector=selector,
                        action="select_role_based",
                        selector_type="css" if "md=" in selector else "custom",
                        alternative_selectors=alternative_selectors,
                        element_attributes=element_attributes,
                        success=True,
                        additional_data={
                            "element_type": tag_name,
                            "selected_value": option_value,
                            "method": f"role_{role}",
                        },
                    )
                    success_msg = f"Success. Option '{option_value}' selected in the dropdown with selector '{selector}' using {role} role"
                    return {
                        "summary_message": success_msg,
                        "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
                    }
        except Exception as role_error:
            logger.debug(f"Role-based selection failed: {str(role_error)}")

        # For Lightning components specifically
        is_lightning = await page.evaluate(
            """(selector) => {
                const el = document.querySelector(selector);
                if (!el) return false;
                return el.classList.contains('slds-combobox__input') || 
                       document.querySelector('.slds-dropdown, lightning-base-combobox') !== null ||
                       window.location.href.includes('lightning/') ||
                       window.location.href.includes('force.com');
            }""",
            selector,
        )

        if is_lightning:
            lightning_success = await select_option_lightning(
                page, element, option_value
            )
            if lightning_success:
                await selector_logger.log_selector_interaction(
                    tool_name="select_option",
                    selector=selector,
                    action="select_lightning",
                    selector_type="css" if "md=" in selector else "custom",
                    alternative_selectors=alternative_selectors,
                    element_attributes=element_attributes,
                    success=True,
                    additional_data={
                        "element_type": "lightning",
                        "selected_value": option_value,
                        "method": "lightning_specialized",
                    },
                )
                success_msg = f"Success. Option '{option_value}' selected in the Lightning component with selector '{selector}'"
                return {
                    "summary_message": success_msg,
                    "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
                }

        # Final fallback: Try any element with matching text
        try:
            # Try text-based selectors as last resort
            for selector in ["li", ".dropdown-item", "[data-value]", "span", "div"]:
                option_locator = page.locator(selector).filter(has_text=option_value)
                if (
                    await option_locator.count() > 0
                    and await option_locator.is_visible()
                ):
                    await option_locator.click()
                    await selector_logger.log_selector_interaction(
                        tool_name="select_option",
                        selector=selector,
                        action="select_text_based",
                        selector_type="css" if "md=" in selector else "custom",
                        alternative_selectors=alternative_selectors,
                        element_attributes=element_attributes,
                        success=True,
                        additional_data={
                            "element_type": tag_name,
                            "selected_value": option_value,
                            "method": "text_based",
                        },
                    )
                    success_msg = f"Success. Option '{option_value}' selected by text content in element with selector '{selector}'"
                    return {
                        "summary_message": success_msg,
                        "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
                    }
        except Exception as text_error:
            logger.debug(f"Text-based selection failed: {str(text_error)}")

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
        error = f"Error: Option '{option_value}' not found in the element with selector '{selector}' using any selection method. TRY CLICKING THE ELEMENT FIRST AND RETRY."
        return {"summary_message": error, "detailed_message": error}

    except Exception as e:
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


@tool(
    agent_names=["browser_nav_agent"],
    name="bulk_select_option",
    description=(
        "Used to select an option in multiple picklist/listbox/combobox/dropdowns/spinners in a single attempt. "
        "Each entry is a tuple of (selector, value_to_fill)."
    ),
)
async def bulk_select_option(
    entries: Annotated[
        List[List[str]],
        (
            "List of tuples containing 'selector' and 'value_to_fill' in the format "
            "[('selector', 'value_to_fill'), ...]. 'selector' is the md attribute value and 'value_to_fill' is the option to select."
        ),
    ]
) -> Annotated[
    List[Dict[str, str]],
    "List of dictionaries, each containing 'selector' and the result of the operation.",
]:
    add_event(EventType.INTERACTION, EventData(detail="BulkSelectOption"))
    results: List[Dict[str, str]] = []
    logger.info("Executing bulk select option command")

    # Remove nested normalization as it caused linter errors
    # Process entries exactly as received

    for entry in entries:
        if len(entry) != 2:
            logger.error(f"Invalid entry format: {entry}. Expected [selector, value]")
            continue
        result = await select_option((entry[0], entry[1]))
        # First ensure result is a string before performing string operations
        if isinstance(result, str):
            if (
                "new elements have appeared in view" in result
                and isinstance(
                    result, str
                )  # Ensure result is a string before calling lower()
                and "success" in result.lower()
            ):
                success_part = result.split(".\nAs a consequence")[0]
                results.append({"selector": entry[0], "result": success_part})
            else:
                results.append({"selector": entry[0], "result": result})
        else:
            # Handle non-string results (like lists or dictionaries)
            results.append({"selector": entry[0], "result": str(result)})
    return results
