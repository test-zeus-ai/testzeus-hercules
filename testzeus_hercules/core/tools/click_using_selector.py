import asyncio
import inspect
import json
import traceback
from dataclasses import dataclass
from typing import Annotated, Any, Optional

from playwright.async_api import ElementHandle, Page
from testzeus_hercules.config import get_global_conf  # Add this import
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe  # type: ignore
from testzeus_hercules.utils.dom_mutation_observer import unsubscribe  # type: ignore
from testzeus_hercules.utils.js_helper import block_ads, get_js_with_element_finder
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.ui_messagetype import MessageType

page_data_store = {}


# Function to set data
def set_page_data(page: Any, data: Any) -> None:
    page_data_store[page] = data


# Function to get data
def get_page_data(page: Any) -> dict:
    return page_data_store.get(page)


@tool(agent_names=["browser_nav_agent"], description="""Clicks element by md attribute. Returns success/failure status. ALL TOOL ARGUMENTS ARE MANDATORY""", name="click")
async def click(
    selector: Annotated[str, """selector using md attribute, eg: "[md='114']" md is ID"""],
    user_input_dialog_response: Annotated[str, "Dialog input value"] = "",
    expected_message_of_dialog: Annotated[str, "Expected dialog message"] = "",
    action_on_dialog: Annotated[str, "Dialog action: 'DISMISS' or 'ACCEPT'"] = "",
    type_of_click: Annotated[str, "Click type: click/right_click/double_click/middle_click"] = "click",
    wait_before_execution: Annotated[float, "Wait time before click"] = 0.0,
) -> Annotated[str, "Click action result"]:
    """
    Executes a click action on the element matching the given query selector string within the currently open web page.
    If there is no page open, it will raise a ValueError. An optional wait time can be specified before executing the click logic. Use this to wait for the page to load especially when the last action caused the DOM/Page to load.

    Parameters:
    - selector: The query selector string to identify the element for the click action.
    - user_input_dialog_response: The input response to a dialog box.
    - expected_message_of_dialog: The expected message of the dialog box when it opens.
    - action_on_dialog: The action to be performed on the dialog box. Only 'accept' or 'dismiss' are allowed.
    - type_of_click: The type of click to perform. Possible values are 'click' (default), 'right_click', 'double_click', 'middle_click'.
    - wait_before_execution: Optional wait time in seconds before executing the click event logic. Defaults to 0.0 seconds.

    Returns:
    - Success if the click was successful, appropriate error message otherwise.
    """
    query_selector = selector

    if "md=" not in query_selector:
        query_selector = f"[md='{query_selector}']"

    logger.info(f'Executing ClickElement with "{query_selector}" as the selector')
    add_event(EventType.INTERACTION, EventData(detail="click"))
    # Initialize PlaywrightManager and get the active browser page
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    # await page.route("**/*", block_ads)
    action_on_dialog = action_on_dialog.lower() if action_on_dialog else None
    type_of_click = type_of_click.lower() if type_of_click else "click"

    async def handle_dialog(dialog: Any) -> None:
        try:
            await asyncio.sleep(0.5)
            data = get_page_data(page)
            user_input_dialog_response = data.get("user_input_dialog_response")
            expected_message_of_dialog = data.get("expected_message_of_dialog")
            action_on_dialog = data.get("action_on_dialog", "")
            if action_on_dialog:
                action_on_dialog = action_on_dialog.lower().strip()
            logger.info(f"Dialog message: {dialog.message}")

            # Check if the dialog message matches the expected message (if provided)
            if expected_message_of_dialog and dialog.message != expected_message_of_dialog:
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
            logger.info(f"Error handling dialog: {e}")
            # await dialog.accept(user_input_dialog_response)

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

    await page.wait_for_load_state()
    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"Success: {result['summary_message']}.\n As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action to click {query_selector} is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def do_click(page: Page, selector: str, wait_before_execution: float, type_of_click: str) -> dict[str, str]:
    """
    Executes the click action on the element with the given selector within the provided page,
    including searching within iframes if necessary.

    Parameters:
    - page: The Playwright page instance.
    - selector: The query selector string to identify the element for the click action.
    - wait_before_execution: Optional wait time in seconds before executing the click event logic.
    - type_of_click: The type of click to perform.

    Returns:
    dict[str,str] - Explanation of the outcome of this operation represented as a dictionary with 'summary_message' and 'detailed_message'.
    """
    logger.info(f'Executing ClickElement with "{selector}" as the selector. Wait time before execution: {wait_before_execution} seconds.')

    # Wait before execution if specified
    if wait_before_execution > 0:
        await asyncio.sleep(wait_before_execution)

    # Wait for the selector to be present and ensure it's attached and visible. If timeout, try JavaScript click
    try:
        logger.info(f'Executing ClickElement with "{selector}" as the selector. Waiting for the element to be attached and visible.')

        # Attempt to find the element on the main page or in iframes
        browser_manager = PlaywrightManager()
        element = await browser_manager.find_element(selector, page)
        if element is None:
            raise ValueError(f'Element with selector: "{selector}" not found')

        logger.info(f'Element with selector: "{selector}" is attached. Scrolling it into view if needed.')
        try:
            await element.scroll_into_view_if_needed(timeout=200)
            logger.info(f'Element with selector: "{selector}" is attached and scrolled into view. Waiting for the element to be visible.')
        except Exception:
            # If scrollIntoView fails, just move on, not a big deal
            pass

        try:
            await element.wait_for_element_state("visible", timeout=200)
            logger.info(f'Executing ClickElement with "{selector}" as the selector. Element is attached and visible. Clicking the element.')
        except Exception:
            # If the element is not visible, try to click it anyway
            pass

        element_tag_name = await element.evaluate("element => element.tagName.toLowerCase()")
        element_outer_html = await get_element_outer_html(element, page, element_tag_name)

        if element_tag_name == "option":
            element_value = await element.get_attribute("value")
            parent_element = await element.evaluate_handle("element => element.parentNode")
            await parent_element.select_option(value=element_value)  # type: ignore

            logger.info(f'Select menu option "{element_value}" selected')

            return {
                "summary_message": f'Select menu option "{element_value}" selected',
                "detailed_message": f'Select menu option "{element_value}" selected. The select element\'s outer HTML is: {element_outer_html}.',
            }

        msg = await browser_manager.perform_javascript_click(page, selector, type_of_click)
        return {
            "summary_message": msg,
            "detailed_message": f"{msg} The clicked element's outer HTML is: {element_outer_html}.",
        }  # type: ignore
    except Exception as e:
        logger.error(f'Unable to click element with selector: "{selector}". Error: {e}')
        traceback.print_exc()
        msg = f'Unable to click element with selector: "{selector}" since the selector is invalid. Proceed by retrieving DOM again.'
        return {"summary_message": msg, "detailed_message": f"{msg}. Error: {e}"}
