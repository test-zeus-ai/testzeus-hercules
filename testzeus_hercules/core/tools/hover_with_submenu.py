import asyncio
import inspect
import traceback
from typing import Annotated, Optional

import playwright.async_api
from playwright.async_api import ElementHandle, Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.browser_logger import get_browser_logger
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import get_element_outer_html
from testzeus_hercules.utils.dom_mutation_observer import subscribe, unsubscribe
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["browser_nav_agent"],
    description="""Enhanced hover tool that maintains hover state and waits for submenus to appear. 
    Returns tooltip details and submenu information.""",
    name="hover_with_submenu",
)
async def hover_with_submenu(
    selector: Annotated[str, "selector using md attribute, just give the md ID value"],
    wait_for_submenu: Annotated[bool, "Whether to wait for submenu to appear"] = True,
    submenu_timeout: Annotated[float, "Timeout in seconds to wait for submenu"] = 3.0,
    maintain_hover: Annotated[bool, "Whether to maintain hover state after submenu appears"] = True,
    wait_before_execution: Annotated[float, "Wait time in seconds before hover"] = 0.0,
) -> Annotated[str, "Result of hover action with tooltip text and submenu information"]:
    """
    Enhanced hover tool that can maintain hover state and wait for submenus to appear.
    This prevents hover state from being lost during page refreshes or DOM changes.
    """
    logger.info(f'Executing EnhancedHoverElement with "{selector}" as the selector')
    if "md=" not in selector:
        selector = f"[md='{selector}']"
    
    add_event(EventType.INTERACTION, EventData(detail="hover_with_submenu"))
    
    # Initialize PlaywrightManager and get the active browser page
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()

    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)
    await browser_manager.highlight_element(selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    subscribe(detect_dom_changes)
    result = await do_enhanced_hover(
        page, selector, wait_before_execution, wait_for_submenu, submenu_timeout, maintain_hover
    )
    await asyncio.sleep(get_global_conf().get_delay_time())
    unsubscribe(detect_dom_changes)

    await browser_manager.wait_for_load_state_if_enabled(page=page)
    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"Success: {result['summary_message']}.\nAs a consequence of this action, new elements have appeared in view: {dom_changes_detected}. You may need further interaction. Get all_fields DOM to complete the interaction, if needed, also the tooltip data is already in the message"
    return result["detailed_message"]


async def do_enhanced_hover(
    page: Page, 
    selector: str, 
    wait_before_execution: float,
    wait_for_submenu: bool,
    submenu_timeout: float,
    maintain_hover: bool
) -> dict[str, str]:
    """
    Enhanced hover function that maintains hover state and waits for submenus.
    """
    logger.info(f'Executing EnhancedHoverElement with "{selector}" as the selector. Wait time before execution: {wait_before_execution} seconds.')

    # Wait before execution if specified
    if wait_before_execution > 0:
        await asyncio.sleep(wait_before_execution)

    try:
        logger.info(f'Executing EnhancedHoverElement with "{selector}" as the selector. Waiting for the element to be attached and visible.')

        # Wait for the page to load
        browser_manager = PlaywrightManager()
        await browser_manager.wait_for_load_state_if_enabled(page=page)

        # Find the element
        element = await page.query_selector(selector)
        if not element:
            selector_logger = get_browser_logger(get_global_conf().get_proof_path())
            await selector_logger.log_selector_interaction(
                tool_name="hover_with_submenu",
                selector=selector,
                action="hover",
                selector_type="css" if "md=" in selector else "custom",
                success=False,
                error_message=f'Element with selector: "{selector}" not found',
            )
            raise ValueError(f'Element with selector: "{selector}" not found')

        logger.info(f'Element with selector: "{selector}" is found. Scrolling it into view if needed.')
        
        try:
            await element.scroll_into_view_if_needed(timeout=200)
            logger.info(f'Element with selector: "{selector}" is scrolled into view. Waiting for the element to be visible.')
        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error scrolling element into view: {e}")

        try:
            await element.wait_for_element_state("visible", timeout=200)
            logger.info(f'Element with selector: "{selector}" is visible. Hovering over the element.')
        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error waiting for element to be visible: {e}")

        element_tag_name = await element.evaluate("element => element.tagName.toLowerCase()")
        element_outer_html = await get_element_outer_html(element, page, element_tag_name)

        # Initialize selector logger
        selector_logger = get_browser_logger(get_global_conf().get_proof_path())
        alternative_selectors = await selector_logger.get_alternative_selectors(element, page)
        element_attributes = await selector_logger.get_element_attributes(element)

        # Perform the initial hover
        await perform_enhanced_hover(element, selector, maintain_hover)

        # Wait briefly to allow any tooltips to appear
        await asyncio.sleep(0.2)

        # Capture tooltip information
        tooltip_text = await get_tooltip_text(page)

        # Wait for submenu if requested
        submenu_info = ""
        if wait_for_submenu:
            # First, get a baseline of current elements
            baseline_elements = await page.evaluate("""
                () => {
                    const elements = document.querySelectorAll('ul, .sub, .menu, .dropdown, .nav, [class*="sub"], [class*="menu"], [class*="dropdown"], [class*="nav"]');
                    return Array.from(elements).map(el => ({
                        tagName: el.tagName,
                        className: el.className,
                        id: el.id,
                        text: el.innerText?.substring(0, 50) || '',
                        visible: el.offsetParent !== null && el.style.display !== 'none' && el.style.visibility !== 'hidden'
                    }));
                }
            """)
            
            logger.info(f"Baseline elements before hover: {len(baseline_elements)}")
            
            # Wait for submenu to appear
            submenu_info = await wait_for_submenu_to_appear(page, selector, submenu_timeout)
            
            # After waiting, check for new elements
            current_elements = await page.evaluate("""
                () => {
                    const elements = document.querySelectorAll('ul, .sub, .menu, .dropdown, .nav, [class*="sub"], [class*="menu"], [class*="dropdown"], [class*="nav"]');
                    return Array.from(elements).map(el => ({
                        tagName: el.tagName,
                        className: el.className,
                        id: el.id,
                        text: el.innerText?.substring(0, 50) || '',
                        visible: el.offsetParent !== null && el.style.display !== 'none' && el.style.visibility !== 'hidden'
                    }));
                }
            """)
            
            logger.info(f"Current elements after hover: {len(current_elements)}")
            
            # Find new visible elements
            new_elements = []
            for current in current_elements:
                if current['visible']:
                    is_new = True
                    for baseline in baseline_elements:
                        if (current['tagName'] == baseline['tagName'] and 
                            current['className'] == baseline['className'] and
                            current['id'] == baseline['id']):
                            is_new = False
                            break
                    if is_new:
                        new_elements.append(current)
            
            if new_elements:
                logger.info(f"New elements detected after hover: {len(new_elements)}")
                for elem in new_elements:
                    logger.info(f"  - {elem['tagName']}.{elem['className']}: {elem['text']}")
                submenu_info += f" (Detected {len(new_elements)} new elements)"
            else:
                logger.warning("No new elements detected after hover")

        # Log successful selector interaction
        await selector_logger.log_selector_interaction(
            tool_name="hover_with_submenu",
            selector=selector,
            action="hover",
            selector_type="css" if "md=" in selector else "custom",
            alternative_selectors=alternative_selectors,
            element_attributes=element_attributes,
            success=True,
            additional_data={
                "tooltip_text": tooltip_text,
                "submenu_info": submenu_info
            } if tooltip_text or submenu_info else None,
        )

        msg = f'Executed enhanced hover action on element with selector: "{selector}".'
        if tooltip_text:
            msg += f' Tooltip shown: "{tooltip_text}".'
        if submenu_info:
            msg += f' {submenu_info}'

        return {
            "summary_message": f"Successfully hovered over element {selector}",
            "detailed_message": msg
        }

    except Exception as e:
        logger.exception(f"Error in enhanced hover: {e}")
        return {
            "summary_message": f"Failed to hover over element {selector}",
            "detailed_message": f"Error: {str(e)}"
        }


async def perform_enhanced_hover(element: ElementHandle, selector: str, maintain_hover: bool) -> None:
    """
    Performs an enhanced hover action that can maintain hover state.
    """
    logger.info("Performing enhanced Playwright hover on element with selector: %s", selector)
    
    # Perform the initial hover
    await element.hover(force=True, timeout=200)
    
    if maintain_hover:
        # Use JavaScript to maintain hover state
        await element.evaluate("""
            (element) => {
                // Add a class to indicate hover state
                element.classList.add('hercules-hover-active');
                
                // Store the original mouseover event
                const originalMouseOver = element.onmouseover;
                
                // Set up a periodic check to maintain hover
                const hoverInterval = setInterval(() => {
                    if (!element.classList.contains('hercules-hover-active')) {
                        clearInterval(hoverInterval);
                        return;
                    }
                    
                    // Trigger mouseover event to maintain hover state
                    if (originalMouseOver) {
                        originalMouseOver.call(element, new MouseEvent('mouseover'));
                    }
                    
                    // Also trigger the ng-mouseover if it exists
                    const ngMouseOver = element.getAttribute('ng-mouseover');
                    if (ngMouseOver) {
                        // This will be handled by AngularJS
                        element.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
                    }
                }, 100);
                
                // Store the interval ID for cleanup
                element._herculesHoverInterval = hoverInterval;
            }
        """, element)


async def wait_for_submenu_to_appear(page: Page, parent_selector: str, timeout: float) -> str:
    """
    Waits for a submenu to appear after hovering over a parent element.
    """
    logger.info(f"Waiting for submenu to appear for element: {parent_selector}")
    
    start_time = asyncio.get_event_loop().time()
    
    while (asyncio.get_event_loop().time() - start_time) < timeout:
        try:
            # Look for common submenu selectors (generic) - search anywhere in DOM
            submenu_selectors = [
                "ul.sub",
                "ul.sublvl1", 
                "ul.sublvl2",
                ".sub",
                ".dropdown-menu",
                ".submenu",
                ".menu",
                ".nav",
                "[class*='sub']",
                "[class*='menu']",
                "[class*='dropdown']",
                "[class*='nav']",
                "[class*='sublvl']",
                "[class*='level']",
                "[class*='item']",
                "[class*='list']"
            ]
            
            for submenu_selector in submenu_selectors:
                # Find all elements matching this selector
                submenu_elements = await page.query_selector_all(submenu_selector)
                
                for submenu_element in submenu_elements:
                    try:
                        # Check if the submenu is visible
                        is_visible = await submenu_element.is_visible()
                        if is_visible:
                            # Get the submenu text
                            submenu_text = await submenu_element.inner_text()
                            
                            # Check if this submenu has meaningful content (not empty)
                            if submenu_text and submenu_text.strip():
                                # Get the submenu's position and size
                                bounding_box = await submenu_element.bounding_box()
                                if bounding_box and bounding_box['width'] > 0 and bounding_box['height'] > 0:
                                    logger.info(f"Submenu found and visible: {submenu_selector} with text: {submenu_text[:50]}...")
                                    return f"Submenu appeared: {submenu_text[:100]}..."
                    except Exception as e:
                        logger.debug(f"Error checking submenu element: {e}")
                        continue
            
            # Wait a bit before checking again
            await asyncio.sleep(0.1)
            
        except Exception as e:
            logger.debug(f"Error checking for submenu: {e}")
            await asyncio.sleep(0.1)
    
    logger.warning(f"Submenu did not appear within {timeout} seconds for element: {parent_selector}")
    return "Submenu did not appear within timeout period"


async def get_tooltip_text(page: Page) -> str:
    """
    Gets tooltip text from the page.
    """
    try:
        # JavaScript code to find tooltip elements
        tooltip_text = await page.evaluate("""
            () => {
                const tooltipSelectors = [
                    '[role="tooltip"]',
                    '.tooltip',
                    '.tooltip-inner',
                    '[data-toggle="tooltip"]',
                    '[title]',
                    '.popover',
                    '.popover-content',
                    '.dropdown-menu',
                    '.sub',
                    '.submenu'
                ];
                
                for (const selector of tooltipSelectors) {
                    const elements = document.querySelectorAll(selector);
                    for (const element of elements) {
                        if (element.offsetParent !== null && 
                            element.style.display !== 'none' && 
                            element.style.visibility !== 'hidden') {
                            const text = element.innerText || element.textContent;
                            if (text && text.trim()) {
                                return text.trim();
                            }
                        }
                    }
                }
                return '';
            }
        """)
        return tooltip_text if tooltip_text else ""
    except Exception as e:
        logger.debug(f"Error retrieving tooltip text: {e}")
        return ""


@tool(
    agent_names=["browser_nav_agent"],
    description="""Releases hover state from an element.""",
    name="release_hover",
)
async def release_hover(
    selector: Annotated[str, "selector using md attribute, just give the md ID value"],
) -> Annotated[str, "Result of releasing hover state"]:
    """
    Releases the hover state from an element.
    """
    logger.info(f'Releasing hover state from element with selector: "{selector}"')
    
    if "md=" not in selector:
        selector = f"[md='{selector}']"
    
    add_event(EventType.INTERACTION, EventData(detail="release_hover"))
    
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()

    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    try:
        element = await page.query_selector(selector)
        if element:
            # Remove hover state using JavaScript
            await element.evaluate("""
                (element) => {
                    element.classList.remove('hercules-hover-active');
                    if (element._herculesHoverInterval) {
                        clearInterval(element._herculesHoverInterval);
                        delete element._herculesHoverInterval;
                    }
                }
            """, element)
            
            # Move mouse away from the element
            await element.hover(force=False)
            
            return f"Successfully released hover state from element: {selector}"
        else:
            return f"Element not found: {selector}"
            
    except Exception as e:
        logger.exception(f"Error releasing hover state: {e}")
        return f"Error releasing hover state: {str(e)}" 