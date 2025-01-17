import json
import re

from playwright.async_api import Route
from testzeus_hercules.utils.logger import logger


async def block_ads(route: Route) -> None:
    # List of ad-related keywords or domains
    ad_keywords = ["ads", "doubleclick.net", "googlesyndication", "adservice"]
    if any(keyword in route.request.url for keyword in ad_keywords):
        await route.abort()  # Block the ad request
    else:
        await route.continue_()  # Continue with other requests


def escape_js_message(message: str) -> str:
    """
    Escape a message for use in JavaScript code.

    Args:
        message (str): The message to escape.

    Returns:
        str: The escaped message.
    """
    return json.dumps(message)


def beautify_plan_message(message: str) -> str:
    """
    Add a newline between each numbered step in the plan message if it does not already exist.

    Args:
        message (str): The plan message.

    Returns:
        str: The plan message with newlines added between each numbered step.
    """
    logger.debug(f"beautify_plan_message original:\n{message}")
    # Add a newline before each numbered step that is not already preceded by a newline
    plan_with_newlines = re.sub(r"(?<!\n)( \d+\.)", r"\n\1", message)
    logger.debug(f"beautify_plan_message modified:\n{plan_with_newlines}")
    return plan_with_newlines


FIND_ELEMENT_IN_SHADOW_DOM = """
const findElementInShadowDOMAndIframes = (parent, selector) => {
    // Try to find the element in the current context
    let element = parent.querySelector(selector);
    if (element) {
        return element; // Element found in the current context
    }

    // Search inside shadow DOMs and iframes
    const elements = parent.querySelectorAll('*');
    for (const el of elements) {
        // Search inside shadow DOMs
        if (el.shadowRoot) {
            element = findElementInShadowDOMAndIframes(el.shadowRoot, selector);
            if (element) {
                return element; // Element found in shadow DOM
            }
        }
        // Search inside iframes
        if (el.tagName.toLowerCase() === 'iframe') {
            let iframeDocument;
            try {
                // Access the iframe's document if it's same-origin
                iframeDocument = el.contentDocument || el.contentWindow.document;
            } catch (e) {
                // Cannot access cross-origin iframe; skip to the next element
                continue;
            }
            if (iframeDocument) {
                element = findElementInShadowDOMAndIframes(iframeDocument, selector);
                if (element) {
                    return element; // Element found inside iframe
                }
            }
        }
    }
    return null; // Element not found
};
"""

TEMPLATES = {"FIND_ELEMENT_IN_SHADOW_DOM": FIND_ELEMENT_IN_SHADOW_DOM}


def get_js_with_element_finder(action_js_code: str) -> str:
    """
    Combines the element finder code with specific action code.

    Args:
        action_js_code: JavaScript code that uses findElementInShadowDOMAndIframes

    Returns:
        Combined JavaScript code
    """
    pattern = "/*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/"
    if pattern in action_js_code:
        return action_js_code.replace(pattern, TEMPLATES["FIND_ELEMENT_IN_SHADOW_DOM"])
    else:
        return action_js_code
