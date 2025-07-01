import asyncio
import inspect
import traceback
from typing import Annotated, Any

from playwright.async_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.browser_logger import get_browser_logger
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe, unsubscribe
from testzeus_hercules.utils.logger import logger

page_data_store = {}


# Function to set data
def set_page_data(page: Any, data: Any) -> None:
    page_data_store[page] = data


# Function to get data
def get_page_data(page: Any) -> dict[str, Any]:
    data = page_data_store.get(page)
    return data if data is not None else {}


@tool(
    agent_names=["browser_nav_agent"],
    description="""Clicks element by md attribute or other selectors. Returns success/failure status. Supports dual-mode operation.""",
    name="click",
)
async def click(
    selector: Annotated[str, """selector using md attribute (agent mode) or CSS/XPath selector (code mode), just give the md ID value or standard selector"""],
    user_input_dialog_response: Annotated[str, "Dialog input value"] = "",
    expected_message_of_dialog: Annotated[str, "Expected dialog message"] = "",
    action_on_dialog: Annotated[str, "Dialog action: 'DISMISS' or 'ACCEPT'"] = "",
    type_of_click: Annotated[str, "Click type: click/right_click/double_click/middle_click"] = "click",
    wait_before_execution: Annotated[float, "Wait time before click"] = 0.0,
    mode: Annotated[str, "Operation mode: 'agent' (default) or 'code'"] = "agent",
) -> Annotated[str, "Click action result"]:
    query_selector = selector

    if mode == "agent" and "md=" not in query_selector and not query_selector.startswith("[") and not query_selector.startswith("/"):
        query_selector = f"[md='{query_selector}']"

    logger.info(f'Executing ClickElement with "{query_selector}" as the selector')
    add_event(EventType.INTERACTION, EventData(detail="click"))
    # Initialize PlaywrightManager and get the active browser page
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    # await page.route("**/*", block_ads)
    action_on_dialog = action_on_dialog.lower() if action_on_dialog else ""
    type_of_click = type_of_click.lower() if type_of_click else "click"

    async def handle_dialog(dialog: Any) -> None:
        try:
            await asyncio.sleep(0.5)
            data = get_page_data(page)
            user_input_dialog_response = data.get("user_input_dialog_response", "")
            expected_message_of_dialog = data.get("expected_message_of_dialog", "")
            action_on_dialog = data.get("action_on_dialog", "")
            if action_on_dialog:
                action_on_dialog = action_on_dialog.lower().strip()
            dialog_message = dialog.message if dialog.message is not None else ""
            logger.info(f"Dialog message: {dialog_message}")

            # Check if the dialog message matches the expected message (if provided)
            if expected_message_of_dialog and dialog_message != expected_message_of_dialog:
                logger.error(f"Dialog message does not match the expected message: {expected_message_of_dialog}")
                if action_on_dialog == "accept":
                    if dialog.type == "prompt":
                        await dialog.accept(user_input_dialog_response)
                    else:
                        await dialog.accept()
                elif action_on_dialog == "dismiss":
                    await dialog.dismiss()
                else:
                    await dialog.dismiss()  # Dismiss if the dialog message doesn't match
            elif user_input_dialog_response:
                await dialog.accept(user_input_dialog_response)
            else:
                await dialog.dismiss()

        except Exception as e:

            traceback.print_exc()
            logger.info(f"Error handling dialog: {e}")

    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)

    await browser_manager.highlight_element(query_selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    subscribe(detect_dom_changes)
    set_page_data(
        page,
        {
            "user_input_dialog_response": user_input_dialog_response,
            "expected_message_of_dialog": expected_message_of_dialog,
            "action_on_dialog": action_on_dialog,
            "type_of_click": type_of_click,
        },
    )

    page = await browser_manager.get_current_page()
    page.on("dialog", handle_dialog)
    result = await do_click(page, query_selector, wait_before_execution, type_of_click)

    await asyncio.sleep(get_global_conf().get_delay_time())  # sleep to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)

    await browser_manager.wait_for_load_state_if_enabled(page=page)

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"Success: {result['summary_message']}.\n As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action to click {query_selector} is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def do_click(page: Page, selector: str, wait_before_execution: float, type_of_click: str) -> dict[str, str]:
    logger.info(f'Executing ClickElement with "{selector}" as the selector. Wait time before execution: {wait_before_execution} seconds.')

    # Wait before execution if specified
    if wait_before_execution > 0:
        await asyncio.sleep(wait_before_execution)

    # Wait for the selector to be present and ensure it's attached and visible. If timeout, try JavaScript click
    try:
        logger.info(f'Executing ClickElement with "{selector}" as the selector. Waiting for the element to be attached and visible.')

        # Attempt to find the element on the main page or in iframes
        browser_manager = PlaywrightManager()
        element = await browser_manager.find_element(selector, page, element_name="click")
        if element is None:
            # Initialize selector logger with proof path
            selector_logger = get_browser_logger(get_global_conf().get_proof_path())
            # Log failed selector interaction
            await selector_logger.log_selector_interaction(
                tool_name="click",
                selector=selector,
                action=type_of_click,
                selector_type="css" if "md=" in selector else "custom",
                success=False,
                error_message=f'Element with selector: "{selector}" not found',
            )
            raise ValueError(f'Element with selector: "{selector}" not found')

        logger.info(f'Element with selector: "{selector}" is attached. Scrolling it into view if needed.')
        try:
            await element.scroll_into_view_if_needed(timeout=2000)
            logger.info(f'Element with selector: "{selector}" is attached and scrolled into view. Waiting for the element to be visible.')
        except Exception as e:

            traceback.print_exc()
            logger.exception(f"Error scrolling element into view: {e}")
            # If scrollIntoView fails, just move on, not a big deal
            pass

        if not await element.is_visible():
            return {
                "summary_message": f'Element with selector: "{selector}" is not visible, Try another element',
                "detailed_message": f'Element with selector: "{selector}" is not visible, Try another element',
            }

        element_tag_name = await element.evaluate("element => element.tagName.toLowerCase()")
        element_outer_html = await get_element_outer_html(element, page, element_tag_name)

        # Initialize selector logger with proof path
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
        # Get alternative selectors and element attributes for logging
        alternative_selectors = await selector_logger.get_alternative_selectors(element, page)
        element_attributes = await selector_logger.get_element_attributes(element)

        # hack for aura component in salesforce
        element_title = (await element.get_attribute("title") or "").lower()
        if "upload" in element_title:
            return {
                "summary_message": "Use the click_and_upload_file tool to upload files",
                "detailed_message": "Use the click_and_upload_file tool to upload files",
            }

        if element_tag_name == "option":
            element_value = await element.get_attribute("value")
            parent_element = await element.evaluate_handle("element => element.parentNode")
            await parent_element.select_option(value=element_value)  # type: ignore

            # Log successful selector interaction for option selection
            await selector_logger.log_selector_interaction(
                tool_name="click",
                selector=selector,
                action="select_option",
                selector_type="css" if "md=" in selector else "custom",
                alternative_selectors=alternative_selectors,
                element_attributes=element_attributes,
                success=True,
                additional_data={"selected_value": element_value},
            )

            logger.info(f'Select menu option "{element_value}" selected')

            return {
                "summary_message": f'Select menu option "{element_value}" selected',
                "detailed_message": f'Select menu option "{element_value}" selected. The select element\'s outer HTML is: {element_outer_html}.',
            }

        input_type = await element.evaluate("(el) => el.type")

        # Determine if it's checkable
        if element_tag_name == "input" and input_type in ["radio"]:
            await element.check()
            msg = f'Checked element with selector: "{selector}"'
        elif element_tag_name == "input" and input_type in ["checkbox"]:
            await element.type(" ")
            msg = f'Checked element with selector: "{selector}"'
        else:
            # Perform the click based on the type_of_click
            if type_of_click == "right_click":
                await element.click(button="right")
                msg = f'Right-clicked element with selector: "{selector}"'
            elif type_of_click == "double_click":
                await element.dblclick()
                msg = f'Double-clicked element with selector: "{selector}"'
            elif type_of_click == "middle_click":
                await element.click(button="middle")
                msg = f'Middle-clicked element with selector: "{selector}"'
            else:  # Default to regular click
                await element.click()
                msg = f'Clicked element with selector: "{selector}"'

        # Log successful selector interaction
        await selector_logger.log_selector_interaction(
            tool_name="click",
            selector=selector,
            action=type_of_click,
            selector_type="css" if "md=" in selector else "custom",
            alternative_selectors=alternative_selectors,
            element_attributes=element_attributes,
            success=True,
            additional_data={"click_type": type_of_click},
        )

        return {
            "summary_message": msg,
            "detailed_message": f"{msg} The clicked element's outer HTML is: {element_outer_html}.",
        }  # type: ignore
    except Exception as e:
        # Try a JavaScript fallback click before giving up

        traceback.print_exc()
        try:
            logger.info(f'Standard click failed for "{selector}". Attempting JavaScript fallback click.')

            msg = await browser_manager.perform_javascript_click(page, selector, type_of_click)

            if msg:
                # Initialize selector logger with proof path
                selector_logger = get_browser_logger(get_global_conf().get_proof_path())
                # Log successful JavaScript fallback click
                await selector_logger.log_selector_interaction(
                    tool_name="click",
                    selector=selector,
                    action=f"js_fallback_{type_of_click}",
                    selector_type="css" if "md=" in selector else "custom",
                    success=True,
                    additional_data={"click_type": "javascript_fallback"},
                )

                return {
                    "summary_message": msg,
                    "detailed_message": f"{msg}.",
                }
        except Exception as js_error:

            traceback.print_exc()
            logger.error(f"JavaScript fallback click also failed: {js_error}")
            # Both standard and fallback methods failed, proceed with original error handling
            pass

        # Initialize selector logger with proof path
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
        # Log failed selector interaction
        await selector_logger.log_selector_interaction(
            tool_name="click",
            selector=selector,
            action=type_of_click,
            selector_type="css" if "md=" in selector else "custom",
            success=False,
            error_message=str(e),
        )

        logger.error(f'Unable to click element with selector: "{selector}". Error: {e}')
        traceback.print_exc()
        msg = f'Unable to click element with selector: "{selector}" since the selector is invalid. Proceed by retrieving DOM again.'
        return {"summary_message": msg, "detailed_message": f"{msg}. Error: {e}"}
