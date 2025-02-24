import time
from typing import Optional, Union, cast

from playwright.sync_api import ElementHandle, Page
from testzeus_hercules.utils.logger import logger


def wait_for_non_loading_dom_state(page: Page, timeout: int = 5) -> None:
    """
    Wait for the DOM to reach a non-loading state.
    This includes waiting for network idle and no visible loading indicators.
    """
    try:
        # Wait for network idle
        page.wait_for_load_state("networkidle", timeout=timeout * 1000)

        # Wait for any loading spinners to disappear
        loading_selectors = [
            "[class*='loading']",
            "[class*='spinner']",
            "[id*='loading']",
            "[id*='spinner']",
            "[aria-busy='true']",
        ]

        for selector in loading_selectors:
            try:
                page.wait_for_selector(selector, state="hidden", timeout=timeout * 1000)
            except:
                # Ignore if selector not found
                pass

    except Exception as e:
        logger.warning(f"Error waiting for non-loading DOM state: {e}")


def get_element_outer_html(element: ElementHandle, page: Page) -> str:
    """
    Get the outer HTML of an element.
    """
    try:
        # First try to get the outerHTML directly
        outer_html = element.evaluate("element => element.outerHTML")
        if outer_html:
            return outer_html

        # If that fails, try to get the tag name and build a simple representation
        tag_name = element.evaluate("element => element.tagName.toLowerCase()")
        return f"<{tag_name}></{tag_name}>"

    except Exception as e:
        logger.error(f"Error getting element outer HTML: {e}")
        return "Element HTML not available"


def get_element_attribute(element: ElementHandle, attr: str) -> Optional[str]:
    """
    Get an attribute value from an element.
    """
    try:
        value: str = element.get_attribute(attr)  # type: ignore
        return value
    except Exception as e:
        logger.error(f"Error getting element attribute {attr}: {e}")
        return None
