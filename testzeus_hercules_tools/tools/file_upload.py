"""
Dual-mode file upload tool.
"""

from typing import Optional, Dict, Any
from playwright.async_api import Page
from .base import BaseTool
from .logger import InteractionLogger
from ..config import ToolsConfig
from ..playwright_manager import ToolsPlaywrightManager


class FileUploadTool(BaseTool):
    """Dual-mode file upload tool."""
    
    def __init__(self, config: Optional[ToolsConfig] = None, playwright_manager: Optional[ToolsPlaywrightManager] = None):
        super().__init__(config, playwright_manager)
        self.logger = InteractionLogger(config)


async def upload_file(
    selector: str,
    file_path: str,
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Upload file to element using dual-mode selector.
    
    Args:
        selector: Element selector (md ID in agent mode, CSS/XPath in code mode)
        file_path: Path to file to upload
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and details
    """
    tool = FileUploadTool(config, playwright_manager)
    page = await tool.playwright_manager.get_page()
    
    try:
        element = await tool._find_element(selector, page)
        if not element:
            result = {
                "success": False,
                "error": f"Element not found with selector: {selector}",
                "selector": selector,
                "file_path": file_path,
                "mode": tool.config.mode
            }
            
            await tool.logger.log_interaction(
                tool_name="upload_file",
                selector=selector,
                action="upload",
                success=False,
                error_message=result["error"],
                mode=tool.config.mode,
                additional_data={"file_path": file_path}
            )
            
            return result
        
        element_info = await tool._get_element_info(element, page)
        element_type = await element.evaluate("el => el.type")
        
        if element_type == "file":
            await element.set_input_files(file_path)
        else:
            async with page.expect_file_chooser() as fc_info:
                await element.click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(file_path)
        
        result = {
            "success": True,
            "message": f"Successfully uploaded file '{file_path}' to element with selector: {selector}",
            "selector": selector,
            "file_path": file_path,
            "element_type": element_type,
            "mode": tool.config.mode,
            "element_info": element_info
        }
        
        await tool.logger.log_interaction(
            tool_name="upload_file",
            selector=selector,
            action="upload",
            success=True,
            mode=tool.config.mode,
            element_info=element_info,
            additional_data={"file_path": file_path, "element_type": element_type}
        )
        
        return result
        
    except Exception as e:
        result = {
            "success": False,
            "error": f"Failed to upload file: {str(e)}",
            "selector": selector,
            "file_path": file_path,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="upload_file",
            selector=selector,
            action="upload",
            success=False,
            error_message=str(e),
            mode=tool.config.mode,
            additional_data={"file_path": file_path}
        )
        
        return result
