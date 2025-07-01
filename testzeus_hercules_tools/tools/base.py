"""
Base functionality for dual-mode tools.
"""

from typing import Optional, Dict, Any, Union
from playwright.async_api import Page, ElementHandle
from ..config import ToolsConfig
from ..playwright_manager import ToolsPlaywrightManager


class BaseTool:
    """Base class for dual-mode tools."""
    
    def __init__(self, config: Optional[ToolsConfig] = None, playwright_manager: Optional[ToolsPlaywrightManager] = None):
        self.config = config or ToolsConfig.from_env()
        self.playwright_manager = playwright_manager or ToolsPlaywrightManager(self.config)
        
    def _prepare_selector(self, selector: str) -> str:
        """Prepare selector based on current mode."""
        if self.config.is_agent_mode():
            if "md=" not in selector and not selector.startswith("[") and not selector.startswith("/"):
                return f"[md='{selector}']"
        return selector
    
    async def _find_element(self, selector: str, page: Optional[Page] = None) -> Optional[ElementHandle]:
        """Find element using prepared selector."""
        prepared_selector = self._prepare_selector(selector)
        return await self.playwright_manager.find_element(prepared_selector, page)
    
    async def _get_element_info(self, element: ElementHandle, page: Optional[Page] = None) -> Dict[str, Any]:
        """Get comprehensive element information for logging."""
        if page is None:
            page = await self.playwright_manager.get_page()
            
        info = {
            "alternative_selectors": await self.playwright_manager.get_alternative_selectors(element, page),
            "attributes": {},
            "tag_name": "",
            "outer_html": ""
        }
        
        try:
            for attr in ["id", "class", "name", "type", "value", "role", "aria-label", "md"]:
                value = await element.get_attribute(attr)
                if value:
                    info["attributes"][attr] = value
            
            info["tag_name"] = await element.evaluate("el => el.tagName.toLowerCase()")
            
            outer_html = await element.evaluate("el => el.outerHTML")
            info["outer_html"] = outer_html[:500] + "..." if len(outer_html) > 500 else outer_html
            
        except Exception:
            pass
            
        return info
