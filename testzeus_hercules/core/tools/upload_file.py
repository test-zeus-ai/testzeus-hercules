import asyncio
import inspect
import traceback
from dataclasses import dataclass
from typing import Annotated, Dict, List, Tuple  # noqa: UP035

from playwright.async_api import ElementHandle, Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.browser_logger import get_browser_logger
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe, unsubscribe
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.ui_messagetype import MessageType

# Remove UploadFileEntry TypedDict class


@tool(
    agent_names=["browser_nav_agent"],
    name="click_and_upload_file",
    description="Click and Upload a file to a file input element on the page.",
)
async def click_and_upload_file(
    entry: Annotated[
        List[str],
        "tuple containing 'selector' and 'file_path' in ('selector', 'file_path') format, selector is md attribute value of the dom element to interact, md is an ID and 'file_path' is the file_path to upload",
    ]
) -> Annotated[str, "Explanation of the outcome of this operation."]:
    add_event(EventType.INTERACTION, EventData(detail="UploadFile"))
    logger.info(f"Uploading file: {entry}")

    selector: str = entry[0]
    file_path: str = entry[1]

    if "md=" not in selector:
        selector = f"[md='{selector}']"

    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        return "Error: No active page found. OpenURL command opens a new page."

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)
    await browser_manager.highlight_element(selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    subscribe(detect_dom_changes)

    result = await click_and_upload(page, selector, file_path)
    await asyncio.sleep(
        get_global_conf().get_delay_time()
    )  # sleep for 100ms to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"{result['detailed_message']}.\nAs a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of uploading file '{file_path}' is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def click_and_upload(page: Page, selector: str, file_path: str) -> dict[str, str]:
    """
    Clicks on an element and performs file upload using Playwright's FileChooser API.

    Args:
        page (Page): The Playwright Page object representing the browser tab.
        selector (str): The selector string used to locate the target element.
        file_path (str): The path to the file to upload.

    Returns:
        dict[str, str]: Explanation of the outcome represented as a dictionary with 'summary_message' and 'detailed_message'.
    """
    try:
        logger.debug(f"Looking for selector {selector} to upload file: {file_path}")

        browser_manager = PlaywrightManager()
        element = await browser_manager.find_element(
            selector, page, element_name="upload_file"
        )

        if element is None:
            # Initialize selector logger with proof path
            selector_logger = get_browser_logger(get_global_conf().get_proof_path())
            # Log failed selector interaction
            await selector_logger.log_selector_interaction(
                tool_name="upload_file",
                selector=selector,
                action="upload",
                selector_type="css" if "md=" in selector else "custom",
                success=False,
                error_message=f"Error: Selector '{selector}' not found. Unable to continue.",
            )
            error = f"Error: Selector '{selector}' not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        logger.info(f"Found selector '{selector}' to upload file")

        # Initialize selector logger with proof path
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
        # Get alternative selectors and element attributes for logging
        alternative_selectors = await selector_logger.get_alternative_selectors(
            element, page
        )
        element_attributes = await selector_logger.get_element_attributes(element)

        # Check if element is a file input
        element_type = await element.evaluate("el => el.type")
        if element_type != "file":
            # Log failed selector interaction for non-file input
            await selector_logger.log_selector_interaction(
                tool_name="upload_file",
                selector=selector,
                action="upload",
                selector_type="css" if "md=" in selector else "custom",
                alternative_selectors=alternative_selectors,
                element_attributes=element_attributes,
                success=False,
                error_message=f"Error: Element is not a file input. Found type: {element_type}",
            )
            error = f"Error: Element is not a file input. Found type: {element_type}"
            return {"summary_message": error, "detailed_message": error}

        # Use FileChooser API
        async with page.expect_file_chooser() as fc_info:
            await element.click()

        file_chooser = await fc_info.value
        await file_chooser.set_files(file_path)

        # Log successful selector interaction
        await selector_logger.log_selector_interaction(
            tool_name="upload_file",
            selector=selector,
            action="upload",
            selector_type="css" if "md=" in selector else "custom",
            alternative_selectors=alternative_selectors,
            element_attributes=element_attributes,
            success=True,
            additional_data={"file_path": file_path, "element_type": "file"},
        )

        element_outer_html = await get_element_outer_html(element, page)
        success_msg = f"Success. File '{file_path}' uploaded using the input with selector '{selector}'"
        return {
            "summary_message": success_msg,
            "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
        }

    except Exception as e:
        # Initialize selector logger with proof path
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
        # Log failed selector interaction
        await selector_logger.log_selector_interaction(
            tool_name="upload_file",
            selector=selector,
            action="upload",
            selector_type="css" if "md=" in selector else "custom",
            success=False,
            error_message=str(e),
        )

        traceback.print_exc()
        error = f"Error uploading file to selector '{selector}'."
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}


@tool(
    agent_names=["browser_nav_agent"],
    name="bulk_click_and_upload_file",
    description="Click and Uploads files to multiple file input elements on the page.",
)
async def bulk_click_and_upload_file(
    entries: Annotated[
        List[List[str]],
        "List of tuples, each containing ('selector', 'file_path'). 'selector' is the md attribute value of the DOM element to interact with (md is an ID), and 'file_path' is the path to the file to upload",
    ]
) -> Annotated[
    List[Dict[str, str]],
    "List of dictionaries, each containing 'selector' and the result of the operation.",
]:
    add_event(EventType.INTERACTION, EventData(detail="BulkUploadFile"))
    results: List[Dict[str, str]] = []
    logger.info("Executing bulk upload file command")

    for entry in entries:
        if len(entry) != 2:
            logger.error(
                f"Invalid entry format: {entry}. Expected [selector, file_path]"
            )
            continue
        result = await click_and_upload_file(entry)
        results.append({"selector": entry[0], "result": result})

    return results
