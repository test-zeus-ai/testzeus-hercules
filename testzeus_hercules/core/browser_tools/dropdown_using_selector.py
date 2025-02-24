from typing import Annotated, Dict, List
import time
from playwright.sync_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.browser_tools.click_using_selector import do_click
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe, unsubscribe
from testzeus_hercules.utils.js_helper import get_js_with_element_finder
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.ui_messagetype import MessageType


def custom_select_option(page: Page, selector: str, option_value: str) -> None:
    """
    Helper function to select an option in a dropdown using JavaScript.
    """
    try:
        # Get the select element
        select = page.query_selector(selector)
        if not select:
            raise ValueError(f"Select element not found with selector: {selector}")

        # Set the value using JavaScript
        result = page.evaluate(
            """
            (element, value) => {
                element.value = value;
                element.dispatchEvent(new Event('change', { bubbles: true }));
                return element.value;
            }
        """,
            select,
            option_value,
        )

        logger.debug(f"custom_select_option result: {result}")
    except Exception as e:
        logger.error(
            f"Error in custom_select_option, Selector: {selector}, Value: {option_value}. Error: {str(e)}"
        )
        raise


@tool(
    agent_names=["dropdown_nav_agent"],
    description="Select an option from a dropdown element.",
    name="select_option",
)
def select_option(
    selector: Annotated[str, "CSS selector for the dropdown element."],
    option_value: Annotated[str, "Value of the option to select."],
    wait_before_action: Annotated[
        float, "Time to wait before selecting (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the option selection operation."]:
    """
    Select an option from a dropdown element.
    """
    try:
        browser_manager = PlaywrightManager()
        page = browser_manager.get_current_page()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Select the option
        result = do_select_option(page, selector, option_value)

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        return result

    except Exception as e:
        logger.error(f"Error in select_option: {str(e)}")
        return {"error": str(e)}


def do_select_option(page: Page, selector: str, option_value: str) -> Dict[str, str]:
    """
    Helper function to perform the actual option selection operation.
    """
    try:
        browser_manager = PlaywrightManager()

        # Find the select element
        select = browser_manager.find_element(selector, page)
        if not select:
            return {"error": f"Select element not found with selector: {selector}"}

        # Select the option
        custom_select_option(page, selector, option_value)

        return {
            "status": "success",
            "message": f"Successfully selected option '{option_value}' from dropdown",
        }

    except Exception as e:
        logger.error(f"Error in do_select_option: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["dropdown_nav_agent"],
    description="Select options from multiple dropdowns.",
    name="bulk_select_option",
)
def bulk_select_option(
    entries: Annotated[
        List[Dict[str, str]],
        "List of dictionaries containing 'selector' and 'option_value' keys.",
    ],
) -> Annotated[
    List[Dict[str, str]], "Results of the bulk option selection operations."
]:
    """
    Select options from multiple dropdowns.
    """
    results = []
    for entry in entries:
        result = select_option(entry["selector"], entry["option_value"])
        results.append(result)
    return results
