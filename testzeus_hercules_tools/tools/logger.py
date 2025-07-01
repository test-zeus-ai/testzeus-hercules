"""
Interaction logger for dual-mode tools.
"""

import json
import os
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from ..config import ToolsConfig


@dataclass
class InteractionLog:
    """Data class for interaction logs."""
    timestamp: float
    tool_name: str
    selector: str
    action: str
    success: bool
    mode: str
    error_message: Optional[str] = None
    element_info: Optional[Dict[str, Any]] = None
    additional_data: Optional[Dict[str, Any]] = None


class InteractionLogger:
    """Logger for tool interactions with dual-mode support."""
    
    _shared_interactions: List[InteractionLog] = []
    _shared_log_file: Optional[str] = None
    
    def __init__(self, config: Optional[ToolsConfig] = None):
        self.config = config or ToolsConfig.from_env()
        self._log_file: Optional[str] = None
        
        if self.config.enable_logging:
            self._setup_log_file()
            if InteractionLogger._shared_log_file is None:
                InteractionLogger._shared_log_file = self._log_file
                self._load_existing_interactions()
            else:
                self._log_file = InteractionLogger._shared_log_file
    
    def _setup_log_file(self) -> None:
        """Setup log file path."""
        if self.config.log_path:
            log_dir = self.config.log_path
        else:
            log_dir = os.path.join(os.getcwd(), "testzeus_tools_logs")
        
        os.makedirs(log_dir, exist_ok=True)
        self._log_file = os.path.join(log_dir, "interactions.ndjson")
    
    def _load_existing_interactions(self) -> None:
        """Load existing interactions from log file."""
        if self._log_file and os.path.exists(self._log_file):
            try:
                with open(self._log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line.strip())
                            interaction = InteractionLog(**data)
                            InteractionLogger._shared_interactions.append(interaction)
            except Exception:
                pass  # Fail silently for loading errors
    
    async def log_interaction(
        self,
        tool_name: str,
        selector: str,
        action: str,
        success: bool,
        mode: str,
        error_message: Optional[str] = None,
        element_info: Optional[Dict[str, Any]] = None,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log an interaction."""
        if not self.config.enable_logging:
            return
        
        log_entry = InteractionLog(
            timestamp=time.time(),
            tool_name=tool_name,
            selector=selector,
            action=action,
            success=success,
            mode=mode,
            error_message=error_message,
            element_info=element_info,
            additional_data=additional_data
        )
        
        InteractionLogger._shared_interactions.append(log_entry)
        
        if self._log_file:
            try:
                with open(self._log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(log_entry), ensure_ascii=False) + "\n")
            except Exception:
                pass  # Fail silently for logging errors
    
    def get_interactions(self) -> List[InteractionLog]:
        """Get all logged interactions."""
        return InteractionLogger._shared_interactions.copy()
    
    def get_successful_interactions(self) -> List[InteractionLog]:
        """Get only successful interactions."""
        return [log for log in InteractionLogger._shared_interactions if log.success]
    
    def clear_interactions(self) -> None:
        """Clear all logged interactions."""
        InteractionLogger._shared_interactions.clear()
        if self._log_file and os.path.exists(self._log_file):
            try:
                os.remove(self._log_file)
            except Exception:
                pass
