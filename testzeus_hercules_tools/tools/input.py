"""
Dual-mode text input tool.
"""

import asyncio
from typing import Optional, Dict, Any
from playwright.async_api import Page
from .base import BaseTool
from .logger import InteractionLogger
from ..config import ToolsConfig
from ..playwright_manager import ToolsPlaywrightManager


class InputTool(BaseTool):
    """Dual-mode text input tool."""
    
    def __init__(self, config: Optional[ToolsConfig] = None, playwright_manager: Optional[ToolsPlaywrightManager] = None):
        super().__init__(config, playwright_manager)
        self.logger = InteractionLogger(config)


async def enter_text(
    selector: str,
    text: str,
    clear_first: bool = True,
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Enter text into an element using dual-mode selector.
    
    Args:
        selector: Element selector (md ID in agent mode, CSS/XPath in code mode)
        text: Text to enter
        clear_first: Whether to clear existing text first
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and details
    """
    tool = InputTool(config, playwright_manager)
    page = await tool.playwright_manager.get_page()
    
    try:
        element = await tool._find_element(selector, page)
        if not element:
            result = {
                "success": False,
                "error": f"Element not found with selector: {selector}",
                "selector": selector,
                "text": text,
                "mode": tool.config.mode
            }
            
            await tool.logger.log_interaction(
                tool_name="enter_text",
                selector=selector,
                action="input",
                success=False,
                error_message=result["error"],
                mode=tool.config.mode
            )
            
            return result
        
        element_info = await tool._get_element_info(element, page)
        
        await element.focus()
        
        if clear_first:
            await element.select_text()
            await page.keyboard.press("Delete")
        
        await element.type(text, delay=10)  # Small delay between keystrokes
        
        result = {
            "success": True,
            "message": f"Successfully entered text into element with selector: {selector}",
            "selector": selector,
            "text": text,
            "mode": tool.config.mode,
            "element_info": element_info
        }
        
        await tool.logger.log_interaction(
            tool_name="enter_text",
            selector=selector,
            action="input",
            success=True,
            mode=tool.config.mode,
            element_info=element_info,
            additional_data={"text": text, "clear_first": clear_first}
        )
        
        return result
        
    except Exception as e:
        result = {
            "success": False,
            "error": f"Failed to enter text: {str(e)}",
            "selector": selector,
            "text": text,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="enter_text",
            selector=selector,
            action="input",
            success=False,
            error_message=str(e),
            mode=tool.config.mode
        )
        
        return result
