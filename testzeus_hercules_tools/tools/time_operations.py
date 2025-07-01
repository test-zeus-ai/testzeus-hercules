"""
Dual-mode time operations tool.
"""

import asyncio
import time
from typing import Optional, Dict, Any
from .base import BaseTool
from .logger import InteractionLogger
from ..config import ToolsConfig
from ..playwright_manager import ToolsPlaywrightManager


class TimeOperationsTool(BaseTool):
    """Dual-mode time operations tool."""
    
    def __init__(self, config: Optional[ToolsConfig] = None, playwright_manager: Optional[ToolsPlaywrightManager] = None):
        super().__init__(config, playwright_manager)
        self.logger = InteractionLogger(config)


async def wait_for_seconds(
    seconds: float,
    reason: str = "Static wait",
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Wait for specified number of seconds with dual-mode support.
    
    Args:
        seconds: Number of seconds to wait
        reason: Reason for waiting (for logging purposes)
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and wait details
    """
    tool = TimeOperationsTool(config, playwright_manager)
    
    try:
        if seconds < 0:
            result = {
                "success": False,
                "error": "Wait time cannot be negative",
                "seconds": seconds,
                "reason": reason,
                "mode": tool.config.mode
            }
            
            await tool.logger.log_interaction(
                tool_name="wait_for_seconds",
                selector=str(seconds),
                action="wait",
                success=False,
                error_message="Negative wait time",
                mode=tool.config.mode,
                additional_data={"seconds": seconds, "reason": reason}
            )
            
            return result
        
        start_time = time.perf_counter()
        await asyncio.sleep(seconds)
        actual_duration = time.perf_counter() - start_time
        
        result = {
            "success": True,
            "message": f"Successfully waited for {seconds} seconds",
            "requested_seconds": seconds,
            "actual_duration": actual_duration,
            "reason": reason,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="wait_for_seconds",
            selector=str(seconds),
            action="wait",
            success=True,
            mode=tool.config.mode,
            additional_data={
                "requested_seconds": seconds,
                "actual_duration": actual_duration,
                "reason": reason
            }
        )
        
        return result
        
    except Exception as e:
        result = {
            "success": False,
            "error": f"Wait operation failed: {str(e)}",
            "seconds": seconds,
            "reason": reason,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="wait_for_seconds",
            selector=str(seconds),
            action="wait",
            success=False,
            error_message=str(e),
            mode=tool.config.mode,
            additional_data={"seconds": seconds, "reason": reason}
        )
        
        return result


async def wait_until_condition(
    condition_check: str,
    max_wait_seconds: float = 30.0,
    check_interval: float = 1.0,
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Wait until a condition is met with dual-mode support.
    
    Args:
        condition_check: JavaScript condition to check on page
        max_wait_seconds: Maximum time to wait
        check_interval: Interval between condition checks
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and wait details
    """
    tool = TimeOperationsTool(config, playwright_manager)
    
    try:
        await tool.playwright_manager.initialize()
        page = await tool.playwright_manager.get_page()
        
        start_time = time.perf_counter()
        elapsed = 0
        checks_performed = 0
        
        while elapsed < max_wait_seconds:
            try:
                condition_met = await page.evaluate(condition_check)
                checks_performed += 1
                
                if condition_met:
                    result = {
                        "success": True,
                        "message": f"Condition met after {elapsed:.2f} seconds",
                        "condition": condition_check,
                        "elapsed_seconds": elapsed,
                        "checks_performed": checks_performed,
                        "max_wait_seconds": max_wait_seconds,
                        "mode": tool.config.mode
                    }
                    
                    await tool.logger.log_interaction(
                        tool_name="wait_until_condition",
                        selector=condition_check,
                        action="conditional_wait",
                        success=True,
                        mode=tool.config.mode,
                        additional_data={
                            "elapsed_seconds": elapsed,
                            "checks_performed": checks_performed,
                            "condition_met": True
                        }
                    )
                    
                    return result
                
            except Exception as eval_error:
                pass
            
            await asyncio.sleep(check_interval)
            elapsed = time.perf_counter() - start_time
        
        result = {
            "success": False,
            "error": f"Condition not met within {max_wait_seconds} seconds",
            "condition": condition_check,
            "elapsed_seconds": elapsed,
            "checks_performed": checks_performed,
            "max_wait_seconds": max_wait_seconds,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="wait_until_condition",
            selector=condition_check,
            action="conditional_wait",
            success=False,
            error_message="Timeout reached",
            mode=tool.config.mode,
            additional_data={
                "elapsed_seconds": elapsed,
                "checks_performed": checks_performed,
                "condition_met": False,
                "timeout": True
            }
        )
        
        return result
        
    except Exception as e:
        result = {
            "success": False,
            "error": f"Conditional wait failed: {str(e)}",
            "condition": condition_check,
            "max_wait_seconds": max_wait_seconds,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="wait_until_condition",
            selector=condition_check,
            action="conditional_wait",
            success=False,
            error_message=str(e),
            mode=tool.config.mode,
            additional_data={"error_type": "unexpected"}
        )
        
        return result
