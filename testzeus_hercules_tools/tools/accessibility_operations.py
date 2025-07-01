"""
Dual-mode accessibility testing tool.
"""

import json
import time
from typing import Optional, Dict, Any
from .base import BaseTool
from .logger import InteractionLogger
from ..config import ToolsConfig
from ..playwright_manager import ToolsPlaywrightManager


class AccessibilityTool(BaseTool):
    """Dual-mode accessibility testing tool."""
    
    def __init__(self, config: Optional[ToolsConfig] = None, playwright_manager: Optional[ToolsPlaywrightManager] = None):
        super().__init__(config, playwright_manager)
        self.logger = InteractionLogger(config)


async def test_page_accessibility(
    page_url: Optional[str] = None,
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Test page accessibility using Axe-core with dual-mode support.
    
    Args:
        page_url: URL of page to test (optional, uses current page if not provided)
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and accessibility test results
    """
    tool = AccessibilityTool(config, playwright_manager)
    
    try:
        await tool.playwright_manager.initialize()
        page = await tool.playwright_manager.get_page()
        
        if page_url:
            await page.goto(page_url)
            await page.wait_for_load_state("domcontentloaded")
        
        current_url = page.url
        
        axe_script_url = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"
        
        try:
            axe_script = await page.evaluate(f"""
                fetch("{axe_script_url}").then(res => res.text())
            """)
            await page.add_script_tag(content=axe_script)
        except Exception as e:
            result = {
                "success": False,
                "error": f"Failed to load Axe-core script: {str(e)}",
                "page_url": current_url,
                "mode": tool.config.mode
            }
            
            await tool.logger.log_interaction(
                tool_name="test_page_accessibility",
                selector=current_url,
                action="accessibility_test",
                success=False,
                error_message=str(e),
                mode=tool.config.mode,
                additional_data={"error_type": "script_load"}
            )
            
            return result
        
        # Run accessibility checks
        axe_results = await page.evaluate("""
            async () => {
                return await axe.run();
            }
        """)
        
        violations = axe_results.get("violations", [])
        incomplete = axe_results.get("incomplete", [])
        passes = axe_results.get("passes", [])
        
        failure_summaries = []
        for violation in violations:
            nodes = violation.get("nodes", [])
            for node in nodes:
                if node.get("failureSummary"):
                    failure_summaries.append(node.get("failureSummary"))
        
        has_violations = len(violations) > 0
        
        result = {
            "success": not has_violations,
            "page_url": current_url,
            "violations_count": len(violations),
            "incomplete_count": len(incomplete),
            "passes_count": len(passes),
            "violations": violations,
            "incomplete": incomplete,
            "failure_summaries": failure_summaries,
            "full_results": axe_results,
            "mode": tool.config.mode
        }
        
        if not has_violations:
            result["message"] = "No accessibility violations found"
        else:
            result["message"] = f"Found {len(violations)} accessibility violations"
        
        await tool.logger.log_interaction(
            tool_name="test_page_accessibility",
            selector=current_url,
            action="accessibility_test",
            success=not has_violations,
            mode=tool.config.mode,
            additional_data={
                "violations_count": len(violations),
                "incomplete_count": len(incomplete),
                "passes_count": len(passes),
                "has_violations": has_violations
            }
        )
        
        return result
        
    except Exception as e:
        result = {
            "success": False,
            "error": f"Accessibility test failed: {str(e)}",
            "page_url": page_url or "current_page",
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="test_page_accessibility",
            selector=page_url or "current_page",
            action="accessibility_test",
            success=False,
            error_message=str(e),
            mode=tool.config.mode,
            additional_data={"error_type": "unexpected"}
        )
        
        return result
