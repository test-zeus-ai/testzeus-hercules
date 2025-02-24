import atexit
import json
import os
import signal
import time
from dataclasses import dataclass
from enum import Enum
from types import FrameType
from typing import Any, Dict, List, Optional

import sentry_sdk
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.logger import logger

ENABLE_TELEMETRY = os.getenv("ENABLE_TELEMETRY", "True").lower() in ["true", "1"]


class EventType(Enum):
    """
    Enum for different types of telemetry events.
    """

    ASSERT = "assert"
    COMMAND = "command"
    NAVIGATION = "navigation"
    SCREENSHOT = "screenshot"
    VIDEO = "video"
    NETWORK = "network"
    CONSOLE = "console"
    ERROR = "error"


@dataclass
class EventData:
    """
    Data class for telemetry event data.
    """

    detail: str
    timestamp: float = 0.0
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        if self.metadata is None:
            self.metadata = {}


class TelemetryManager:
    """
    Manager class for handling telemetry events.
    """

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self.initialize_sentry()

    def initialize_sentry(self) -> None:
        """
        Initialize Sentry SDK for error tracking.
        """
        if not ENABLE_TELEMETRY:
            return

        sentry_dsn = get_global_conf().get_sentry_dsn()
        if not sentry_dsn:
            logger.warning("Sentry DSN not configured. Telemetry will be limited.")
            return

        try:
            sentry_sdk.init(
                dsn=sentry_dsn,
                traces_sample_rate=1.0,
                profiles_sample_rate=1.0,
            )
            logger.info("Sentry initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Sentry: {e}")

    def add_event(self, event_type: EventType, event_data: EventData) -> None:
        """
        Add a telemetry event.

        Args:
            event_type: Type of the event.
            event_data: Data associated with the event.
        """
        if not ENABLE_TELEMETRY:
            return

        event = {
            "type": event_type.value,
            "timestamp": event_data.timestamp,
            "detail": event_data.detail,
            "metadata": event_data.metadata,
        }
        self.events.append(event)

    def send_message_to_sentry(self) -> None:
        """
        Send accumulated telemetry events to Sentry.
        """
        if not ENABLE_TELEMETRY or not self.events:
            return

        try:
            with sentry_sdk.push_scope() as scope:
                scope.set_extra("telemetry_events", self.events)
                sentry_sdk.capture_message("Telemetry Events Batch")
            logger.info(f"Sent {len(self.events)} telemetry events to Sentry")
            self.events = []
        except Exception as e:
            logger.error(f"Failed to send telemetry events to Sentry: {e}")


# Global telemetry manager instance
_telemetry_manager = TelemetryManager()


def add_event(event_type: EventType, event_data: EventData) -> None:
    """
    Add a telemetry event to the global manager.

    Args:
        event_type: Type of the event.
        event_data: Data associated with the event.
    """
    _telemetry_manager.add_event(event_type, event_data)


def register_shutdown() -> None:
    """
    Register a shutdown handler to send telemetry events to Sentry on exit.
    """
    if not ENABLE_TELEMETRY:
        return

    def signal_handler(signum: int, frame: Optional[FrameType]) -> None:
        _telemetry_manager.send_message_to_sentry()

    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Register with atexit
    atexit.register(_telemetry_manager.send_message_to_sentry)
