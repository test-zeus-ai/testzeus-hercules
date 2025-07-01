"""
Dual-mode adapter for integrating testzeus_hercules_tools with existing agent system.
"""

import os
import asyncio
from typing import Optional, Dict, Any, List

try:
    from testzeus_hercules.config import get_global_conf
    _HAS_CONFIG = True
except ImportError:
    _HAS_CONFIG = False
    
try:
    from testzeus_hercules.core.browser_logger import get_browser_logger
    _HAS_BROWSER_LOGGER = True
except ImportError:
    _HAS_BROWSER_LOGGER = False


class DualModeAdapter:
    """Adapter to enable dual-mode functionality in existing tools."""
    
    def __init__(self):
        self.mode = os.getenv("TESTZEUS_TOOLS_MODE", "agent")
        self._tools_logger = None
        self._code_generator = None
    
    def get_mode(self) -> str:
        """Get current operation mode."""
        return self.mode
    
    def set_mode(self, mode: str) -> None:
        """Set operation mode."""
        if mode not in ["agent", "code"]:
            raise ValueError("Mode must be 'agent' or 'code'")
        self.mode = mode
        os.environ["TESTZEUS_TOOLS_MODE"] = mode
    
    def is_agent_mode(self) -> bool:
        """Check if running in agent mode."""
        return self.mode == "agent"
    
    def is_code_mode(self) -> bool:
        """Check if running in code mode."""
        return self.mode == "code"
    
    def prepare_selector(self, selector: str, tool_type: str = "browser") -> str:
        """Prepare selector based on current mode and tool type."""
        if tool_type == "browser" and self.is_agent_mode():
            if "md=" not in selector and not selector.startswith("[") and not selector.startswith("/"):
                return f"[md='{selector}']"
        return selector
    
    async def log_interaction_for_code_generation(
        self,
        tool_name: str,
        selector: str,
        action: str,
        success: bool,
        element_info: Optional[Dict[str, Any]] = None,
        additional_data: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        tool_type: str = "browser"
    ) -> None:
        """Log interaction for potential code generation."""
        if not self.is_agent_mode():
            return  # Only log in agent mode for code generation
        
        try:
            from testzeus_hercules_tools.tools import InteractionLogger
            
            if self._tools_logger is None:
                from testzeus_hercules_tools import ToolsConfig
                log_path = None
                if _HAS_CONFIG:
                    try:
                        log_path = get_global_conf().get_proof_path()
                    except:
                        log_path = "/tmp/testzeus_logs"
                else:
                    log_path = "/tmp/testzeus_logs"
                config = ToolsConfig(enable_logging=True, log_path=log_path)
                self._tools_logger = InteractionLogger(config)
            
            enhanced_additional_data = additional_data.copy() if additional_data else {}
            enhanced_additional_data["tool_type"] = tool_type
            
            if tool_type != "browser":
                enhanced_additional_data["original_selector"] = selector
                
                # Map tool-specific identifiers for better code generation
                if tool_name in ["execute_select_query", "sql_query"]:
                    enhanced_additional_data["connection_string"] = selector
                elif tool_name in ["http_request", "api_call"]:
                    enhanced_additional_data["api_url"] = selector
                elif tool_name in ["test_page_accessibility", "accessibility_test"]:
                    enhanced_additional_data["page_url"] = selector
                elif tool_name in ["run_security_scan", "security_scan"]:
                    enhanced_additional_data["target_url"] = selector
                elif tool_name in ["wait_for_seconds", "wait_until_condition"]:
                    enhanced_additional_data["wait_identifier"] = selector
            
            await self._tools_logger.log_interaction(
                tool_name=tool_name,
                selector=selector,
                action=action,
                success=success,
                mode=self.mode,
                element_info=element_info,
                additional_data=enhanced_additional_data,
                error_message=error_message
            )
        except ImportError:
            pass
        except Exception:
            pass
    
    async def generate_code_from_session(self, output_filename: str = "generated_test.py") -> Optional[str]:
        """Generate code from current session interactions."""
        if not self.is_agent_mode() or self._tools_logger is None:
            return None
        
        try:
            from testzeus_hercules_tools.tools import CodeGenerator
            from testzeus_hercules_tools import ToolsConfig
            
            if self._code_generator is None:
                log_path = None
                if _HAS_CONFIG:
                    try:
                        log_path = get_global_conf().get_proof_path()
                    except:
                        log_path = "/tmp/testzeus_logs"
                else:
                    log_path = "/tmp/testzeus_logs"
                config = ToolsConfig(log_path=log_path)
                self._code_generator = CodeGenerator(config)
            
            return self._code_generator.generate_from_logger(self._tools_logger, output_filename)
        except ImportError:
            return None
        except Exception:
            return None
    
    def get_tool_type(self, tool_name: str) -> str:
        """Determine tool type based on tool name."""
        browser_tools = {
            "click_element", "enter_text", "hover_element", "select_dropdown",
            "open_url", "press_key_combination", "upload_file", "get_page_text",
            "get_interactive_elements", "click_using_selector", "enter_text_using_selector",
            "hover", "dropdown_using_selector"
        }
        
        sql_tools = {"execute_select_query", "sql_query", "sql_calls"}
        api_tools = {"http_request", "api_call", "api_calls"}
        accessibility_tools = {"test_page_accessibility", "accessibility_test", "accessibility_calls"}
        time_tools = {"wait_for_seconds", "wait_until_condition", "timer_tool"}
        security_tools = {"run_security_scan", "security_scan", "api_sec_calls"}
        
        if tool_name in browser_tools:
            return "browser"
        elif tool_name in sql_tools:
            return "sql"
        elif tool_name in api_tools:
            return "api"
        elif tool_name in accessibility_tools:
            return "accessibility"
        elif tool_name in time_tools:
            return "time"
        elif tool_name in security_tools:
            return "security"
        else:
            return "browser"  # Default to browser for unknown tools
    
    async def log_tool_interaction(
        self,
        tool_name: str,
        selector: str,
        action: str,
        success: bool,
        **kwargs
    ) -> None:
        """Convenience method to log tool interactions with automatic tool type detection."""
        tool_type = self.get_tool_type(tool_name)
        
        await self.log_interaction_for_code_generation(
            tool_name=tool_name,
            selector=selector,
            action=action,
            success=success,
            tool_type=tool_type,
            **kwargs
        )


_dual_mode_adapter = DualModeAdapter()


def get_dual_mode_adapter() -> DualModeAdapter:
    """Get the global dual-mode adapter instance."""
    return _dual_mode_adapter
