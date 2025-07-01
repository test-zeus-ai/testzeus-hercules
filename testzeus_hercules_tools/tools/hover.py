"""
Dual-mode hover tool.
"""

from typing import Optional, Dict, Any
from playwright.async_api import Page
from .base import BaseTool
from .logger import InteractionLogger
from ..config import ToolsConfig
from ..playwright_manager import ToolsPlaywrightManager


class HoverTool(BaseTool):
    """Dual-mode hover tool."""
    
    def __init__(self, config: Optional[ToolsConfig] = None, playwright_manager: Optional[ToolsPlaywrightManager] = None):
        super().__init__(config, playwright_manager)
        self.logger = InteractionLogger(config)


async def hover_element(
    selector: str,
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Hover over an element using dual-mode selector.
    
    Args:
        selector: Element selector (md ID in agent mode, CSS/XPath in code mode)
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and details
    """
    tool = HoverTool(config, playwright_manager)
    page = await tool.playwright_manager.get_page()
    
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
                tool_name="hover_element",
                selector=selector,
                action="hover",
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
        
        await element.hover()
        
        result = {
            "success": True,
            "message": f"Successfully hovered over element with selector: {selector}",
            "selector": selector,
            "mode": tool.config.mode,
            "element_info": element_info
        }
        
        await tool.logger.log_interaction(
            tool_name="hover_element",
            selector=selector,
            action="hover",
            success=True,
            mode=tool.config.mode,
            element_info=element_info
        )
        
        return result
        
    except Exception as e:
        result = {
            "success": False,
            "error": f"Failed to hover over element: {str(e)}",
            "selector": selector,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="hover_element",
            selector=selector,
            action="hover",
            success=False,
            error_message=str(e),
            mode=tool.config.mode
        )
        
        return result
