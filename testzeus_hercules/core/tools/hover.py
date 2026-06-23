import asyncio
import inspect
import traceback
from typing import Annotated

import playwright.async_api
from playwright.async_api import ElementHandle, Page
from testzeus_hercules.config import get_global_conf  # Add this import
from testzeus_hercules.core.browser_logger import get_browser_logger
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe  # type: ignore
from testzeus_hercules.utils.dom_mutation_observer import unsubscribe  # type: ignore
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["browser_nav_agent"],
    description=(
        "Hovers over an element and returns tooltip or hover-revealed details. "
        "Use this whenever the task says to hover. Prefer the numeric md id from "
        "get_interactive_elements, but visible descriptions like 'first User Avatar image' "
        "are also accepted."
    ),
    name="hover",
)
async def hover(
    selector: Annotated[str, "Numeric md id, CSS selector, or visible element description to hover"],
    wait_before_execution: Annotated[float, "Wait time in seconds before hover"] = 0.0,
) -> Annotated[str, "Result of hover action with tooltip text"]:
    logger.info(f'Executing HoverElement with "{selector}" as the selector')
    selector = selector.strip()
    if selector.isdigit():
        selector = f"[md='{selector}']"
    add_event(EventType.INTERACTION, EventData(detail="hover"))
    # Initialize PlaywrightManager and get the active browser page
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()

    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)

    if _is_query_selector(selector):
        await browser_manager.highlight_element(selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    subscribe(detect_dom_changes)
    result = await do_hover(page, selector, wait_before_execution)
    await asyncio.sleep(get_global_conf().get_delay_time())  # sleep to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)

    await browser_manager.wait_for_load_state_if_enabled(page=page)

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"Success: {result['summary_message']}.\nAs a consequence of this action, new elements have appeared in view: {dom_changes_detected}. You may need further interaction. Get all_fields DOM to complete the interaction, if needed, also the tooltip data is already in the message"
    return result["detailed_message"]


async def do_hover(page: Page, selector: str, wait_before_execution: float) -> dict[str, str]:
    """
    Executes the hover action on the element with the given selector within the provided page,
    including searching within iframes and shadow DOMs if necessary.

    Parameters:
    - page: The Playwright page instance.
    - selector: The query selector string to identify the element for the hover action.
    - wait_before_execution: Optional wait time in seconds before executing the hover event logic.

    Returns:
    dict[str,str] - Explanation of the outcome of this operation represented as a dictionary with 'summary_message' and 'detailed_message'.
    """
    logger.info(f'Executing HoverElement with "{selector}" as the selector. Wait time before execution: {wait_before_execution} seconds.')

    # Wait before execution if specified
    if wait_before_execution > 0:
        await asyncio.sleep(wait_before_execution)

    try:
        logger.info(f'Executing HoverElement with "{selector}" as the selector. Waiting for the element to be attached and visible.')

        # Attempt to find the element on the main page or in shadow DOMs and iframes
        browser_manager = PlaywrightManager()

        # Get the current page
        page = await browser_manager.get_current_page()

        # Wait for the page to load
        await browser_manager.wait_for_load_state_if_enabled(page=page)

        # Find the element
        element, resolved_selector = await resolve_hover_element(page, selector)
        if not element:
            # Initialize selector logger with proof path
            selector_logger = get_browser_logger(get_global_conf().get_proof_path())
            # Log failed selector interaction
            await selector_logger.log_selector_interaction(
                tool_name="hover",
                selector=selector,
                action="hover",
                selector_type="css" if "md=" in selector else "custom",
                success=False,
                error_message=f'Element with selector: "{selector}" not found',
            )
            raise ValueError(f'Element with selector: "{selector}" not found')

        logger.info(f'Element with selector: "{resolved_selector}" is found. Scrolling it into view if needed.')
        try:
            await element.scroll_into_view_if_needed(timeout=200)
            logger.info(f'Element with selector: "{selector}" is scrolled into view. Waiting for the element to be visible.')
        except Exception as e:

            traceback.print_exc()
            logger.exception(f"Error scrolling element into view: {e}")
            # If scrollIntoView fails, just move on
            pass

        try:
            await element.wait_for_element_state("visible", timeout=200)
            logger.info(f'Element with selector: "{selector}" is visible. Hovering over the element.')
        except Exception as e:

            traceback.print_exc()
            logger.exception(f"Error waiting for element to be visible: {e}")
            # If the element is not visible, try to hover over it anyway
            pass

        element_tag_name = await element.evaluate("element => element.tagName.toLowerCase()")
        element_outer_html = await get_element_outer_html(element, page, element_tag_name)

        # Initialize selector logger with proof path
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
        # Get alternative selectors and element attributes for logging
        alternative_selectors = await selector_logger.get_alternative_selectors(element, page)
        element_attributes = await selector_logger.get_element_attributes(element)

        await perform_playwright_hover(element, selector)

        # Wait briefly to allow any tooltips to appear
        await asyncio.sleep(0.2)

        # Capture tooltip/hover-revealed information
        tooltip_text = await get_tooltip_text(page, resolved_selector)

        # Log successful selector interaction
        await selector_logger.log_selector_interaction(
            tool_name="hover",
            selector=selector,
            action="hover",
            selector_type="css" if "md=" in selector else "custom",
            alternative_selectors=alternative_selectors,
            element_attributes=element_attributes,
            success=True,
            additional_data={"tooltip_text": tooltip_text} if tooltip_text else None,
        )

        msg = f'Executed hover action on element with selector: "{resolved_selector}".'
        if tooltip_text:
            msg += f' Tooltip shown: "{tooltip_text}".'

        return {
            "summary_message": msg,
            "detailed_message": f"{msg} The hovered element's outer HTML is: {element_outer_html}.",
        }
    except Exception as e:

        traceback.print_exc()
        # Initialize selector logger with proof path
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
        # Log failed selector interaction
        await selector_logger.log_selector_interaction(
            tool_name="hover",
            selector=selector,
            action="hover",
            selector_type="css" if "md=" in selector else "custom",
            success=False,
            error_message=str(e),
        )

        logger.error(f'Unable to hover over element with selector: "{selector}". Error: {e}')
        traceback.print_exc()
        msg = f'Unable to hover over element with selector: "{selector}" since the selector is invalid or the element is not interactable. Consider retrieving the DOM again.'
        return {"summary_message": msg, "detailed_message": f"{msg}. Error: {e}"}


async def get_tooltip_text(page: Page, selector: str = "") -> str:
    # JavaScript code to find tooltip elements
    js_code = """
    (selector) => {
        function isVisible(element) {
            if (!element) return false;
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return (
                style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                style.opacity !== '0' &&
                rect.width > 0 &&
                rect.height > 0
            );
        }

        function collectVisibleText(root) {
            if (!root) return '';
            const parts = [];
            const elements = Array.from(root.querySelectorAll('*'));
            if (elements.length === 0) elements.push(root);
            for (const element of elements) {
                if (isVisible(element)) {
                    const hasVisibleTextChild = Array.from(element.children || []).some((child) => {
                        const childText = (child.innerText || child.textContent || '').trim();
                        return isVisible(child) && childText;
                    });
                    if (hasVisibleTextChild) continue;
                    const text = (element.innerText || element.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (text) parts.push(text);
                }
            }
            if (parts.length === 0 && isVisible(root)) {
                const text = (root.innerText || root.textContent || '').replace(/\\s+/g, ' ').trim();
                if (text) parts.push(text);
            }
            return [...new Set(parts)].join('\\n').trim();
        }

        // Search near the hovered element first. Many hover UIs reveal captions
        // in the same container instead of using role="tooltip".
        if (selector) {
            let hovered = null;
            try {
                hovered = document.querySelector(selector);
            } catch (error) {
                hovered = null;
            }
            if (hovered) {
                const containers = [
                    hovered.closest('.figure'),
                    hovered.parentElement,
                    hovered.nextElementSibling,
                    hovered
                ].filter(Boolean);
                for (const container of containers) {
                    const text = collectVisibleText(container);
                    if (text) return text;
                }
            }
        }

        // Search for elements with role="tooltip"
        let tooltip = document.querySelector('[role="tooltip"]');
        if (tooltip && isVisible(tooltip) && tooltip.innerText) {
            return tooltip.innerText.trim();
        }

        // Search for common tooltip classes
        let tooltipClasses = ['tooltip', 'ui-tooltip', 'tooltip-inner', 'figcaption'];
        for (let cls of tooltipClasses) {
            tooltip = document.querySelector('.' + cls);
            if (tooltip && isVisible(tooltip) && tooltip.innerText) {
                return tooltip.innerText.trim();
            }
        }

        return '';
    }
    """
    try:
        tooltip_text = await page.evaluate(js_code, selector)
        return tooltip_text
    except Exception as e:

        traceback.print_exc()
        logger.error(f"Error retrieving tooltip text: {e}")
        return ""


def _is_query_selector(selector: str) -> bool:
    return selector.startswith(("[", "#", ".", "xpath=", "text="))


async def resolve_hover_element(page: Page, selector: str) -> tuple[ElementHandle | None, str]:
    if _is_query_selector(selector):
        try:
            element = await page.query_selector(selector)
            if element:
                return element, selector
        except Exception:
            pass

    resolved = await page.evaluate_handle(
        """
        (rawSelector) => {
            function isVisible(element) {
                if (!element) return false;
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return (
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    style.opacity !== '0' &&
                    rect.width > 0 &&
                    rect.height > 0
                );
            }

            const raw = String(rawSelector || '').trim();
            const lowered = raw.toLowerCase();
            const ordinalWords = [
                ['first', 0], ['1st', 0],
                ['second', 1], ['2nd', 1],
                ['third', 2], ['3rd', 2]
            ];
            let ordinal = 0;
            for (const [word, index] of ordinalWords) {
                if (new RegExp(`\\\\b${word}\\\\b`).test(lowered)) {
                    ordinal = index;
                    break;
                }
            }

            const target = lowered
                .replace(/\\b(first|1st|second|2nd|third|3rd|the|a|an|image|img|element|button|link|field|over|hover|on)\\b/g, ' ')
                .replace(/\\s+/g, ' ')
                .trim();

            const candidates = Array.from(
                document.querySelectorAll('[md], img, button, a, input, select, textarea, [role]')
            ).filter(isVisible);

            function textFor(element) {
                return [
                    element.getAttribute('alt'),
                    element.getAttribute('aria-label'),
                    element.getAttribute('title'),
                    element.getAttribute('name'),
                    element.innerText,
                    element.textContent
                ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim().toLowerCase();
            }

            const scored = candidates
                .map((element, index) => {
                    const text = textFor(element);
                    let score = 0;
                    if (target && text === target) score += 100;
                    if (target && text.includes(target)) score += 50;
                    if (target && target.includes(text) && text.length > 2) score += 25;
                    if (lowered.includes('avatar') && text.includes('avatar')) score += 40;
                    if (lowered.includes('image') && element.tagName.toLowerCase() === 'img') score += 20;
                    if (element.hasAttribute('md')) score += 5;
                    return {element, score, index};
                })
                .filter((item) => item.score > 0)
                .sort((a, b) => b.score - a.score || a.index - b.index);

            return scored[ordinal]?.element || scored[0]?.element || null;
        }
        """,
        selector,
    )
    element = resolved.as_element()
    if not element:
        return None, selector

    md = await element.get_attribute("md")
    if md:
        return element, f"[md='{md}']"
    return element, selector


async def perform_playwright_hover(element: ElementHandle, selector: str) -> None:
    """
    Performs a hover action on the element using Playwright's hover method.

    Parameters:
    - element: The Playwright ElementHandle instance representing the element to be hovered over.
    - selector: The query selector string of the element.

    Returns:
    - None
    """
    logger.info("Performing Playwright hover on element with selector: %s", selector)
    await element.hover(force=True, timeout=1000)
