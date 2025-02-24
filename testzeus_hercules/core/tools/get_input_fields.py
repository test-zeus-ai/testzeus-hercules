import json
import os
import time
from typing import Annotated, Any, Dict, List, Union

from playwright.async_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.browser_logger import get_browser_logger
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
    description="""DOM Type dict Retrieval Tool, giving only html input types elements on page.
Notes: [Elements ordered as displayed, Consider ordinal/numbered item positions]""",
    name="get_input_fields",
)
async def get_input_fields() -> (
    Annotated[str, "DOM type dict giving all input elements on page"]
):

    add_event(EventType.INTERACTION, EventData(detail="get_input_fields"))
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
    logger.debug("Fetching DOM for input_fields")
    extracted_data = await do_get_accessibility_info(page, only_input_fields=True)
    if extracted_data is None:
        return "Could not fetch input fields. Please consider trying with content_type all_fields."

    # Flatten the hierarchy into a list of elements
    def flatten_elements(
        node: dict, parent_name: str = "", parent_title: str = ""
    ) -> list[dict]:
        elements = []
        form_elements = {
            "input",
            "label",
            "select",
            "textarea",
            "button",
            "fieldset",
            "legend",
            "datalist",
            "output",
            "option",
            "optgroup",
        }

        if "children" in node:
            # Get current node's name and title for passing to children
            current_name = node.get("name", parent_name)
            current_title = node.get("title", parent_title)

            for child in node["children"]:
                # If child doesn't have name/title, it will use parent's values
                if "name" not in child and current_name:
                    child["name"] = current_name
                if "title" not in child and current_title:
                    child["title"] = current_title
                elements.extend(flatten_elements(child, current_name, current_title))

        if "md" in node and node.get("tag", "").lower() in form_elements:
            new_node = node.copy()
            new_node.pop("children", None)
            elements.append(new_node)
        return elements

    extracted_data = flatten_elements(extracted_data)

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

    if isinstance(extracted_data, list):
        for i, item in enumerate(extracted_data):
            extracted_data[i] = await rename_children(item)

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
