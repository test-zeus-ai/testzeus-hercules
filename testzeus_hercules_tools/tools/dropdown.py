"""
Dual-mode dropdown selection tool.
"""

from typing import Optional, Dict, Any, Union
from playwright.async_api import Page
from .base import BaseTool
from .logger import InteractionLogger
from ..config import ToolsConfig
from ..playwright_manager import ToolsPlaywrightManager


class DropdownTool(BaseTool):
    """Dual-mode dropdown selection tool."""
    
    def __init__(self, config: Optional[ToolsConfig] = None, playwright_manager: Optional[ToolsPlaywrightManager] = None):
        super().__init__(config, playwright_manager)
        self.logger = InteractionLogger(config)


async def select_dropdown(
    selector: str,
    value: Union[str, int],
    by: str = "value",
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Select an option from a dropdown using dual-mode selector.
    
    Args:
        selector: Element selector (md ID in agent mode, CSS/XPath in code mode)
        value: Value to select
        by: Selection method ("value", "text", "index")
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and details
    """
    tool = DropdownTool(config, playwright_manager)
    page = await tool.playwright_manager.get_page()
    
    try:
        element = await tool._find_element(selector, page)
        if not element:
            result = {
                "success": False,
                "error": f"Element not found with selector: {selector}",
                "selector": selector,
                "value": value,
                "mode": tool.config.mode
            }
            
            await tool.logger.log_interaction(
                tool_name="select_dropdown",
                selector=selector,
                action="select",
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
        
        if by == "value":
            await element.select_option(value=str(value))
        elif by == "text":
            await element.select_option(label=str(value))
        elif by == "index":
            await element.select_option(index=int(value))
        else:
            raise ValueError(f"Invalid selection method: {by}")
        
        result = {
            "success": True,
            "message": f"Successfully selected '{value}' from dropdown with selector: {selector}",
            "selector": selector,
            "value": value,
            "by": by,
            "mode": tool.config.mode,
            "element_info": element_info
        }
        
        await tool.logger.log_interaction(
            tool_name="select_dropdown",
            selector=selector,
            action="select",
            success=True,
            mode=tool.config.mode,
            element_info=element_info,
            additional_data={"value": value, "by": by}
        )
        
        return result
        
    except Exception as e:
        result = {
            "success": False,
            "error": f"Failed to select from dropdown: {str(e)}",
            "selector": selector,
            "value": value,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="select_dropdown",
            selector=selector,
            action="select",
            success=False,
            error_message=str(e),
            mode=tool.config.mode
        )
        
        return result
