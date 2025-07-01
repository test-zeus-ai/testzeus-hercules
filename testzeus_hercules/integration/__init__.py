"""
Integration module for testzeus_hercules_tools package.
Provides seamless integration between agent mode and code mode operations.
"""

from .dual_mode_adapter import DualModeAdapter
from .code_generator_integration import CodeGeneratorIntegration

__all__ = ["DualModeAdapter", "CodeGeneratorIntegration"]
