"""Hercules v2 kernel: settings, on-disk layout, per-run context and telemetry.

Nothing here keeps global state; build ``Settings`` once and pass a
``RunContext`` explicitly.
"""

from testzeus_hercules.kernel.paths import HerculesPaths, validate_run_id
from testzeus_hercules.kernel.run_context import CancelToken, EventSink, ListEventSink, NullEventSink, RecordedEvent, RunCancelledError, RunContext, new_run_id
from testzeus_hercules.kernel.settings import DeciderSettings, EgressSettings, LLMSettings, Settings, SettingsError, TelemetrySettings, UISettings
from testzeus_hercules.kernel.telemetry import TELEMETRY_NOTICE, RecordingTransport, TelemetryClient, TelemetryPayloadError

__all__ = [
    "CancelToken",
    "DeciderSettings",
    "EgressSettings",
    "EventSink",
    "HerculesPaths",
    "LLMSettings",
    "ListEventSink",
    "NullEventSink",
    "RecordedEvent",
    "RecordingTransport",
    "RunCancelledError",
    "RunContext",
    "Settings",
    "SettingsError",
    "TELEMETRY_NOTICE",
    "TelemetryClient",
    "TelemetryPayloadError",
    "TelemetrySettings",
    "UISettings",
    "new_run_id",
    "validate_run_id",
]
