import asyncio

from testzeus_hercules.utils.logger import logger
from playwright.async_api import ElementHandle, Page


async def wait_for_non_loading_dom_state(page: Page, max_wait_millis: int):
    max_wait_seconds = max_wait_millis / 1000
    end_time = asyncio.get_event_loop().time() + max_wait_seconds
    while asyncio.get_event_loop().time() < end_time:
        all_frames_ready = True
        for frame in page.frames:
            dom_state = await frame.evaluate("document.readyState")
            if dom_state == "loading":
                all_frames_ready = False
                break  # Exit the loop if any frame is still loading
        if all_frames_ready:
            logger.debug("All frames have DOM state not 'loading'")
            break  # Exit the outer loop if all frames are ready
        await asyncio.sleep(0.5)


async def get_element_outer_html(
    element: ElementHandle, page: Page, element_tag_name: str | None = None
) -> str:
    """
    Constructs the opening tag of an HTML element along with its attributes.

    Args:
        element (ElementHandle): The element to retrieve the opening tag for.
        page (Page): The page object associated with the element.
        element_tag_name (str, optional): The tag name of the element. Defaults to None. If not passed, it will be retrieved from the element.

    Returns:
        str: The opening tag of the HTML element, including a select set of attributes.
    """
    tag_name: str = (
        element_tag_name
        if element_tag_name
        else await page.evaluate("element => element.tagName.toLowerCase()", element)
    )

    attributes_of_interest: list[str] = [
        "id",
        "name",
        "aria-label",
        "placeholder",
        "href",
        "src",
        "aria-autocomplete",
        "role",
        "type",
        "data-testid",
        "value",
        "selected",
        "aria-labelledby",
        "aria-describedby",
        "aria-haspopup",
        "title",
        "aria-controls",
    ]
    opening_tag: str = f"<{tag_name}"

    for attr in attributes_of_interest:
        value: str = await element.get_attribute(attr)  # type: ignore
        if value:
            opening_tag += f' {attr}="{value}"'
    opening_tag += ">"

    return opening_tag
