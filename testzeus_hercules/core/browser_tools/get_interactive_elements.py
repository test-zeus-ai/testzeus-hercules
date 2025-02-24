import json
import os
import time
from typing import Annotated, Any, Union, Dict, List

from playwright.sync_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import wait_for_non_loading_dom_state
from testzeus_hercules.utils.get_detailed_accessibility_tree import (
    do_get_accessibility_info,
    rename_children,
)
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["dom_nav_agent"],
    description="Get all interactive elements on the page.",
    name="get_interactive_elements",
)
def get_interactive_elements() -> (
    Annotated[Dict[str, List[Dict[str, str]]], "Interactive elements on the page."]
):
    """
    Get all interactive elements on the page.
    """
    try:
        browser_manager = PlaywrightManager()
        page = browser_manager.get_current_page()

        # Get all interactive elements
        result = do_get_interactive_elements(page)

        return result

    except Exception as e:
        logger.error(f"Error in get_interactive_elements: {str(e)}")
        return {"error": str(e)}


def do_get_interactive_elements(page: Page) -> Dict[str, List[Dict[str, str]]]:
    """
    Helper function to get all interactive elements on the page.
    """
    try:
        # Define selectors for interactive elements
        selectors = {
            "buttons": "button, input[type='button'], input[type='submit']",
            "links": "a[href]",
            "inputs": "input[type='text'], input[type='password'], input[type='email'], input[type='number']",
            "dropdowns": "select",
            "checkboxes": "input[type='checkbox']",
            "radio_buttons": "input[type='radio']",
            "textareas": "textarea",
        }

        # Get elements for each type
        elements = {}
        for element_type, selector in selectors.items():
            elements[element_type] = []
            found_elements = page.query_selector_all(selector)

            for element in found_elements:
                # Get element properties
                element_info = {
                    "tag": element.evaluate("el => el.tagName.toLowerCase()"),
                    "type": element.evaluate("el => el.type") or "",
                    "id": element.evaluate("el => el.id") or "",
                    "name": element.evaluate("el => el.name") or "",
                    "value": element.evaluate("el => el.value") or "",
                    "text": element.inner_text() or "",
                }

                # Add href for links
                if element_type == "links":
                    element_info["href"] = element.evaluate("el => el.href") or ""

                elements[element_type].append(element_info)

        return {
            "status": "success",
            "elements": elements,
        }

    except Exception as e:
        logger.error(f"Error in do_get_interactive_elements: {str(e)}")
        return {"error": str(e)}
