import asyncio
from typing import Annotated, List

from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger
from .hover_with_submenu import hover_with_submenu, release_hover
from .click_using_selector import click


@tool(
    agent_names=["browser_nav_agent"],
    description="""Simple multi-level navigation tool that handles nested hover scenarios and automatically clicks the final item.
    Use this when you need to hover through multiple menu levels and click the final item in the path.
    Example: ['Products', 'Transfer', 'Route'] will hover Products, hover Transfer, then click Route.""",
    name="multi_level_navigation",
)
async def multi_level_navigation(
    navigation_path: Annotated[List[str], "List of menu items to hover through (e.g., ['Products', 'Transfer', 'Route'])"],
    submenu_timeout: Annotated[float, "Timeout in seconds to wait for each submenu"] = 3.0,
) -> Annotated[str, "Result of multi-level navigation action"]:
    """
    Simple multi-level navigation that only opens intended menus and automatically clicks the final item.
    
    Example: navigation_path=['Products', 'Transfer', 'Route']
    - Hovers over "Products" and waits for its submenu
    - Hovers over "Transfer" (in Products submenu) and waits for its submenu  
    - Automatically clicks on "Route" (in Transfer submenu)
    - Cleans up all hover states after clicking
    
    Note: The final item in the navigation path will be automatically clicked.
    """
    logger.info(f'Executing MultiLevelNavigation with path: {navigation_path}')
    
    add_event(EventType.INTERACTION, EventData(detail=f"multi_level_navigation_{'_'.join(navigation_path)}"))
    
    # Initialize PlaywrightManager and get the active browser page
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()

    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    try:
        from .get_interactive_elements import get_interactive_elements
        
        # Track only the current hover for cleanup
        current_hover_selector = None
        
        # Process each level of navigation
        for i, menu_item in enumerate(navigation_path):
            logger.info(f"=== Processing navigation level {i+1}: {menu_item} ===")
            
            # For intermediate items, we need to get elements after hovering the parent
            if i > 0:
                # Wait a bit for submenu to fully appear
                await asyncio.sleep(0.5)
                
                # Get elements after the submenu has appeared
                interactive_elements_result = await get_interactive_elements()
                import json
                elements_data = json.loads(interactive_elements_result)
                
                # Find the menu element in the submenu context
                menu_selector = find_menu_element(elements_data, menu_item)
                
                if not menu_selector:
                    # Try alternative search methods for submenu items
                    menu_selector = await find_submenu_element(page, menu_item)
                    
                if not menu_selector:
                    return f"Menu item '{menu_item}' not found in submenu elements"
            else:
                # For first item or final item, get current interactive elements
                interactive_elements_result = await get_interactive_elements()
                import json
                elements_data = json.loads(interactive_elements_result)
                
                # Find the menu element
                menu_selector = find_menu_element(elements_data, menu_item)
                
                if not menu_selector:
                    return f"Menu item '{menu_item}' not found in interactive elements"
            
            # Log element details for debugging
            try:
                element_details = await page.evaluate(f"""
                    () => {{
                        const element = document.querySelector('[md="{menu_selector}"]');
                        if (element) {{
                            return {{
                                tagName: element.tagName,
                                className: element.className,
                                innerText: element.innerText?.substring(0, 50) || '',
                                outerHTML: element.outerHTML?.substring(0, 100) || ''
                            }};
                        }}
                        return null;
                    }}
                """)
                if element_details:
                    logger.info(f"Element details: {element_details['tagName']}.{element_details['className']} - '{element_details['innerText']}'")
            except Exception as e:
                logger.debug(f"Error getting element details: {e}")
            
            # Determine if this is the final item (to be clicked) or intermediate (to be hovered)
            is_final_item = (i == len(navigation_path) - 1)
            
            if is_final_item:
                # For final item, just click it
                logger.info(f"=== FINAL ITEM: Clicking {menu_item} ===")
                
                # Simple click with fallback
                click_result = await simple_click_with_fallback(page, menu_selector, menu_item)
                
                # Clean up any remaining hover state
                if current_hover_selector:
                    await release_hover(selector=current_hover_selector)
                
                return f"Successfully completed multi-level navigation: {' -> '.join(navigation_path)}. {click_result}"
            else:
                # For intermediate items, hover and wait for submenu
                logger.info(f"=== INTERMEDIATE ITEM: Hovering over {menu_item} ===")
                
                # Release previous hover if exists
                if current_hover_selector:
                    await release_hover(selector=current_hover_selector)
                
                # Hover over the current item
                hover_result = await hover_with_submenu(
                    selector=menu_selector,
                    wait_for_submenu=True,
                    submenu_timeout=submenu_timeout,
                    maintain_hover=True,
                    wait_before_execution=0.3
                )
                
                # Track current hover for next iteration
                current_hover_selector = menu_selector
                
                # Wait for submenu to appear
                await asyncio.sleep(0.5)
                
                logger.info(f"Hovered over '{menu_item}': {hover_result}")
        
        # If we get here, something went wrong
        return f"Unexpected end of navigation path: {navigation_path}"
        
    except Exception as e:
        logger.exception(f"Error in multi-level navigation: {e}")
        return f"Failed to complete multi-level navigation: {str(e)}"


def find_menu_element(elements, target_name):
    """Simple function to find menu element by name."""
    if isinstance(elements, list):
        for element in elements:
            result = find_menu_element(element, target_name)
            if result:
                return result
    elif isinstance(elements, dict):
        # Check if this element matches the menu name
        element_name = elements.get('name', '').lower()
        element_class = elements.get('class', '').lower()
        target_name_lower = target_name.lower()
        
        # Priority order: exact name match, then exact class match, then contained name
        if target_name_lower == element_name:
            md_value = elements.get('md')
            if md_value:
                return md_value
        
        if target_name_lower == element_class:
            md_value = elements.get('md')
            if md_value:
                return md_value
        
        if target_name_lower in element_name:
            md_value = elements.get('md')
            if md_value:
                return md_value
        
        # Check children recursively
        children = elements.get('children', [])
        if children:
            result = find_menu_element(children, target_name)
            if result:
                return result
    return None


async def find_submenu_element(page, target_name: str):
    """Find menu element in submenu context using alternative methods."""
    logger.info(f"Searching for submenu element: {target_name}")
    
    try:
        # Method 1: Try exact text match in submenu context
        element = await page.query_selector(f"text={target_name}")
        if element:
            md_value = await element.get_attribute('md')
            if md_value:
                logger.info(f"Found '{target_name}' using exact text match: {md_value}")
                return md_value
        
        # Method 2: Try case-insensitive text match
        element = await page.query_selector(f"text={target_name}i")
        if element:
            md_value = await element.get_attribute('md')
            if md_value:
                logger.info(f"Found '{target_name}' using case-insensitive text match: {md_value}")
                return md_value
        
        # Method 3: Try partial text match
        element = await page.query_selector(f"text*={target_name}")
        if element:
            md_value = await element.get_attribute('md')
            if md_value:
                logger.info(f"Found '{target_name}' using partial text match: {md_value}")
                return md_value
        
        # Method 4: Try by class name containing the target
        element = await page.query_selector(f"[class*='{target_name.lower()}']")
        if element:
            md_value = await element.get_attribute('md')
            if md_value:
                logger.info(f"Found '{target_name}' using class name match: {md_value}")
                return md_value
        
        # Method 5: Try exact class name match
        element = await page.query_selector(f"[class='{target_name.lower()}']")
        if element:
            md_value = await element.get_attribute('md')
            if md_value:
                logger.info(f"Found '{target_name}' using exact class match: {md_value}")
                return md_value
        
        # Method 6: Try by span class (for menu icons)
        element = await page.query_selector(f"span[class='{target_name.lower()}']")
        if element:
            md_value = await element.get_attribute('md')
            if md_value:
                logger.info(f"Found '{target_name}' using span class match: {md_value}")
                return md_value
        
        # Method 7: Search in submenu containers specifically
        submenu_selectors = ['ul.sub', 'ul.sublvl1', 'ul.sublvl2', '.sub', '.dropdown-menu', '.submenu']
        for selector in submenu_selectors:
            try:
                element = await page.query_selector(f"{selector} text={target_name}")
                if element:
                    md_value = await element.get_attribute('md')
                    if md_value:
                        logger.info(f"Found '{target_name}' in submenu {selector}: {md_value}")
                        return md_value
            except Exception as e:
                logger.debug(f"Error searching in {selector}: {e}")
        
        logger.warning(f"Could not find submenu element '{target_name}' using any method")
        return None
        
    except Exception as e:
        logger.debug(f"Error in find_submenu_element: {e}")
        return None


async def simple_click_with_fallback(page, selector: str, menu_item: str):
    """Simple click with basic fallback methods."""
    logger.info(f"Attempting to click {menu_item} with selector {selector}")
    
    # Method 1: Try standard click
    try:
        logger.info(f"Method 1: Trying standard click for {menu_item}")
        result = await click(selector=selector)
        if "not visible" not in result.lower() and "error" not in result.lower():
            logger.info(f"Standard click successful for {menu_item}")
            return result
    except Exception as e:
        logger.debug(f"Standard click failed: {e}")
    
    # Method 2: Try JavaScript click
    try:
        logger.info(f"Method 2: Trying JavaScript click for {menu_item}")
        result = await page.evaluate(f"""
            () => {{
                const element = document.querySelector('[md="{selector}"]');
                if (!element) return {{ success: false, message: 'Element not found' }};
                
                try {{
                    element.click();
                    return {{ success: true, message: 'JavaScript click successful' }};
                }} catch (e) {{
                    return {{ success: false, message: 'JavaScript click failed: ' + e.message }};
                }}
            }}
        """)
        
        if result.get('success'):
            logger.info(f"JavaScript click successful for {menu_item}")
            return f"Successfully clicked {menu_item} using JavaScript"
    except Exception as e:
        logger.debug(f"JavaScript click failed: {e}")
    
    logger.error(f"All click methods failed for {menu_item}")
    return f"Failed to click {menu_item} - all methods exhausted"


@tool(
    agent_names=["browser_nav_agent"],
    description="""Simplified multi-level navigation for common scenarios.
    Use this for the specific case of hovering over two levels then clicking the final item.
    Example: hover Products, hover Transfer, then automatically click Route.""",
    name="hover_hover_click",
)
async def hover_hover_click(
    first_menu: Annotated[str, "First menu to hover over (e.g., 'Products')"],
    second_menu: Annotated[str, "Second menu to hover over (e.g., 'Transfer')"],
    final_item: Annotated[str, "Final item to click (e.g., 'Route')"],
    submenu_timeout: Annotated[float, "Timeout in seconds to wait for each submenu"] = 3.0,
) -> Annotated[str, "Result of hover-hover-click action"]:
    """Simplified version for the common hover-hover-click pattern."""
    return await multi_level_navigation([first_menu, second_menu, final_item], submenu_timeout)


@tool(
    agent_names=["browser_nav_agent"],
    description="""Single-level navigation: hover over a menu and automatically click a submenu item.
    Use this for simple hover -> click scenarios.
    Example: hover Products, then automatically click Transfer.""",
    name="hover_and_click",
)
async def hover_and_click(
    menu_name: Annotated[str, "Menu to hover over (e.g., 'Products')"],
    submenu_item: Annotated[str, "Submenu item to click (e.g., 'Route')"],
    submenu_timeout: Annotated[float, "Timeout in seconds to wait for submenu"] = 3.0,
) -> Annotated[str, "Result of hover and click action"]:
    """Single-level hover and click."""
    return await multi_level_navigation([menu_name, submenu_item], submenu_timeout)


@tool(
    agent_names=["browser_nav_agent"],
    description="""Simple hover over a menu item and wait for submenu.
    Use this when you just want to hover and wait, without clicking.""",
    name="hover_menu",
)
async def hover_menu(
    menu_name: Annotated[str, "Menu to hover over (e.g., 'Products')"],
    submenu_timeout: Annotated[float, "Timeout in seconds to wait for submenu"] = 3.0,
) -> Annotated[str, "Result of hover action"]:
    """Simple hover without clicking."""
    logger.info(f"Hovering over menu: {menu_name}")
    
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    
    if page is None:
        raise ValueError("No active page found.")
    
    try:
        from .get_interactive_elements import get_interactive_elements
        
        # Get current interactive elements
        interactive_elements_result = await get_interactive_elements()
        import json
        elements_data = json.loads(interactive_elements_result)
        
        # Find the menu element
        menu_selector = find_menu_element(elements_data, menu_name)
        
        if not menu_selector:
            return f"Menu item '{menu_name}' not found"
        
        # Hover over the menu
        hover_result = await hover_with_submenu(
            selector=menu_selector,
            wait_for_submenu=True,
            submenu_timeout=submenu_timeout,
            maintain_hover=True,
            wait_before_execution=0.3
        )
        
        return f"Successfully hovered over '{menu_name}': {hover_result}"
        
    except Exception as e:
        logger.exception(f"Error hovering menu: {e}")
        return f"Failed to hover menu: {str(e)}"


@tool(
    agent_names=["browser_nav_agent"],
    description="""Releases hover state from a menu item.""",
    name="release_menu_hover",
)
async def release_menu_hover(
    menu_name: Annotated[str, "Menu to release hover from (e.g., 'Products')"],
) -> Annotated[str, "Result of releasing hover"]:
    """Release hover from a specific menu."""
    logger.info(f"Releasing hover from menu: {menu_name}")
    
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    
    if page is None:
        raise ValueError("No active page found.")
    
    try:
        from .get_interactive_elements import get_interactive_elements
        
        # Get current interactive elements
        interactive_elements_result = await get_interactive_elements()
        import json
        elements_data = json.loads(interactive_elements_result)
        
        # Find the menu element
        menu_selector = find_menu_element(elements_data, menu_name)
        
        if not menu_selector:
            return f"Menu item '{menu_name}' not found"
        
        # Release hover
        result = await release_hover(selector=menu_selector)
        
        return f"Successfully released hover from '{menu_name}': {result}"
        
    except Exception as e:
        logger.exception(f"Error releasing hover: {e}")
        return f"Failed to release hover: {str(e)}" 