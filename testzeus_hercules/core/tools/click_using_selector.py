import time
from typing import Annotated, Dict, List, Optional, Union

from playwright.sync_api import Dialog, ElementHandle, Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["click_nav_agent"],
    description="Click on an element using a selector.",
    name="click",
)
def click(
    selector: Annotated[str, "CSS selector for the element to click."],
    wait_before_execution: Annotated[
        float, "Time to wait before clicking (in seconds)."
    ] = 0.0,
    type_of_click: Annotated[
        str, "Type of click action (click, dblclick, etc.)."
    ] = "click",
    user_input_dialog_response: Annotated[
        str, "Response to any dialog that appears."
    ] = "",
) -> Annotated[Dict[str, str], "Result of the click operation."]:
    """
    Click on an element using a selector.
    """
    function_name = "click"
    browser_manager = PlaywrightManager()
    page = browser_manager.get_current_page()

    def handle_dialog(dialog: Dialog) -> None:
        """Handle any dialogs that appear during the click operation."""
        time.sleep(0.5)  # Small delay to ensure dialog is fully loaded
        dialog_message = dialog.message.lower()

        # Handle different types of dialogs
        if "confirm" in dialog_message:
            if user_input_dialog_response:
                dialog.accept(user_input_dialog_response)
            else:
                dialog.accept()
        elif "alert" in dialog_message:
            dialog.dismiss()
        elif "prompt" in dialog_message:
            if user_input_dialog_response:
                dialog.accept(user_input_dialog_response)
            else:
                dialog.dismiss()
        else:
            dialog.dismiss()  # Dismiss if the dialog message doesn't match

    try:
        browser_manager.take_screenshots(f"{function_name}_start", page)
        browser_manager.highlight_element(selector)

        # Set up dialog handler
        page.on("dialog", handle_dialog)

        result = do_click(page, selector, wait_before_execution, type_of_click)
        time.sleep(
            get_global_conf().get_delay_time()
        )  # sleep to allow the mutation observer to detect changes

        # Wait for any network activity to complete
        page.wait_for_load_state("networkidle")
        browser_manager.take_screenshots(f"{function_name}_end", page)

        return result
    except Exception as e:
        logger.error(f"Error in {function_name}: {str(e)}")
        return {"error": str(e)}
    finally:
        # Remove dialog handler
        page.remove_listener("dialog", handle_dialog)


def do_click(
    page: Page, selector: str, wait_before_execution: float, type_of_click: str
) -> Dict[str, str]:
    """
    Helper function to perform the actual click operation.
    Example:
    ```python
    result = do_click(page, '#submit-button', 0.5, 'click')
    ```
    """
    try:
        # Wait before execution if specified
        if wait_before_execution > 0:
            time.sleep(wait_before_execution)

        browser_manager = PlaywrightManager()
        element = browser_manager.find_element(selector, page)
        if not element:
            return {"error": f"Element not found with selector: {selector}"}

        # Ensure element is visible and scrolled into view
        element.scroll_into_view_if_needed(timeout=200)
        element.wait_for_element_state("visible", timeout=200)

        # Get element HTML before click for logging
        element_outer_html = get_element_outer_html(element, page)

        # Perform the click action
        if type_of_click == "dblclick":
            element.dblclick()
        elif type_of_click == "click":
            element.click()
        elif type_of_click == "right_click":
            element.click(button="right")
        else:
            return {"error": f"Unsupported click type: {type_of_click}"}

        return {
            "success": True,
            "message": f"Successfully performed {type_of_click} on element: {element_outer_html}",
        }
    except Exception as e:
        logger.error(f"Error in do_click: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["click_nav_agent"],
    description="Click on multiple elements using selectors.",
    name="bulk_click",
)
def bulk_click(
    entries: Annotated[
        List[Dict[str, str]],
        "List of dictionaries containing 'selector' and optional 'type_of_click' keys.",
    ],
) -> Annotated[List[Dict[str, str]], "Results of the bulk click operations."]:
    """
    Click on multiple elements using selectors.
    """
    results = []
    for entry in entries:
        type_of_click = entry.get("type_of_click", "click")
        result = click(entry["selector"], type_of_click=type_of_click)
        results.append(result)
    return results
