import time
from typing import Annotated, Dict, List, Optional

from playwright.sync_api import ElementHandle, Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["file_upload_nav_agent"],
    description="Click on a file input element and upload a file.",
    name="click_and_upload_file",
)
def click_and_upload_file(
    selector: Annotated[str, "CSS selector for the file input element."],
    file_path: Annotated[str, "Path to the file to upload."],
) -> Annotated[Dict[str, str], "Result of the file upload operation."]:
    """
    Click on a file input element and upload a file.
    """
    function_name = "click_and_upload_file"
    browser_manager = PlaywrightManager()
    page = browser_manager.get_current_page()

    try:
        browser_manager.take_screenshots(f"{function_name}_start", page)
        browser_manager.highlight_element(selector)

        result = click_and_upload(page, selector, file_path)
        time.sleep(
            get_global_conf().get_delay_time()
        )  # sleep to allow the mutation observer to detect changes

        # Wait for any network activity to complete
        page.wait_for_load_state("networkidle")
        browser_manager.take_screenshots(f"{function_name}_end", page)

        return result
    except Exception as e:
        logger.error(f"Error in {function_name}: {str(e)}")
        return {"error": str(e)}


def click_and_upload(page: Page, selector: str, file_path: str) -> Dict[str, str]:
    """
    Helper function to perform the actual file upload.
    Example:
    ```python
    result = click_and_upload(page, '#file-input', '/path/to/file.txt')
    ```
    """
    try:
        browser_manager = PlaywrightManager()
        element = browser_manager.find_element(selector, page)
        if not element:
            return {"error": f"Element not found with selector: {selector}"}

        # Get element HTML before upload for logging
        element_outer_html = get_element_outer_html(element, page)

        # Set up file chooser handling
        with page.expect_file_chooser() as fc_info:
            element.click()

        file_chooser = fc_info.value
        file_chooser.set_files(file_path)

        return {
            "success": True,
            "message": f"File uploaded successfully to element: {element_outer_html}",
        }
    except Exception as e:
        logger.error(f"Error in click_and_upload: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["file_upload_nav_agent"],
    description="Upload files to multiple file input elements.",
    name="bulk_click_and_upload_file",
)
def bulk_click_and_upload_file(
    entries: Annotated[
        List[Dict[str, str]],
        "List of dictionaries containing 'selector' and 'file_path' keys.",
    ],
) -> Annotated[List[Dict[str, str]], "Results of the bulk file upload operations."]:
    """
    Upload files to multiple file input elements.
    """
    results = []
    for entry in entries:
        result = click_and_upload_file(entry["selector"], entry["file_path"])
        results.append(result)
    return results
