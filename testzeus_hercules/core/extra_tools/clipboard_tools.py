import asyncio
import inspect
import json
import traceback
from dataclasses import dataclass
from typing import Annotated, Any, Optional

from playwright.async_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.browser_logger import get_browser_logger
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["browser_nav_agent"],
    description="""Reads the clipboard content from the current page.
    Accepts a parameter to specify the clipboard type: 'text' for plain text
    or 'binary' for a binary object representation.""",
    name="read_clipboard",
)
async def read_clipboard(
    clipboard_type: Annotated[
        str, "Clipboard content type: 'text' or 'binary'"
    ] = "text"
) -> Annotated[Any, "Clipboard content read result"]:
    """
    Reads the clipboard content from the current page.

    Parameters:
    - clipboard_type: Specify 'text' to return plain text from the clipboard or
      'binary' to return a binary object representation (e.g., ClipboardItem data).

    Returns:
    The clipboard content. For 'text', returns a string.
    For 'binary', returns the result of navigator.clipboard.read() (typically a list of ClipboardItem objects).
    """
    logger.info(f"Executing read_clipboard with type: {clipboard_type}")
    add_event(EventType.INTERACTION, EventData(detail="read_clipboard"))

    # Initialize PlaywrightManager and get the active browser page
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()

    if page is None:
        raise ValueError("No active page found. Please open a page first.")

    if clipboard_type.lower() == "text":
        try:
            # Using navigator.clipboard.readText() to read plain text from clipboard
            content = await page.evaluate("navigator.clipboard.readText()")
            logger.info("Clipboard text successfully read.")
            return content
        except Exception as e:
            logger.error(f"Error reading clipboard text: {e}")
            traceback.print_exc()
            raise e
    elif clipboard_type.lower() == "binary":
        try:
            # navigator.clipboard.read() returns a promise that resolves to an array of ClipboardItems.
            # Note: The ClipboardItem objects might need further processing (e.g., converting Blob data to base64)
            # for your use case. Here we return the raw array.
            clipboard_items = await page.evaluate("navigator.clipboard.read()")
            logger.info("Clipboard binary data successfully read.")
            return clipboard_items
        except Exception as e:
            logger.error(f"Error reading clipboard binary data: {e}")
            traceback.print_exc()
            raise e
    else:
        error_msg = "Invalid clipboard_type. Must be 'text' or 'binary'."
        logger.error(error_msg)
        raise ValueError(error_msg)
