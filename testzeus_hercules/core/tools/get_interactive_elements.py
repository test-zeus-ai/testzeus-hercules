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
    description="""DOM Type dict Retrieval Tool, giving all interactive elements on page.
Notes: [Elements ordered as displayed, Consider ordinal/numbered item positions, List ordinal represent z-index on page]""",
    name="get_interactive_elements",
)
async def get_interactive_elements() -> (
    Annotated[str, "DOM type dict giving all interactive elements on page"]
):
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

    # Flatten the hierarchy into a list of elements
    def flatten_elements(
        node: dict, parent_name: str = "", parent_title: str = ""
    ) -> list[dict]:
        elements = []
        interactive_roles = {
            "button",
            "link",
            "checkbox",
            "radio",
            "textbox",
            "combobox",
            "listbox",
            "menuitem",
            "menuitemcheckbox",
            "menuitemradio",
            "option",
            "slider",
            "spinbutton",
            "switch",
            "tab",
            "treeitem",
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

        # Include elements with interactive roles or clickable/focusable elements
        if "md" in node and (
            node.get("r", "").lower() in interactive_roles
            or node.get("tag", "").lower()
            in {"a", "button", "input", "select", "textarea"}
            or node.get("clickable", False)
            or node.get("focusable", False)
        ):
            new_node = node.copy()
            new_node.pop("children", None)
            elements.append(new_node)
        return elements

    flattened_data = (
        flatten_elements(extracted_data) if isinstance(extracted_data, dict) else []
    )

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
