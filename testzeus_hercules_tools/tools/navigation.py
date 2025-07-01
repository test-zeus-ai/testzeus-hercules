"""
Dual-mode navigation tool for URL opening.
"""

import asyncio
from typing import Optional, Dict, Any
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from .base import BaseTool
from .logger import InteractionLogger
from ..config import ToolsConfig
from ..playwright_manager import ToolsPlaywrightManager


class NavigationTool(BaseTool):
    """Dual-mode navigation tool."""
    
    def __init__(self, config: Optional[ToolsConfig] = None, playwright_manager: Optional[ToolsPlaywrightManager] = None):
        super().__init__(config, playwright_manager)
        self.logger = InteractionLogger(config)


async def open_url(
    url: str,
    timeout: int = 3,
    force_new_tab: bool = False,
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Open URL in browser with dual-mode support.
    
    Args:
        url: URL to navigate to (must include protocol)
        timeout: Additional wait time in seconds after initial load
        force_new_tab: Force opening in a new tab
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and details
    """
    tool = NavigationTool(config, playwright_manager)
    
    try:
        await tool.playwright_manager.initialize()
        page = await tool.playwright_manager.get_page()
        
        url = _ensure_protocol(url)
        
        if page.url == url:
            title = await page.title()
            result = {
                "success": True,
                "message": f"Page already loaded: {url}",
                "url": url,
                "title": title,
                "from_cache": True,
                "mode": tool.config.mode
            }
            
            await tool.logger.log_interaction(
                tool_name="open_url",
                selector=url,
                action="navigate",
                success=True,
                mode=tool.config.mode,
                additional_data={"from_cache": True, "title": title}
            )
            
            return result
        
        response = await page.goto(url, timeout=timeout * 10000)
        
        title = await page.title()
        final_url = page.url
        status = response.status if response else None
        ok = response.ok if response else False
        
        if timeout > 0:
            await asyncio.sleep(timeout)
        
        result = {
            "success": True,
            "message": f"Page loaded: {final_url}",
            "url": url,
            "final_url": final_url,
            "title": title,
            "status_code": status,
            "ok": ok,
            "from_cache": False,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="open_url",
            selector=url,
            action="navigate",
            success=True,
            mode=tool.config.mode,
            additional_data={
                "final_url": final_url,
                "title": title,
                "status_code": status,
                "ok": ok
            }
        )
        
        return result
        
    except PlaywrightTimeoutError as e:
        result = {
            "success": False,
            "error": f"Timeout error opening URL: {url}",
            "url": url,
            "timeout_seconds": timeout,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="open_url",
            selector=url,
            action="navigate",
            success=False,
            error_message=str(e),
            mode=tool.config.mode,
            additional_data={"timeout_seconds": timeout}
        )
        
        return result
        
    except Exception as e:
        result = {
            "success": False,
            "error": f"Error opening URL: {str(e)}",
            "url": url,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="open_url",
            selector=url,
            action="navigate",
            success=False,
            error_message=str(e),
            mode=tool.config.mode
        )
        
        return result


def _ensure_protocol(url: str) -> str:
    """Ensure URL has a protocol."""
    special_schemes = [
        "about:", "chrome:", "edge:", "brave:", "firefox:", 
        "safari:", "data:", "file:", "view-source:"
    ]
    
    if any(url.startswith(scheme) for scheme in special_schemes):
        return url
    
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    
    return url
