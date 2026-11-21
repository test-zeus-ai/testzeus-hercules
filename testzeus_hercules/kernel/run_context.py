"""Per-run state passed explicitly to every graph node and tool."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from testzeus_hercules.kernel.paths import HerculesPaths, validate_run_id
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import TelemetryClient


class RunCancelledError(RuntimeError):
    """Raised by ``CancelToken.raise_if_cancelled`` once a run has been cancelled."""


class CancelToken:
    """Thread-safe cancellation flag; the first reason given is kept."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason: str | None = None

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def cancel(self, reason: str | None = None) -> None:
        with self._lock:
            if not self._event.is_set():
                self._reason = reason
                self._event.set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise RunCancelledError(self._reason or "run cancelled")


class EventSink(Protocol):
    """Receives run events (step progress, decisions, results) for the UI and logs."""

    def emit(self, kind: str, /, **data: Any) -> None: ...


@dataclass(frozen=True)
class RecordedEvent:
    kind: str
    data: Mapping[str, Any]


class NullEventSink:
    """Discards every event."""

    def emit(self, kind: str, /, **data: Any) -> None:
        return None


class ListEventSink:
    """Keeps events in memory, in order; for tests."""

    def __init__(self) -> None:
        self.events: list[RecordedEvent] = []

    def emit(self, kind: str, /, **data: Any) -> None:
        self.events.append(RecordedEvent(kind=kind, data=dict(data)))


def new_run_id(now: datetime | None = None) -> str:
    """Sortable, unique run id such as ``r_20261025_141502_9f3a1c`` (UTC)."""
    moment = now or datetime.now(timezone.utc)
    return f"r_{moment:%Y%m%d_%H%M%S}_{secrets.token_hex(3)}"


@dataclass(frozen=True)
class RunContext:
    """Everything one run needs; there is no global configuration to fall back on."""

    run_id: str
    settings: Settings
    paths: HerculesPaths
    telemetry: TelemetryClient
    cancel: CancelToken = field(default_factory=CancelToken)
    events: EventSink = field(default_factory=NullEventSink)

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)

    @classmethod
    def create(
        cls,
        settings: Settings,
        *,
        run_id: str | None = None,
        events: EventSink | None = None,
        telemetry: TelemetryClient | None = None,
    ) -> RunContext:
        """Build a context for a new run. Does not touch the filesystem."""
        paths = HerculesPaths.from_settings(settings)
        return cls(
            run_id=new_run_id() if run_id is None else run_id,
            settings=settings,
            paths=paths,
            telemetry=TelemetryClient(settings, paths) if telemetry is None else telemetry,
            events=NullEventSink() if events is None else events,
        )
