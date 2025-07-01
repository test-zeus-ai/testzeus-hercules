"""
Dual-mode page content retrieval tools.
"""

import json
from typing import Optional, Dict, Any, List
from playwright.async_api import Page
from .base import BaseTool
from .logger import InteractionLogger
from ..config import ToolsConfig
from ..playwright_manager import ToolsPlaywrightManager


class PageContentTool(BaseTool):
    """Dual-mode page content tool."""
    
    def __init__(self, config: Optional[ToolsConfig] = None, playwright_manager: Optional[ToolsPlaywrightManager] = None):
        super().__init__(config, playwright_manager)
        self.logger = InteractionLogger(config)


async def get_page_text(
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Get page text content with dual-mode support.
    
    Args:
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and text content
    """
    tool = PageContentTool(config, playwright_manager)
    page = await tool.playwright_manager.get_page()
    
    try:
        text_content = await _get_filtered_text_content(page)
        
        result = {
            "success": True,
            "text_content": text_content,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="get_page_text",
            selector="page",
            action="get_text",
            success=True,
            mode=tool.config.mode,
            additional_data={"text_length": len(text_content)}
        )
        
        return result
        
    except Exception as e:
        result = {
            "success": False,
            "error": f"Failed to get page text: {str(e)}",
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="get_page_text",
            selector="page",
            action="get_text",
            success=False,
            error_message=str(e),
            mode=tool.config.mode
        )
        
        return result


async def get_interactive_elements(
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Get interactive elements on page with dual-mode support.
    
    Args:
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and interactive elements
    """
    tool = PageContentTool(config, playwright_manager)
    page = await tool.playwright_manager.get_page()
    
    try:
        elements = await _get_interactive_elements_data(page)
        
        result = {
            "success": True,
            "elements": elements,
            "element_count": len(elements),
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="get_interactive_elements",
            selector="page",
            action="get_elements",
            success=True,
            mode=tool.config.mode,
            additional_data={"element_count": len(elements)}
        )
        
        return result
        
    except Exception as e:
        result = {
            "success": False,
            "error": f"Failed to get interactive elements: {str(e)}",
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="get_interactive_elements",
            selector="page",
            action="get_elements",
            success=False,
            error_message=str(e),
            mode=tool.config.mode
        )
        
        return result


async def _get_filtered_text_content(page: Page) -> str:
    """Get filtered text content from page."""
    text_content = await page.evaluate("""
        () => {
            function getTextSkippingScriptsStyles(root) {
                if (!root) return '';
                
                const walker = document.createTreeWalker(
                    root,
                    NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT,
                    {
                        acceptNode(node) {
                            if (node.nodeType === Node.ELEMENT_NODE) {
                                const tag = node.tagName.toLowerCase();
                                if (tag === 'script' || tag === 'style') {
                                    return NodeFilter.FILTER_REJECT;
                                }
                            }
                            return NodeFilter.FILTER_ACCEPT;
                        }
                    }
                );
                
                let textContent = '';
                while (walker.nextNode()) {
                    const node = walker.currentNode;
                    if (node.nodeType === Node.TEXT_NODE) {
                        textContent += node.nodeValue;
                    }
                }
                return textContent;
            }
            
            let textContent = getTextSkippingScriptsStyles(document.body);
            textContent += getTextSkippingScriptsStyles(document.documentElement);
            
            return textContent.replace(/\\s+/g, ' ').trim();
        }
    """)
    
    return text_content


async def _get_interactive_elements_data(page: Page) -> List[Dict[str, Any]]:
    """Get interactive elements data from page."""
    elements_data = await page.evaluate("""
        () => {
            const interactiveRoles = new Set([
                'button', 'link', 'checkbox', 'radio', 'textbox', 'combobox',
                'listbox', 'menuitem', 'option', 'slider', 'spinbutton',
                'switch', 'tab', 'treeitem'
            ]);
            
            const interactiveTags = new Set([
                'a', 'button', 'input', 'select', 'textarea'
            ]);
            
            const elements = [];
            const walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_ELEMENT,
                {
                    acceptNode(node) {
                        const role = node.getAttribute('role');
                        const tag = node.tagName.toLowerCase();
                        
                        if (interactiveRoles.has(role) || 
                            interactiveTags.has(tag) ||
                            node.hasAttribute('onclick') ||
                            node.tabIndex >= 0) {
                            return NodeFilter.FILTER_ACCEPT;
                        }
                        return NodeFilter.FILTER_SKIP;
                    }
                }
            );
            
            while (walker.nextNode()) {
                const element = walker.currentNode;
                const rect = element.getBoundingClientRect();
                
                if (rect.width > 0 && rect.height > 0) {
                    elements.push({
                        tag: element.tagName.toLowerCase(),
                        role: element.getAttribute('role') || '',
                        name: element.getAttribute('aria-label') || element.textContent?.trim() || '',
                        id: element.id || '',
                        className: element.className || '',
                        type: element.type || '',
                        clickable: element.hasAttribute('onclick') || element.tagName.toLowerCase() === 'button',
                        focusable: element.tabIndex >= 0,
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height)
                    });
                }
            }
            
            return elements;
        }
    """)
    
    return elements_data
