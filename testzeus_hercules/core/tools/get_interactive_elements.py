import json
import os
import time
from typing import Annotated, Any, Callable, Dict, List, Union

from playwright.async_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.browser_logger import get_browser_logger
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import wait_for_non_loading_dom_state
from testzeus_hercules.utils.get_detailed_accessibility_tree import (
    do_get_accessibility_info,
    rename_children,
)
from testzeus_hercules.utils.logger import logger

INTERACTIVE_ROLES = frozenset(
    {
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
)
INTERACTIVE_TAGS = frozenset({"a", "button", "input", "select", "textarea"})
# get_input_fields: HTML form elements, plus ARIA widgets that act as form controls without being
# one (<div role="switch">, <div role="checkbox">, custom comboboxes ...).
FORM_FIELD_TAGS = frozenset({"input", "label", "select", "textarea", "button", "fieldset", "legend", "datalist", "output", "option", "optgroup"})
FORM_FIELD_ROLES = frozenset({"checkbox", "radio", "switch", "textbox", "searchbox", "combobox", "listbox", "slider", "spinbutton", "option", "menuitemcheckbox", "menuitemradio"})


def _node_role(node: dict) -> str:
    # The role arrives under the full "role" key, or the short "r" key after key shortening.
    return str(node.get("role") or node.get("r") or "").lower()


def is_interactive_node(node: dict) -> bool:
    """Elements get_interactive_elements lists: interactive roles or tags, or clickable/focusable nodes."""
    return _node_role(node) in INTERACTIVE_ROLES or str(node.get("tag", "")).lower() in INTERACTIVE_TAGS or bool(node.get("clickable", False)) or bool(node.get("focusable", False))


def is_form_field_node(node: dict) -> bool:
    """Elements get_input_fields lists: HTML form elements and ARIA form widgets."""
    return str(node.get("tag", "")).lower() in FORM_FIELD_TAGS or _node_role(node) in FORM_FIELD_ROLES


def flatten_elements(node: dict, parent_name: str = "", parent_title: str = "", *, include: Callable[[dict], bool] = is_interactive_node, compact: bool = True) -> list[dict]:
    """Flatten the tree into the list of elements ``include`` selects (children before parents).

    A child without a name/title inherits its parent's. ``compact`` trims each node to the keys an agent
    needs; get_input_fields keeps the full node (it also reports ids and test attributes).
    """
    elements: list[dict] = []

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
            elements.extend(flatten_elements(child, current_name, current_title, include=include, compact=compact))

    if "md" in node and include(node):
        new_node = node.copy()
        new_node.pop("children", None)
        elements.append(compact_interactive_node(new_node) if compact else new_node)
    return elements


def compact_value(value: Any) -> Any:
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        if len(cleaned) > 300:
            return f"{cleaned[:300]}...[truncated]"
        return cleaned
    if isinstance(value, list):
        return [compact_value(item) for item in value[:30]]
    if isinstance(value, dict):
        return {str(key): compact_value(item_value) for key, item_value in value.items() if item_value not in ("", None, [], {})}
    return value


def compact_interactive_node(node: dict) -> dict:
    allowed_keys = (
        "md",
        "tag",
        "role",
        "r",
        "name",
        "title",
        "description",
        "text",
        "aria-label",
        "value",
        "tag_type",
        "type",
        "placeholder",
        "tooltip",
        "clickable",
        "focusable",
        "checked",
        "selected",
        "field_name",
        "disabled",
        "expanded",
        "level",
        "options",
        "additional_info",
    )
    return {key: compact_value(node[key]) for key in allowed_keys if key in node and node[key] not in ("", None, [], {})}


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

    await browser_manager.wait_for_load_state_if_enabled(page=page)

    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    extracted_data = ""
    await wait_for_non_loading_dom_state(page, 1)

    extracted_data = await do_get_accessibility_info(page, only_input_fields=False)

    flattened_data = flatten_elements(extracted_data) if isinstance(extracted_data, dict) else []

    elapsed_time = time.time() - start_time
    logger.info(f"Get DOM Command executed in {elapsed_time} seconds")

    # Count elements
    rr = 0
    if isinstance(flattened_data, list):
        rr = len(flattened_data)
    add_event(
        EventType.DETECTION,
        EventData(detail=f"DETECTED {rr} components"),
    )

    #     if isinstance(extracted_data, dict):
    #         extracted_data = await rename_children(extracted_data)

    #     extracted_data = json.dumps(extracted_data, separators=(",", ":"))
    #     extracted_data_legend = """Key legend:
    # t: tag
    # r: role
    # c: children
    # n: name
    # tl: title
    # Dict >>
    # """
    #     extracted_data = extracted_data_legend + extracted_data
    extracted_data = json.dumps(flattened_data, separators=(",", ":"))
    return extracted_data or "Its Empty, try something else"  # type: ignore
