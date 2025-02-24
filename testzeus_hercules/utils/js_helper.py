import json
import re
from typing import Any, Dict, Optional

from playwright.sync_api import Route
from testzeus_hercules.utils.logger import logger


def block_ads(route: Route) -> None:
    """
    Block ad-related requests.
    """
    try:
        # List of common ad-related domains
        ad_domains = ["google-analytics.com", "doubleclick.net", "adnxs.com"]
        if any(domain in route.request.url for domain in ad_domains):
            route.abort()  # Block the ad request
        else:
            route.continue_()  # Continue with other requests
    except Exception as e:
        logger.error(f"Error in block_ads: {e}")
        route.continue_()  # Continue in case of error


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


def get_js_with_element_finder(js_code: str) -> str:
    """
    Inject element finder function into JavaScript code.
    """
    element_finder_js = """
    function findElementInShadowDOMAndIframes(root, selector) {
        // Try to find in the current document first
        let element = root.querySelector(selector);
        if (element) {
            return element;
        }

        // Search in shadow roots
        const allElements = root.querySelectorAll('*');
        for (const elem of allElements) {
            if (elem.shadowRoot) {
                element = findElementInShadowDOMAndIframes(elem.shadowRoot, selector);
                if (element) {
                    return element;
                }
            }
        }

        // Search in iframes
        const iframes = root.querySelectorAll('iframe');
        for (const iframe of iframes) {
            try {
                const iframeDoc = iframe.contentDocument;
                if (iframeDoc) {
                    element = findElementInShadowDOMAndIframes(iframeDoc, selector);
                    if (element) {
                        return element;
                    }
                }
            } catch (err) {
                console.log('Error accessing iframe:', err);
            }
        }

        return null;
    }
    """

    return js_code.replace("/*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/", element_finder_js)
