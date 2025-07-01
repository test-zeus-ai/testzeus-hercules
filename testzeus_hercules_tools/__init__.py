"""
TestZeus Hercules Tools - Dual Mode Package

This package provides dual-mode tools that can operate in:
1. Agent mode: Uses md attributes for element identification (default)
2. Code mode: Uses CSS selectors, XPath, and other standard selectors

The mode is controlled by the caller, with agent mode as default.
"""

from .config import ToolsConfig
from .playwright_manager import ToolsPlaywrightManager
from .tools import *

__version__ = "1.0.0"
__all__ = ["ToolsConfig", "ToolsPlaywrightManager"]
