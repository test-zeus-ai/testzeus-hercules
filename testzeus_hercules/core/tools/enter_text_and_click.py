import asyncio
import inspect
from typing import Annotated, Any, Optional

from testzeus_hercules.config import get_global_conf  # Add this import
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.prompts import LLM_PROMPTS
from testzeus_hercules.core.tools.click_using_selector import do_click
from testzeus_hercules.core.tools.enter_text_using_selector import do_entertext
from testzeus_hercules.core.tools.press_key_combination import do_press_key_combination

# Add imports
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

page_data_store = {}


# Function to set data
def set_page_data(page: Any, data: dict) -> None:
    page_data_store[page] = data


# Function to get data
def get_page_data(page: Any) -> dict:
    return page_data_store.get(page)


# @tool(
#     agent_names=["browser_nav_agent"],
#     description="""
# Text Entry and Click Tool

#    Purpose: Combines text entry and element clicking using DOM selectors.
#    Advantages:
#    - Superior to separate commands
#    - Streamlined operation
#    - Better performance


#    Operation:
#    - Enters text in specified element
#    - Clicks target element
#    - Returns success/failure status ALL TOOL ARGUMENTS ARE MANDATORY
# """,
#     name="enter_text_and_click"
# )
async def enter_text_and_click(
    text_selector: Annotated[str, "Text input selector"],
    text_to_enter: Annotated[str, "Text to enter"],
    click_selector: Annotated[str, "Click target selector"],
    user_input_dialog_response: Annotated[str, "Dialog input value"] = "",
    expected_message_of_dialog: Annotated[str, "Expected dialog message"] = "",
    action_on_dialog: Annotated[str, "Dialog action: DISMISS/ACCEPT"] = "",
    wait_before_click_execution: Annotated[float, "Wait time before click"] = 0.0,
) -> Annotated[str, "Operation result"]:
    """
    Enters text into an element and then clicks on another element.

    Parameters:
    - text_selector: The selector for the element to enter text into. It should be a properly formatted selector query, for example [md='1234'], where the text will be entered. Use the md attribute.
    - text_to_enter: The text to enter into the element specified by text_selector.
    - click_selector: The selector for the element to click. It should be a properly formatted selector query, for example [md='1234'].
    - wait_before_click_execution: Optional wait time in seconds before executing the click action. Default is 0.0.

    Returns:
    - A message indicating the success or failure of the text entry and click.

    Raises:
    - ValueError: If no active page is found. The OpenURL command opens a new page.

    Example usage:
    ```
    await enter_text_and_click("[md='1234']", "Hello, World!", "[md='5678']", wait_before_click_execution=1.5)
    ```
    """
    if "md=" not in text_selector:
        text_selector = f"[md='{text_selector}']"
    logger.info(f"Entering text '{text_to_enter}' into element with selector '{text_selector}' and then clicking element with selector '{click_selector}'.")
    add_event(EventType.INTERACTION, EventData(detail="enter_text_and_click"))
    # Initialize PlaywrightManager and get the active browser page
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    # await page.route("**/*", block_ads)
    action_on_dialog = action_on_dialog.lower() if action_on_dialog else None

    async def handle_dialog(dialog: Any) -> None:
        try:
            await asyncio.sleep(0.5)
            data = get_page_data(page)
            user_input_dialog_response = data.get("user_input_dialog_response")
            expected_message_of_dialog = data.get("expected_message_of_dialog")
            action_on_dialog = data.get("action_on_dialog", "")
            if action_on_dialog:
                action_on_dialog = action_on_dialog.lower().strip()
            logger.info(f"Dialog message: {dialog.message}")

            # Check if the dialog message matches the expected message (if provided)
            if expected_message_of_dialog and dialog.message != expected_message_of_dialog:
                logger.error(f"Dialog message does not match the expected message: {expected_message_of_dialog}")
                if action_on_dialog == "accept":
                    if dialog.type == "prompt":
                        await dialog.accept(user_input_dialog_response)
                    else:
                        await dialog.accept()
                elif action_on_dialog == "dismiss":
                    await dialog.dismiss()
                else:
                    await dialog.dismiss()  # Dismiss if the dialog message doesn't match
            elif user_input_dialog_response:
                await dialog.accept(user_input_dialog_response)
            else:
                await dialog.dismiss()

        except Exception as e:
            logger.info(f"Error handling dialog: {e}")
            # await dialog.accept(user_input_dialog_response)

    if page is None:  # type: ignore
        logger.error("No active page found")
        raise ValueError("No active page found. OpenURL command opens a new page.")

    await browser_manager.highlight_element(text_selector)

    function_name = inspect.currentframe().f_code.co_name  # type: ignore
    await browser_manager.take_screenshots(f"{function_name}_start", page)

    text_entry_result = await do_entertext(page, text_selector, text_to_enter, use_keyboard_fill=True)
    await page.wait_for_load_state()
    if not text_entry_result["summary_message"].startswith("Success"):
        await browser_manager.take_screenshots(f"{function_name}_end", page)
        return f"Failed to enter text '{text_to_enter}' into element with selector '{text_selector}'. Check that the selctor is valid."

    result = text_entry_result
    set_page_data(
        page,
        {
            "user_input_dialog_response": user_input_dialog_response,
            "expected_message_of_dialog": expected_message_of_dialog,
            "action_on_dialog": action_on_dialog,
        },
    )

    page.on("dialog", handle_dialog)

    # if the text_selector is the same as the click_selector, press the Enter key instead of clicking
    if text_selector == click_selector:
        do_press_key_combination_result = await do_press_key_combination(browser_manager, page, "Enter")
        if do_press_key_combination_result:
            result["detailed_message"] += f' Instead of click, pressed the Enter key successfully on element: "{click_selector}".'
        else:
            result[
                "detailed_message"
            ] += f' Clicking the same element after entering text in it, is of no value. Tried pressing the Enter key on element "{click_selector}" instead of click and failed.'
    else:
        await browser_manager.highlight_element(click_selector)
        do_click_result = await do_click(page, click_selector, wait_before_click_execution)
        result["detailed_message"] += f' {do_click_result["detailed_message"]}'

    await asyncio.sleep(get_global_conf().get_delay_time())  # sleep to allow the mutation observer to detect changes

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    return result["detailed_message"]
