from typing import Annotated, Dict, List
import time
from playwright.sync_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger


def custom_set_slider_value(page: Page, selector: str, value_to_set: float) -> None:
    """
    Sets the value of a range slider input element to a specified value.
    """
    try:
        # Get the slider element
        slider = page.query_selector(selector)
        if not slider:
            raise ValueError(f"Slider element not found with selector: {selector}")

        # Set the value using JavaScript
        result = page.evaluate(
            """
            (element, value) => {
                element.value = value;
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
                return element.value;
            }
        """,
            slider,
            value_to_set,
        )

        logger.debug(f"custom_set_slider_value result: {result}")
    except Exception as e:
        logger.error(
            f"Error in custom_set_slider_value, Selector: {selector}, Value: {value_to_set}. Error: {str(e)}"
        )
        raise


@tool(
    agent_names=["slider_nav_agent"],
    description="Set the value of a slider element.",
    name="setslider",
)
def setslider(
    selector: Annotated[str, "CSS selector for the slider element."],
    value_to_set: Annotated[float, "Value to set the slider to."],
    wait_before_action: Annotated[
        float, "Time to wait before setting value (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the slider value setting operation."]:
    """
    Set the value of a slider element.
    """
    try:
        browser_manager = PlaywrightManager()
        page = browser_manager.get_current_page()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Set the slider value
        result = do_setslider(page, selector, value_to_set)

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        return result

    except Exception as e:
        logger.error(f"Error in setslider: {str(e)}")
        return {"error": str(e)}


def do_setslider(page: Page, selector: str, value_to_set: float) -> Dict[str, str]:
    """
    Helper function to perform the actual slider value setting operation.
    """
    try:
        browser_manager = PlaywrightManager()

        # Find the slider element
        slider = browser_manager.find_element(selector, page)
        if not slider:
            return {"error": f"Slider element not found with selector: {selector}"}

        # Set the slider value
        custom_set_slider_value(page, selector, value_to_set)

        return {
            "status": "success",
            "message": f"Successfully set slider value to {value_to_set}",
        }

    except Exception as e:
        logger.error(f"Error in do_setslider: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["slider_nav_agent"],
    description="Set values for multiple sliders.",
    name="bulk_set_slider",
)
def bulk_set_slider(
    entries: Annotated[
        List[Dict[str, float]],
        "List of dictionaries containing 'selector' and 'value' keys.",
    ],
) -> Annotated[
    List[Dict[str, str]], "Results of the bulk slider value setting operations."
]:
    """
    Set values for multiple sliders.
    """
    results = []
    for entry in entries:
        result = setslider(entry["selector"], entry["value"])
        results.append(result)
    return results
