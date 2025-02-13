import json
import os
import time
from typing import Annotated, Any, Union

from playwright.async_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import wait_for_non_loading_dom_state
from testzeus_hercules.utils.get_detailed_accessibility_tree import (
    do_get_accessibility_info,
    rename_children,
)
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["browser_nav_agent"],
    description="""DOM Type dict Retrieval Tool, giving all interactive elements on page.
Notes: [Elements ordered as displayed, Consider ordinal/numbered item positions, List ordinal represent z-index on page]""",
    name="get_interactive_elements",
)
async def get_interactive_elements() -> Annotated[str, "DOM type dict giving all interactive elements on page"]:
    add_event(EventType.INTERACTION, EventData(detail="get_interactive_elements"))
    start_time = time.time()
    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager()
    await browser_manager.wait_for_page_and_frames_load()
    page = await browser_manager.get_current_page()
    await page.wait_for_load_state()
    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    extracted_data = ""
    await wait_for_non_loading_dom_state(page, 1)

    extracted_data = await do_get_accessibility_info(page, only_input_fields=False)

    elapsed_time = time.time() - start_time
    logger.info(f"Get DOM Command executed in {elapsed_time} seconds")

    # Count elements
    rr = 0
    if isinstance(extracted_data, (dict, list)):
        rr = len(extracted_data)
    add_event(
        EventType.DETECTION,
        EventData(detail=f"DETECTED {rr} components"),
    )

    if isinstance(extracted_data, dict):
        extracted_data = await rename_children(extracted_data)

    extracted_data = json.dumps(extracted_data, separators=(",", ":"))
    extracted_data_legend = """Key legend:
t: tag
r: role
c: children
n: name
tl: title
Dict >>
"""
    extracted_data = extracted_data_legend + extracted_data
    return extracted_data or "Its Empty, try something else"  # type: ignore
