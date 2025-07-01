"""
Configuration management for testzeus_hercules_tools package.
"""

import os
from typing import Optional, Dict, Any
from dataclasses import dataclass, field


@dataclass
class ToolsConfig:
    """Configuration class for testzeus_hercules_tools."""
    
    mode: str = "agent"  # "agent" or "code"
    
    browser_type: str = "chromium"
    headless: bool = True
    browser_resolution: str = "1920,1080"
    
    enable_logging: bool = True
    log_path: Optional[str] = None
    
    default_timeout: int = 30000
    wait_timeout: int = 5000
    
    extra_config: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_env(cls) -> "ToolsConfig":
        """Create configuration from environment variables."""
        return cls(
            mode=os.getenv("TESTZEUS_TOOLS_MODE", "agent"),
            browser_type=os.getenv("BROWSER_TYPE", "chromium"),
            headless=os.getenv("HEADLESS", "true").lower() == "true",
            browser_resolution=os.getenv("BROWSER_RESOLUTION", "1920,1080"),
            enable_logging=os.getenv("ENABLE_LOGGING", "true").lower() == "true",
            log_path=os.getenv("LOG_PATH"),
            default_timeout=int(os.getenv("DEFAULT_TIMEOUT", "30000")),
            wait_timeout=int(os.getenv("WAIT_TIMEOUT", "5000")),
        )
    
    def is_agent_mode(self) -> bool:
        """Check if running in agent mode."""
        return self.mode.lower() == "agent"
    
    def is_code_mode(self) -> bool:
        """Check if running in code mode."""
        return self.mode.lower() == "code"
    
    def get_browser_resolution(self) -> tuple[int, int]:
        """Get browser resolution as tuple."""
        width, height = self.browser_resolution.split(",")
        return int(width), int(height)
