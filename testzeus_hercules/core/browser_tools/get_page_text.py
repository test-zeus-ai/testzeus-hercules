import json
import os
import time
from typing import Annotated, Any, Union

from playwright.sync_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import wait_for_non_loading_dom_state
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["text_nav_agent"],
    description="Get the text content of the current page.",
    name="get_page_text",
)
def get_page_text() -> (
    Annotated[str, "DOM content based on type to analyze and decide"]
):
    """
    Get the text content of the current page.
    """
    try:
        browser_manager = PlaywrightManager()
        browser_manager.wait_for_page_and_frames_load()
        page = browser_manager.get_current_page()
        page.wait_for_load_state()

        # Wait for any dynamic content to load
        wait_for_non_loading_dom_state(page, 1)

        # Get filtered text content
        text_content = get_filtered_text_content(page)

        return text_content
    except Exception as e:
        logger.error(f"Error getting page text: {str(e)}")
        return str(e)


def get_filtered_text_content(page: Page) -> str:
    """
    Get filtered text content from the page, excluding hidden elements and unwanted content.

    Args:
        page: The Playwright page instance

    Returns:
        str: The filtered text content
    """
    text_content = page.evaluate(
        """() => {
        function isElementVisible(element) {
            if (!element) return false;
            
            const style = window.getComputedStyle(element);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                return false;
            }
            
            const rect = element.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        }
        
        function shouldIncludeNode(node) {
            if (node.nodeType === Node.TEXT_NODE) {
                return node.textContent.trim().length > 0;
            }
            
            if (node.nodeType !== Node.ELEMENT_NODE) {
                return false;
            }
            
            const element = node;
            
            // Skip hidden elements
            if (!isElementVisible(element)) {
                return false;
            }
            
            // Skip script, style, and other non-content elements
            const excludeTags = ['SCRIPT', 'STYLE', 'NOSCRIPT', 'IFRAME', 'TEMPLATE'];
            if (excludeTags.includes(element.tagName)) {
                return false;
            }
            
            return true;
        }
        
        function getVisibleText(node, textParts) {
            if (!shouldIncludeNode(node)) {
                return;
            }
            
            if (node.nodeType === Node.TEXT_NODE) {
                const text = node.textContent.trim();
                if (text) {
                    textParts.push(text);
                }
                return;
            }
            
            // Handle child nodes
            for (const child of node.childNodes) {
                getVisibleText(child, textParts);
            }
        }
        
        const textParts = [];
        getVisibleText(document.body, textParts);
        return textParts.join(' ');
    }"""
    )

    return clean_text(text_content)


def clean_text(text: str) -> str:
    """Clean and normalize text content."""
    import re

    # Replace multiple spaces with a single space
    text = re.sub(r"\s+", " ", text)

    # Remove leading/trailing whitespace
    text = text.strip()

    return text


def get_filtered_text_content(page: Page) -> str:
    """
    Get filtered text content from the page, excluding hidden elements and unwanted content.

    Args:
        page: The Playwright page instance

    Returns:
        str: The filtered text content
    """
    text_content = page.evaluate(
        """() => {
        function isElementVisible(element) {
            if (!element) return false;
            
            const style = window.getComputedStyle(element);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                return false;
            }
            
            const rect = element.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        }
        
        function shouldIncludeNode(node) {
            if (node.nodeType === Node.TEXT_NODE) {
                return node.textContent.trim().length > 0;
            }
            
            if (node.nodeType !== Node.ELEMENT_NODE) {
                return false;
            }
            
            const element = node;
            
            // Skip hidden elements
            if (!isElementVisible(element)) {
                return false;
            }
            
            // Skip script, style, and other non-content elements
            const excludeTags = ['SCRIPT', 'STYLE', 'NOSCRIPT', 'IFRAME', 'TEMPLATE'];
            if (excludeTags.includes(element.tagName)) {
                return false;
            }
            
            return true;
        }
        
        function getVisibleText(node, textParts) {
            if (!shouldIncludeNode(node)) {
                return;
            }
            
            if (node.nodeType === Node.TEXT_NODE) {
                const text = node.textContent.trim();
                if (text) {
                    textParts.push(text);
                }
                return;
            }
            
            // Handle child nodes
            for (const child of node.childNodes) {
                getVisibleText(child, textParts);
            }
        }
        
        const textParts = [];
        getVisibleText(document.body, textParts);
        return textParts.join(' ');
    }"""
    )

    return clean_text(text_content)
