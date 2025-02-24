import time
from typing import Annotated, Dict, Optional, Union

from playwright.sync_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.logger import logger


def custom_fill_element(page: Page, selector: str, text_to_enter: str) -> None:
    """
    Custom implementation to fill text in an element using JavaScript.
    """
    js_code = """
    (selector, text) => {
        const element = document.querySelector(selector);
        if (element) {
            element.value = text;
            element.dispatchEvent(new Event('input', { bubbles: true }));
            element.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }
        return false;
    }
    """
    result = page.evaluate(js_code, selector, text_to_enter)
    if not result:
        raise Exception(f"Failed to fill element with selector: {selector}")


@tool(
    agent_names=["text_input_nav_agent"],
    description="Enter text into an input field using a selector.",
    name="entertext",
)
def entertext(
    selector: Annotated[str, "CSS selector for the input field."],
    text_to_enter: Annotated[str, "Text to enter into the input field."],
    use_keyboard_fill: Annotated[
        bool, "Whether to use keyboard simulation for filling text."
    ] = True,
) -> Annotated[Dict[str, str], "Result of the text entry operation."]:
    """
    Enter text into an input field using a selector.
    """
    function_name = "entertext"
    browser_manager = PlaywrightManager()
    page = browser_manager.get_current_page()

    try:
        browser_manager.take_screenshots(f"{function_name}_start", page)
        browser_manager.highlight_element(selector)

        # Inject mutation observer script
        page.evaluate(
            """
            if (!window._mutationObserverInitialized) {
                window._mutationObserverInitialized = true;
                const observer = new MutationObserver((mutations) => {
                    window.dom_mutation_change_detected(mutations.length);
                });
                observer.observe(document.documentElement, {
                    childList: true,
                    subtree: true,
                    attributes: true,
                    characterData: true
                });
            }
        """
        )

        result = do_entertext(page, selector, text_to_enter)
        time.sleep(
            get_global_conf().get_delay_time()
        )  # sleep to allow the mutation observer to detect changes

        page.wait_for_load_state()
        browser_manager.take_screenshots(f"{function_name}_end", page)

        return result
    except Exception as e:
        logger.error(f"Error in {function_name}: {str(e)}")
        return {"error": str(e)}


def do_entertext(
    page: Page, selector: str, text_to_enter: str, use_keyboard_fill: bool = True
) -> Dict[str, str]:
    """
    Helper function to perform the actual text entry.
    Example:
    ```python
    result = do_entertext(page, '#username', 'test_user')
    ```
    """
    try:
        browser_manager = PlaywrightManager()
        elem = browser_manager.find_element(selector, page)
        if not elem:
            return {"error": f"Element not found with selector: {selector}"}

        element_outer_html = get_element_outer_html(elem, page)

        if use_keyboard_fill:
            elem.focus()
            time.sleep(0.01)
            page.keyboard.press("Control+A")
            time.sleep(0.01)
            page.keyboard.press("Delete")
            time.sleep(0.01)
            page.keyboard.type(text_to_enter, delay=1)
        else:
            custom_fill_element(page, selector, text_to_enter)

        elem.focus()
        page.wait_for_load_state()

        return {
            "success": True,
            "message": f"Text entered successfully into element: {element_outer_html}",
        }
    except Exception as e:
        logger.error(f"Error in do_entertext: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["text_input_nav_agent"],
    description="Enter text into multiple input fields using selectors.",
    name="bulk_enter_text",
)
def bulk_enter_text(
    entries: Annotated[
        List[Dict[str, str]],
        "List of dictionaries containing 'selector' and 'text_to_enter' keys.",
    ],
) -> Annotated[List[Dict[str, str]], "Results of the bulk text entry operations."]:
    """
    Enter text into multiple input fields using selectors.
    """
    results = []
    for entry in entries:
        result = entertext(entry["selector"], entry["text_to_enter"])
        results.append(result)
    return results
