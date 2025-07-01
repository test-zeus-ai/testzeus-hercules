"""
Dual-mode keyboard interaction tool.
"""

import asyncio
from typing import Optional, Dict, Any
from .base import BaseTool
from .logger import InteractionLogger
from ..config import ToolsConfig
from ..playwright_manager import ToolsPlaywrightManager


class KeyboardTool(BaseTool):
    """Dual-mode keyboard tool."""
    
    def __init__(self, config: Optional[ToolsConfig] = None, playwright_manager: Optional[ToolsPlaywrightManager] = None):
        super().__init__(config, playwright_manager)
        self.logger = InteractionLogger(config)


async def press_key_combination(
    key_combination: str,
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Press key combination with dual-mode support.
    
    Args:
        key_combination: Key combination to press (e.g., "Enter", "Control+C")
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and details
    """
    tool = KeyboardTool(config, playwright_manager)
    page = await tool.playwright_manager.get_page()
    
    try:
        keys = key_combination.split("+")
        
        for key in keys[:-1]:
            await page.keyboard.down(key)
        
        await page.keyboard.press(keys[-1])
        
        for key in keys[:-1]:
            await page.keyboard.up(key)
        
        await asyncio.sleep(0.1)
        
        result = {
            "success": True,
            "message": f"Key combination '{key_combination}' executed successfully",
            "key_combination": key_combination,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="press_key_combination",
            selector=key_combination,
            action="keypress",
            success=True,
            mode=tool.config.mode,
            additional_data={"keys": keys}
        )
        
        return result
        
    except Exception as e:
        result = {
            "success": False,
            "error": f"Failed to press key combination: {str(e)}",
            "key_combination": key_combination,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="press_key_combination",
            selector=key_combination,
            action="keypress",
            success=False,
            error_message=str(e),
            mode=tool.config.mode
        )
        
        return result
