import asyncio
import atexit
import os
import signal
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

import sentry_sdk
from pydantic import BaseModel

DSN = "https://d14d2ee82f26a3585b2a892fab7fffaa@o4508256143540224.ingest.us.sentry.io/4508256153042944"

# Telemetry flag, default is enabled (1) unless set to "0" in the environment variable
ENABLE_TELEMETRY = os.getenv("ENABLE_TELEMETRY", "1") == "1"

# Initialize Sentry only if telemetry is enabled
if ENABLE_TELEMETRY:
    sentry_sdk.init(dsn=DSN, max_breadcrumbs=0, send_default_pii=True)


class EventType(Enum):
    INTERACTION = "interaction"
    STEP = "step"
    TOOL = "tool"
    ASSERT = "assert"
    RUN = "run"
    DETECTION = "detection"
    CONFIG = "config"
    # Add other event types as needed


class EventData(BaseModel):
    detail: Optional[str] = None
    additional_data: Optional[Dict[str, Any]] = None  # For any extra details specific to certain events


def get_installation_id(file_path="installation_id.txt"):
    """Generate or load a unique installation ID."""
    if os.path.exists(file_path):
        with open(file_path, "r") as file:
            return file.read().strip()
    else:
        installation_id = str(uuid.uuid4())
        with open(file_path, "w") as file:
            file.write(installation_id)
        return installation_id


# Initialize the installation_id
installation_id = get_installation_id()

# Global event collector with event_type buckets
event_collector = {
    "installation_id": installation_id,
    "buckets": {},
    "start_time": datetime.now().isoformat(),
}


def add_event(event_type: EventType, event_data: EventData):
    """
    Adds an event to the event collector in the appropriate event_type bucket,
    only if telemetry is enabled.
    """
    if not ENABLE_TELEMETRY:
        return  # Skip event logging if telemetry is disabled

    global event_collector
    event = {
        "timestamp": datetime.now().isoformat(),
        "data": event_data.model_dump(),
    }

    # Add event to the relevant bucket
    if event_type.value not in event_collector["buckets"]:
        event_collector["buckets"][event_type.value] = {"events": [], "event_count": 0}
    event_collector["buckets"][event_type.value]["events"].append(event)
    event_collector["buckets"][event_type.value]["event_count"] += 1


async def send_message_to_sentry():
    """
    Sends the final message to Sentry asynchronously, only if telemetry is enabled.
    """
    if not ENABLE_TELEMETRY:
        return  # Skip sending if telemetry is disabled
    try:
        message = build_final_message()
        with sentry_sdk.push_scope() as scope:
            scope.set_context("session_summary", message)
            sentry_sdk.capture_message("Program execution summary")
    except Exception as e:
        print(f"Error sending message to Sentry: {e}")


def build_final_message():
    """
    Builds the final message from collected events, organized by event_type buckets.
    """
    message = {
        "installation_id": event_collector["installation_id"],
        "session_start": event_collector["start_time"],
        "buckets": {event_type_s: events for event_type_s, events in event_collector["buckets"].items()},
    }
    return message


def register_shutdown():
    """
    Register a shutdown handler to run send_message_to_sentry on exit,
    only if telemetry is enabled.
    """
    if not ENABLE_TELEMETRY:
        return  # Do not register shutdown if telemetry is disabled

    async def shutdown_wrapper():
        await send_message_to_sentry()

    def on_shutdown():
        # Schedule shutdown_wrapper to be run asynchronously
        asyncio.create_task(shutdown_wrapper())

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, on_shutdown)
    loop.add_signal_handler(signal.SIGINT, on_shutdown)

    # Register with atexit to ensure it runs when the program exits
    atexit.register(lambda: asyncio.run(shutdown_wrapper()))


# Call the register_shutdown function to ensure our cleanup is registered
register_shutdown()
