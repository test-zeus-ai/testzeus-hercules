import asyncio
import inspect
import traceback
from dataclasses import dataclass
from typing import List  # noqa: UP035
from typing import Annotated

from playwright.async_api import ElementHandle, Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe, unsubscribe
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.ui_messagetype import MessageType

# Remove UploadFileEntry TypedDict class


async def upload_file(
    entry: Annotated[
        dict,
        "dict containing 'query_selector' (selector query using md attribute e.g. [md='114'] md is ID) and 'file_path' (the path to the file to upload).",
    ]
) -> Annotated[str, "Explanation of the outcome of this operation."]:
    """
    Uploads a file to a file input element identified by a selector.

    This function uploads the specified file to a file input element identified by the given selector.
    It uses the Playwright library to interact with the browser.

    Args:
        entry (dict[str, str]): An dict containing'query_selector' (selector query using md attribute)
                                 and 'file_path' (the path to the file to upload).

    Returns:
        str: Explanation of the outcome of this operation.

    Example:
        entry = {"query_selector": '#fileInput', "file_path": '/path/to/file.txt'}
        result = await upload_file(entry)
    """
    add_event(EventType.INTERACTION, EventData(detail="UploadFile"))
    logger.info(f"Uploading file: {entry}")
    query_selector: str = entry["query_selector"]
    file_path: str = entry["file_path"]

    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        return "Error: No active page found. OpenURL command opens a new page."

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)

    await browser_manager.highlight_element(query_selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    subscribe(detect_dom_changes)

    result = await do_upload_file(page, query_selector, file_path)
    await asyncio.sleep(get_global_conf().get_delay_time())  # sleep for 100ms to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"{result['detailed_message']}.\nAs a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of uploading file '{file_path}' is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def do_upload_file(page: Page, selector: str, file_path: str) -> dict[str, str]:
    """
    Performs the file upload operation on a file input element.

    This function uploads the specified file to a file input element identified by the given selector.
    It handles elements within the regular DOM, Shadow DOM, and iframes.

    Args:
        page (Page): The Playwright Page object representing the browser tab.
        selector (str): The selector string used to locate the target element.
        file_path (str): The path to the file to upload.

    Returns:
        dict[str, str]: Explanation of the outcome of this operation represented as a dictionary with 'summary_message' and 'detailed_message'.

    Example:
        result = await do_upload_file(page, '#fileInput', '/path/to/file.txt')
    """
    try:
        logger.debug(f"Looking for selector {selector} to upload file: {file_path}")

        browser_manager = PlaywrightManager()
        element = await browser_manager.find_element(selector, page)

        if element is None:
            error = f"Error: Selector '{selector}' not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        logger.info(f"Found selector '{selector}' to upload file")

        # Get the element's tag name and type to determine how to interact with it
        tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
        input_type = await element.evaluate("el => el.type")

        if tag_name == "input" and input_type == "file":
            # For file inputs, set the files directly
            await element.set_input_files(file_path)
            element_outer_html = await get_element_outer_html(element, page)
            success_msg = f"Success. File '{file_path}' uploaded using the input with selector '{selector}'"
            return {
                "summary_message": success_msg,
                "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
            }
        else:
            error = f"Error: Element with selector '{selector}' is not a file input."
            return {"summary_message": error, "detailed_message": error}

    except Exception as e:
        traceback.print_exc()
        error = f"Error uploading file to selector '{selector}'."
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}


@tool(
    agent_names=["browser_nav_agent"],
    name="bulk_upload_file",
    description="Uploads files to multiple file input elements using a bulk operation. ALL TOOL ARGUMENTS ARE MANDATORY.",
)
async def bulk_upload_file(
    entries: Annotated[
        List[dict],
        "List of dictionaries, each containing 'query_selector' and 'file_path'. dict containing 'query_selector' (selector query using md attribute e.g. [md='114'] md is ID) and 'value' (the value or text of the option to select). MANDATORY FIELD",
    ]  # noqa: UP006
) -> Annotated[
    List[dict],
    "List of dictionaries, each containing 'query_selector' and the result of the operation.",
]:  # noqa: UP006
    """
    Uploads files to multiple file input elements using a bulk operation.

    This function uploads files to multiple file input elements using a bulk operation.
    It takes a list of dictionaries, where each dictionary contains a 'query_selector' and 'file_path' pair.
    The function internally calls the 'upload_file' function to perform the operation for each entry.

    Args:
        entries: List of dictionaries, each containing 'query_selector' and 'file_path'.

    Returns:
        List of dictionaries, each containing 'query_selector' and the result of the operation.

    Example:
        entries = [
            {"query_selector": "#fileInput1", "file_path": "/path/to/file1.txt"},
            {"query_selector": "#fileInput2", "file_path": "/path/to/file2.jpg"}
        ]
        results = await bulk_upload_file(entries)
    """
    add_event(EventType.INTERACTION, EventData(detail="BulkUploadFile"))
    results: List[dict[str, str]] = []  # noqa: UP006
    logger.info("Executing bulk upload file command")
    for entry in entries:
        query_selector = entry["query_selector"]
        file_path = entry["file_path"]
        logger.info(f"Uploading file: '{file_path}' to element with selector: '{query_selector}'")
        result = await upload_file(entry)  # Use dictionary directly

        results.append({"query_selector": query_selector, "result": result})

    return results
