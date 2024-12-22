import asyncio
import atexit
import json
import os
import signal
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

import sentry_sdk
from pydantic import BaseModel
from sentry_sdk.scrubber import DEFAULT_DENYLIST, DEFAULT_PII_DENYLIST, EventScrubber
from sentry_sdk.types import Event, Hint

DSN = "https://d14d2ee82f26a3585b2a892fab7fffaa@o4508256143540224.ingest.us.sentry.io/4508256153042944"

# Telemetry flag, default is enabled (1) unless set to "0" in the environment variable
ENABLE_TELEMETRY = os.getenv("ENABLE_TELEMETRY", "1") == "1"

# custom denylist
denylist = DEFAULT_DENYLIST + ["sys.argv", "argv", "server_name"]
pii_denylist = DEFAULT_PII_DENYLIST + ["sys.argv", "argv", "server_name"]


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


def my_before_send(event: Event, hint: Hint) -> Event | None:
    # Filter out all ZeroDivisionError events.
    # Note that the exception type is available in the hint,
    # but we should handle the case where the exception info
    # is missing.
    if hint.get("exc_info", [None])[0] == ZeroDivisionError:
        return None

    # We can set extra data on the event's "extra" field.
    event["extra"]["session_summary"] = build_final_message()
    if "contexts" in event:
        contexts = event["contexts"]
        # Check if sys.argv exists and redact secrets
        if "argv" in contexts:
            contexts.pop("argv")

    # We have modified the event as desired, so return the event.
    # The SDK will then send the returned event to Sentry.
    return event


# Initialize Sentry only if telemetry is enabled
if ENABLE_TELEMETRY:

    sentry_sdk.init(
        dsn=DSN,
        before_send=my_before_send,
        max_breadcrumbs=0,
        send_default_pii=False,
        send_client_reports=False,
        server_name=None,
        event_scrubber=EventScrubber(denylist=denylist, pii_denylist=pii_denylist, recursive=True),
    )
    sentry_sdk.set_extra("sys.argv", None)
    sentry_sdk.set_user(None)


def get_installation_id(file_path: str = "installation_id.txt", is_manual_run: bool = True) -> Dict[str, Any]:
    """Generate or load installation data.

    If the file exists and contains a dict, return it.
    If the file exists and contains only the installation ID, return a dict with default values.
    If the file does not exist:
        - If triggered manually by the user (is_manual_run is True), prompt for user_email.
        - Generate a new installation ID and save all data as a dict.
        - If not triggered manually, use default values for user_email.
    """
    if os.path.exists(file_path):
        data = {
            "user_email": "old_email@example.com",
        }
        rewrite_data = False
        with open(file_path, "r") as file:
            content = file.read().strip()
            installation_id = content
            data["installation_id"] = installation_id
            try:
                data = json.loads(content)
                if isinstance(data, dict) and "installation_id" in data:
                    return data
            except json.JSONDecodeError:
                rewrite_data = True
        if rewrite_data:
            with open(file_path, "w") as file:
                json.dump(data, file)
    else:
        installation_id = str(uuid.uuid4())
        user_email = "new_email@example.com"
        if is_manual_run:
            print("We need your email to inform you about any urgent security patches or issues detected in testzeus-hercules.")
            n_user_email = input("Please provide your email (or press Enter to skip with empty email): ")
            user_email = n_user_email if n_user_email else user_email

        data = {
            "user_email": user_email,
            "installation_id": installation_id,
        }
        with open(file_path, "w") as file:
            json.dump(data, file)
    return data


# Initialize the installation_id
installation_data = get_installation_id(is_manual_run=os.environ.get("AUTO_MODE", "0") == "0")

# Global event collector with event_type buckets
event_collector = {
    "installation_id": installation_data["installation_id"],
    "user_email": installation_data["user_email"],
    "buckets": {},
    "start_time": datetime.now().isoformat(),
}


def add_event(event_type: EventType, event_data: EventData) -> None:
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


async def send_message_to_sentry() -> None:
    """
    Sends the final message to Sentry asynchronously, only if telemetry is enabled.
    """
    if not ENABLE_TELEMETRY:
        return  # Skip sending if telemetry is disabled
    try:
        message = build_final_message()
        with sentry_sdk.push_scope() as scope:
            scope.set_extra("session_summary", message)
            sentry_sdk.capture_message("Program execution summary")
    except Exception as e:
        print(f"Error sending message to Sentry: {e}")


def build_final_message() -> Dict[str, Any]:
    """
    Builds the final message from collected events, organized by event_type buckets.
    """
    message = {
        "installation_id": event_collector["installation_id"],
        "user_email": event_collector["user_email"],
        "session_start": event_collector["start_time"],
        "buckets": {event_type_s: events for event_type_s, events in event_collector["buckets"].items()},
    }
    return message


def register_shutdown() -> None:
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

    # loop = asyncio.get_event_loop()
    signal.signal(signal.SIGTERM, on_shutdown)
    signal.signal(signal.SIGINT, on_shutdown)
    # loop.add_signal_handler(signal.SIGTERM, on_shutdown)
    # loop.add_signal_handler(signal.SIGINT, on_shutdown)

    # Register with atexit to ensure it runs when the program exits
    atexit.register(lambda: asyncio.run(shutdown_wrapper()))


# Call the register_shutdown function to ensure our cleanup is registered
register_shutdown()
