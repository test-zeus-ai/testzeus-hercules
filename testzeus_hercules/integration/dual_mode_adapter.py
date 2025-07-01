"""
Dual-mode adapter for integrating testzeus_hercules_tools with existing agent system.
"""

import os
import asyncio
from typing import Optional, Dict, Any, List
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.browser_logger import get_browser_logger


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
    
    def prepare_selector(self, selector: str) -> str:
        """Prepare selector based on current mode."""
        if self.is_agent_mode():
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
        error_message: Optional[str] = None
    ) -> None:
        """Log interaction for potential code generation."""
        if not self.is_agent_mode():
            return  # Only log in agent mode for code generation
        
        try:
            from testzeus_hercules_tools.tools import InteractionLogger
            
            if self._tools_logger is None:
                from testzeus_hercules_tools import ToolsConfig
                config = ToolsConfig(enable_logging=True, log_path=get_global_conf().get_proof_path())
                self._tools_logger = InteractionLogger(config)
            
            await self._tools_logger.log_interaction(
                tool_name=tool_name,
                selector=selector,
                action=action,
                success=success,
                mode=self.mode,
                element_info=element_info,
                additional_data=additional_data,
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
                config = ToolsConfig(log_path=get_global_conf().get_proof_path())
                self._code_generator = CodeGenerator(config)
            
            return self._code_generator.generate_from_logger(self._tools_logger, output_filename)
        except ImportError:
            return None
        except Exception:
            return None


_dual_mode_adapter = DualModeAdapter()


def get_dual_mode_adapter() -> DualModeAdapter:
    """Get the global dual-mode adapter instance."""
    return _dual_mode_adapter
