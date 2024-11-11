import json
import re

from testzeus_hercules.utils.logger import logger
from playwright.async_api import Route


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
