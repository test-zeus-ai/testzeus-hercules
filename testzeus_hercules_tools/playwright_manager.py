"""
Playwright manager for testzeus_hercules_tools package.
Simplified version focused on dual-mode functionality.
"""

import asyncio
from typing import Optional, Dict, Any, Union
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, ElementHandle
from .config import ToolsConfig


class ToolsPlaywrightManager:
    """Simplified Playwright manager for testzeus_hercules_tools."""
    
    def __init__(self, config: Optional[ToolsConfig] = None):
        self.config = config or ToolsConfig.from_env()
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        
    async def initialize(self) -> None:
        """Initialize Playwright browser."""
        if self._playwright is None:
            self._playwright = await async_playwright().start()
            
        if self._browser is None:
            browser_type = getattr(self._playwright, self.config.browser_type)
            self._browser = await browser_type.launch(
                headless=self.config.headless
            )
            
        if self._context is None:
            width, height = self.config.get_browser_resolution()
            self._context = await self._browser.new_context(
                viewport={"width": width, "height": height}
            )
            
        if self._page is None:
            self._page = await self._context.new_page()
    
    async def get_page(self) -> Page:
        """Get the current page."""
        if self._page is None:
            await self.initialize()
        return self._page
    
    async def find_element(self, selector: str, page: Optional[Page] = None) -> Optional[ElementHandle]:
        """Find element using selector."""
        if page is None:
            page = await self.get_page()
            
        try:
            if self.config.is_agent_mode() and "md=" not in selector:
                selector = f"[md='{selector}']"
            
            element = await page.wait_for_selector(selector, timeout=self.config.wait_timeout)
            return element
        except Exception:
            return None
    
    async def get_alternative_selectors(self, element: ElementHandle, page: Optional[Page] = None) -> Dict[str, str]:
        """Generate alternative selectors for an element."""
        if page is None:
            page = await self.get_page()
            
        selectors = {}
        
        try:
            xpath = await page.evaluate(
                """(element) => {
                    const getXPath = (elm) => {
                        if (!elm || !elm.nodeType) return null;
                        const segs = [];
                        while (elm && elm.nodeType === 1) {
                            if (elm.hasAttribute('id')) {
                                segs.unshift(`//*[@id="${elm.getAttribute('id')}"]`);
                                return segs.join('/');
                            } else {
                                let sib = elm;
                                let nth = 1;
                                for (sib = sib.previousSibling; sib; sib = sib.previousSibling) {
                                    if (sib.nodeType === 1 && sib.tagName === elm.tagName) nth++;
                                }
                                segs.unshift(elm.tagName.toLowerCase() + '[' + nth + ']');
                            }
                            elm = elm.parentNode;
                        }
                        return segs.length ? '/' + segs.join('/') : null;
                    };
                    return getXPath(element);
                }""",
                element,
            )
            if xpath:
                selectors["xpath"] = xpath
                
            css_selector = await page.evaluate(
                """(element) => {
                    if (element.id) return '#' + element.id;
                    if (element.className) return '.' + element.className.split(' ').join('.');
                    return element.tagName.toLowerCase();
                }""",
                element,
            )
            if css_selector:
                selectors["css"] = css_selector
                
            aria = await page.evaluate(
                """(element) => {
                    if (element.getAttribute('role')) {
                        return `[role="${element.getAttribute('role')}"]`;
                    }
                    if (element.getAttribute('aria-label')) {
                        return `[aria-label="${element.getAttribute('aria-label')}"]`;
                    }
                    return null;
                }""",
                element,
            )
            if aria:
                selectors["aria"] = aria
                
        except Exception:
            pass
            
        return selectors
    
    async def close(self) -> None:
        """Close browser and cleanup."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
