"""
Dual-mode click tool.
"""

import asyncio
from typing import Optional, Dict, Any
from playwright.async_api import Page
from .base import BaseTool
from .logger import InteractionLogger
from ..config import ToolsConfig
from ..playwright_manager import ToolsPlaywrightManager


class ClickTool(BaseTool):
    """Dual-mode click tool."""
    
    def __init__(self, config: Optional[ToolsConfig] = None, playwright_manager: Optional[ToolsPlaywrightManager] = None):
        super().__init__(config, playwright_manager)
        self.logger = InteractionLogger(config)


async def click_element(
    selector: str,
    click_type: str = "click",
    wait_before: float = 0.0,
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Click an element using dual-mode selector.
    
    Args:
        selector: Element selector (md ID in agent mode, CSS/XPath in code mode)
        click_type: Type of click (click, right_click, double_click, middle_click)
        wait_before: Wait time before clicking
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and details
    """
    tool = ClickTool(config, playwright_manager)
    page = await tool.playwright_manager.get_page()
    
    # Wait before execution if specified
    if wait_before > 0:
        await asyncio.sleep(wait_before)
    
    try:
        element = await tool._find_element(selector, page)
        if not element:
            result = {
                "success": False,
                "error": f"Element not found with selector: {selector}",
                "selector": selector,
                "mode": tool.config.mode
            }
            
            await tool.logger.log_interaction(
                tool_name="click_element",
                selector=selector,
                action=click_type,
                success=False,
                error_message=result["error"],
                mode=tool.config.mode
            )
            
            return result
        
        element_info = await tool._get_element_info(element, page)
        
        try:
            await element.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass  # Continue if scroll fails
        
        if not await element.is_visible():
            result = {
                "success": False,
                "error": f"Element is not visible: {selector}",
                "selector": selector,
                "mode": tool.config.mode
            }
            
            await tool.logger.log_interaction(
                tool_name="click_element",
                selector=selector,
                action=click_type,
                success=False,
                error_message=result["error"],
                mode=tool.config.mode,
                element_info=element_info
            )
            
            return result
        
        if click_type == "right_click":
            await element.click(button="right")
        elif click_type == "double_click":
            await element.dblclick()
        elif click_type == "middle_click":
            await element.click(button="middle")
        else:  # Default to regular click
            await element.click()
        
        result = {
            "success": True,
            "message": f"Successfully {click_type} element with selector: {selector}",
            "selector": selector,
            "mode": tool.config.mode,
            "element_info": element_info
        }
        
        await tool.logger.log_interaction(
            tool_name="click_element",
            selector=selector,
            action=click_type,
            success=True,
            mode=tool.config.mode,
            element_info=element_info,
            additional_data={"click_type": click_type}
        )
        
        return result
        
    except Exception as e:
        result = {
            "success": False,
            "error": f"Failed to click element: {str(e)}",
            "selector": selector,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="click_element",
            selector=selector,
            action=click_type,
            success=False,
            error_message=str(e),
            mode=tool.config.mode
        )
        
        return result
