import json
import os
import time
from typing import Annotated, Dict, List, Optional, Union

from playwright.sync_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import wait_for_non_loading_dom_state
from testzeus_hercules.utils.get_detailed_accessibility_tree import (
    do_get_accessibility_info,
    rename_children,
)
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["input_field_nav_agent"],
    description="Get information about all input fields on the current page.",
    name="get_input_fields",
)
def get_input_fields() -> (
    Annotated[Dict[str, Dict], "Information about input fields on the page"]
):
    """
    Get information about all input fields on the current page.
    """
    try:
        browser_manager = PlaywrightManager()
        browser_manager.wait_for_page_and_frames_load()
        page = browser_manager.get_current_page()
        page.wait_for_load_state()

        # Wait for any dynamic content to load
        wait_for_non_loading_dom_state(page, 1)

        # Get input field information
        extracted_data = do_get_accessibility_info(page, only_input_fields=True)

        return extracted_data
    except Exception as e:
        logger.error(f"Error getting input fields: {str(e)}")
        return {"error": str(e)}


def do_get_accessibility_info(
    page: Page, only_input_fields: bool = False
) -> Dict[str, Dict]:
    """
    Get accessibility information from the page.
    """
    js_code = """
    (onlyInputFields) => {
        function getAccessibilityInfo(element, index) {
            const info = {
                tag: element.tagName.toLowerCase(),
                type: element.type || '',
                value: element.value || '',
                placeholder: element.placeholder || '',
                'aria-label': element.getAttribute('aria-label') || '',
                role: element.getAttribute('role') || '',
                name: element.name || '',
                id: element.id || '',
                class: element.className || '',
                disabled: element.disabled || false,
                required: element.required || false,
                readOnly: element.readOnly || false,
                maxLength: element.maxLength || '',
                pattern: element.pattern || '',
                autocomplete: element.autocomplete || '',
                checked: element.checked || false,
                selected: element.selected || false,
                multiple: element.multiple || false,
                size: element.size || '',
                step: element.step || '',
                min: element.min || '',
                max: element.max || '',
                'aria-required': element.getAttribute('aria-required') || '',
                'aria-invalid': element.getAttribute('aria-invalid') || '',
                'aria-expanded': element.getAttribute('aria-expanded') || '',
                'aria-haspopup': element.getAttribute('aria-haspopup') || '',
                'aria-controls': element.getAttribute('aria-controls') || '',
                'aria-owns': element.getAttribute('aria-owns') || '',
                'aria-describedby': element.getAttribute('aria-describedby') || '',
                'data-testid': element.getAttribute('data-testid') || '',
            };

            // Add computed styles
            const computedStyle = window.getComputedStyle(element);
            info.styles = {
                display: computedStyle.display,
                visibility: computedStyle.visibility,
                position: computedStyle.position,
                width: computedStyle.width,
                height: computedStyle.height,
                backgroundColor: computedStyle.backgroundColor,
                color: computedStyle.color,
                fontSize: computedStyle.fontSize,
                fontFamily: computedStyle.fontFamily,
                border: computedStyle.border,
                padding: computedStyle.padding,
                margin: computedStyle.margin,
                opacity: computedStyle.opacity,
            };

            return info;
        }

        const elements = onlyInputFields
            ? document.querySelectorAll('input, select, textarea, button')
            : document.querySelectorAll('*');

        const result = {};
        elements.forEach((element, index) => {
            result[index] = getAccessibilityInfo(element, index);
        });

        return result;
    }
    """
    extracted_data = page.evaluate(js_code, only_input_fields)
    return extracted_data
