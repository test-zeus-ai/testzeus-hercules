import time
from typing import Annotated, Dict, List

from playwright.sync_api import ElementHandle, Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["date_time_nav_agent"],
    description="Set a date/time value in an input field.",
    name="set_date_time_value",
)
def set_date_time_value(
    selector: Annotated[str, "CSS selector for the date/time input field."],
    input_value: Annotated[str, "Date/time value to set."],
) -> Annotated[Dict[str, str], "Result of the date/time value setting operation."]:
    """
    Set a date/time value in an input field.
    """
    function_name = "set_date_time_value"
    browser_manager = PlaywrightManager()
    page = browser_manager.get_current_page()

    try:
        browser_manager.take_screenshots(f"{function_name}_start", page)
        browser_manager.highlight_element(selector)

        result = do_set_date_time_value(page, selector, input_value)
        time.sleep(
            get_global_conf().get_delay_time()
        )  # sleep to allow the mutation observer to detect changes

        page.wait_for_load_state()
        browser_manager.take_screenshots(f"{function_name}_end", page)

        return result
    except Exception as e:
        logger.error(f"Error in {function_name}: {str(e)}")
        return {"error": str(e)}


def do_set_date_time_value(
    page: Page, selector: str, input_value: str
) -> Dict[str, str]:
    """
    Helper function to perform the actual date/time value setting.
    Example:
    ```python
    result = do_set_date_time_value(page, '#dateInput', '2023-10-10')
    ```
    """
    try:
        browser_manager = PlaywrightManager()
        element = browser_manager.find_element(selector, page)
        if not element:
            return {"error": f"Element not found with selector: {selector}"}

        # Get element type information
        tag_name = element.evaluate("el => el.tagName.toLowerCase()")
        input_type = element.evaluate("el => el.type")

        # Validate input element
        if tag_name != "input" or input_type not in ["date", "time", "datetime-local"]:
            return {
                "error": f"Element must be an input of type date/time/datetime-local. Found: {tag_name} with type {input_type}"
            }

        # Set the value
        element.fill(input_value)
        element_outer_html = get_element_outer_html(element, page)

        return {
            "status": "success",
            "message": f"Successfully set date/time value in element: {element_outer_html}",
        }
    except Exception as e:
        logger.error(f"Error in do_set_date_time_value: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["date_time_nav_agent"],
    description="Set date/time values in multiple input fields.",
    name="bulk_set_date_time_value",
)
def bulk_set_date_time_value(
    entries: Annotated[
        List[Dict[str, str]],
        "List of dictionaries containing 'selector' and 'input_value' keys.",
    ],
) -> Annotated[
    List[Dict[str, str]], "Results of the bulk date/time value setting operations."
]:
    """
    Set date/time values in multiple input fields.
    """
    results = []
    for entry in entries:
        result = set_date_time_value(entry["selector"], entry["input_value"])
        results.append(result)
    return results
