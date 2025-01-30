import asyncio
from typing import Annotated

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


@tool(agent_names=["browser_nav_agent"], description="Performs drag and drop operation between source (using md) and target (using any valid selector).", name="drag_and_drop")
async def drag_and_drop(
    source_selector: Annotated[str, "Source element selector using md attribute, eg: [md='114']"],
    target_selector: Annotated[str, "Target element selector using any valid Playwright selector (CSS, XPath, etc.)"],
    wait_before_execution: Annotated[float, "Wait time before drag and drop"] = 0.0,
) -> Annotated[str, "Drag and drop operation result"]:
    """
    Executes a drag and drop operation from source element to target element.

    Parameters:
    - source_selector: The selector for the element to be dragged (must use md attribute)
    - target_selector: The selector for the drop target element (any valid Playwright selector)
    - wait_before_execution: Optional wait time in seconds before executing the operation

    Returns:
    - Success message if the operation was successful, error message otherwise
    """
    query_selector = source_selector
    if "md=" not in query_selector:
        query_selector = f"[md='{query_selector}']"

    logger.info(f"Executing drag and drop from '{query_selector}' to '{target_selector}'")
    add_event(EventType.INTERACTION, EventData(detail="drag_and_drop"))

    # Initialize PlaywrightManager and get the active browser page
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()

    if page is None:
        raise ValueError("No active page found. OpenURL command opens a new page.")

    try:
        # Wait before execution if specified
        if wait_before_execution > 0:
            await asyncio.sleep(wait_before_execution)

        # Find source using md selector
        source_element = await browser_manager.find_element(query_selector, page)
        if source_element is None:
            raise ValueError(f"Source element with selector: '{query_selector}' not found")

        # Find target using multiple selector strategies
        target_element = None
        selectors_to_try = []

        # Build list of selectors to try
        if target_selector.startswith("#"):
            # If it's an ID selector, try text-based alternatives
            base_text = target_selector.replace("#", "")
            selectors_to_try = [
                target_selector,  # Original ID selector
                f":text('{base_text}')",  # Any element with exact text
                f"*:has-text('{base_text}')",  # Any element containing text
                f"[aria-label='{base_text}']",  # Aria label match
                f"[title='{base_text}']",  # Title attribute match
            ]
        elif "has-text" in target_selector:
            # If it's a text selector, try ID and other text-based selectors
            base_text = target_selector.split("'")[1]  # Extract text between quotes
            selectors_to_try = [
                target_selector,  # Original text selector
                f"#{base_text}",  # ID selector
                f":text('{base_text}')",  # Any element with exact text
                f"[aria-label='{base_text}']",  # Aria label match
                f"[title='{base_text}']",  # Title attribute match
            ]
        else:
            # For any other selector, try as is
            selectors_to_try = [target_selector]

        # Try each selector
        for selector in selectors_to_try:
            try:
                element = await page.wait_for_selector(selector, timeout=2000)
                if element:
                    target_element = element
                    logger.info(f"Found target element using selector: {selector}")
                    break
            except Exception:
                continue

        if target_element is None:
            raise ValueError(f"Target element not found using any of these selectors: {selectors_to_try}")

        # Ensure elements are visible and get their positions
        try:
            await source_element.scroll_into_view_if_needed()
            await target_element.scroll_into_view_if_needed()
            await asyncio.sleep(0.2)  # Wait for scroll to complete

            source_box = await source_element.bounding_box()
            target_box = await target_element.bounding_box()

            if not source_box or not target_box:
                raise ValueError("Unable to determine element positions")

            # Calculate center points
            source_x = source_box["x"] + source_box["width"] / 2
            source_y = source_box["y"] + source_box["height"] / 2
            target_x = target_box["x"] + target_box["width"] / 2
            target_y = target_box["y"] + target_box["height"] / 2

            # Perform drag and drop with precise mouse movements
            # 1. Move to source and start drag
            await page.mouse.move(source_x, source_y)
            await asyncio.sleep(0.3)
            await page.mouse.down()
            await asyncio.sleep(0.3)

            # 2. Drag movement with increased steps and slower motion
            steps = 20  # More steps for smoother movement
            for i in range(1, steps + 1):
                current_x = source_x + (target_x - source_x) * (i / steps)
                current_y = source_y + (target_y - source_y) * (i / steps)
                await page.mouse.move(current_x, current_y)
                await asyncio.sleep(0.1)  # Slower movement

            # 3. Ensure we're over target and hold briefly
            await page.mouse.move(target_x, target_y)
            await asyncio.sleep(0.5)  # Longer hover over target

            # 4. Release drop
            await page.mouse.up()

            # Wait for animations and DOM updates
            await asyncio.sleep(get_global_conf().get_delay_time())

            return f"Successfully performed drag and drop from '{query_selector}' to '{target_selector}'"

        except Exception as e:
            error_msg = f"Failed to perform drag and drop: {str(e)}"
            logger.error(error_msg)
            return error_msg

    except Exception as e:
        error_msg = f"Failed to perform drag and drop operation: {str(e)}"
        logger.error(error_msg)
        return error_msg
