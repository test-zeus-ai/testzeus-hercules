from typing import Annotated, Dict
import time
from playwright.sync_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["drag_drop_nav_agent"],
    description="Drag and drop an element to a target location.",
    name="drag_and_drop",
)
def drag_and_drop(
    source_selector: Annotated[str, "CSS selector for the source element."],
    target_selector: Annotated[str, "CSS selector for the target element."],
    wait_before_action: Annotated[
        float, "Time to wait before dragging (in seconds)."
    ] = 0.0,
) -> Annotated[Dict[str, str], "Result of the drag and drop operation."]:
    """
    Drag and drop an element to a target location.
    """
    try:
        browser_manager = PlaywrightManager()
        page = browser_manager.get_current_page()

        # Wait before action if specified
        if wait_before_action > 0:
            time.sleep(wait_before_action)

        # Perform the drag and drop
        result = do_drag_and_drop(page, source_selector, target_selector)

        # Wait after action
        time.sleep(get_global_conf().get_delay_time())

        return result

    except Exception as e:
        logger.error(f"Error in drag_and_drop: {str(e)}")
        return {"error": str(e)}


def do_drag_and_drop(
    page: Page, source_selector: str, target_selector: str
) -> Dict[str, str]:
    """
    Helper function to perform the actual drag and drop operation.
    """
    try:
        browser_manager = PlaywrightManager()

        # Find source and target elements
        source = browser_manager.find_element(source_selector, page)
        target = browser_manager.find_element(target_selector, page)

        if not source or not target:
            return {
                "error": "Source or target element not found",
                "source_found": bool(source),
                "target_found": bool(target),
            }

        # Get source and target bounding boxes
        source_box = source.bounding_box()
        target_box = target.bounding_box()

        if not source_box or not target_box:
            return {"error": "Could not get element positions"}

        # Calculate center points
        source_x = source_box["x"] + source_box["width"] / 2
        source_y = source_box["y"] + source_box["height"] / 2
        target_x = target_box["x"] + target_box["width"] / 2
        target_y = target_box["y"] + target_box["height"] / 2

        # Perform drag and drop
        page.mouse.move(source_x, source_y)
        page.mouse.down()
        page.mouse.move(target_x, target_y)
        page.mouse.up()

        return {
            "status": "success",
            "message": f"Successfully dragged element from {source_selector} to {target_selector}",
        }

    except Exception as e:
        logger.error(f"Error in do_drag_and_drop: {str(e)}")
        return {"error": str(e)}
