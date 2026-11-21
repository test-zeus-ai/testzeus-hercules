"""Anonymous, opt-out usage telemetry.

Events come from a closed set and each has an allowlisted property schema, so
test content, prompts, page text, URLs, paths and secrets cannot be attached.
``TelemetryClient.emit`` only validates and enqueues; a background daemon
thread delivers events, so callers never block or see errors, even offline.
Events go to PostHog as anonymous events: no person profiles, no GeoIP lookup.
"""

from __future__ import annotations

import datetime
import functools
import logging
import os
import platform
import queue
import re
import sys
import tempfile
import threading
import time
import uuid
from importlib import metadata
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal, Mapping, Protocol, get_args

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, ValidationError

from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.settings import DeciderBackend, EgressPolicy, Provider, Settings, Tier

logger = logging.getLogger(__name__)

# PostHog project that receives Hercules telemetry. A project token can only write
# events into the project; it cannot read anything back, so it is safe to publish.
POSTHOG_HOST = "https://us.i.posthog.com"
POSTHOG_PROJECT_TOKEN = "phc_vFuLJsRCxmwN3krUwRnLXdzSvbxMkWznRXxTvsBn9xzU"

TELEMETRY_NOTICE = (
    "Hercules sends anonymous usage telemetry to PostHog to help improve it: the Hercules, Python and OS versions, "
    "the configured model tier, LLM provider, decision backend and egress policy, step and run counts, "
    "whether a run was started from the command line, the tester UI or a coding agent (and which agent, such as Claude Code), "
    "run outcomes, durations, error class names and a random installation id. It never sends test cases, "
    "step text, prompts, page content, screenshots, URLs, file paths, hostnames, usernames, emails or secrets, "
    "and it never delays Hercules when you are offline. It is always off under egress policy local. To opt out, "
    "pass --no-telemetry, set HERCULES_TELEMETRY=0 or DO_NOT_TRACK=1, run `hercules setup --telemetry off`, "
    "or add `telemetry: {enabled: false}` to ~/.hercules/config.yaml or your project's .hercules/config.yaml."
)

TelemetryEvent = Literal["app_started", "doctor_completed", "run_started", "run_finished", "step_failed", "error", "agent_setup", "explore_started", "explore_finished"]
TELEMETRY_EVENTS: tuple[str, ...] = get_args(TelemetryEvent)

RunStatus = Literal["passed", "failed", "error", "cancelled"]
ExploreStatus = Literal["finished", "failed", "stopped"]
# Where a run was started: `hercules run` (cli), the daemon's tester UI or HTTP API (daemon), or a coding agent over MCP.
RunOrigin = Literal["cli", "daemon", "agent"]
AgentClient = Literal["claude-code", "codex", "cursor", "copilot", "gemini", "other"]
RUN_ORIGINS: tuple[str, ...] = get_args(RunOrigin)
AGENT_CLIENTS: tuple[str, ...] = get_args(AgentClient)
# Set by the daemon for the workers it starts, so a worker's run events say where the run came from.
RUN_ORIGIN_ENV = "HERCULES_RUN_ORIGIN"
AGENT_CLIENT_ENV = "HERCULES_AGENT_CLIENT"

_AGENT_NAMES: tuple[tuple[str, AgentClient], ...] = (
    ("claude", "claude-code"),
    ("codex", "codex"),
    ("cursor", "cursor"),
    ("copilot", "copilot"),
    ("visual studio code", "copilot"),
    ("vscode", "copilot"),
    ("gemini", "gemini"),
)


def agent_client_from_name(name: str | None) -> AgentClient | None:
    """A coding agent's fixed telemetry name from the name its MCP client reports (never the raw name)."""
    if not name:
        return None
    lowered = name.lower()
    for marker, client in _AGENT_NAMES:
        if marker in lowered:
            return client
    return "other"


def run_origin_from_env(env: Mapping[str, str], default: RunOrigin = "cli") -> tuple[RunOrigin, AgentClient | None]:
    """The run origin and agent the daemon handed a worker in its environment (unknown values fall back)."""
    origin = env.get(RUN_ORIGIN_ENV, "")
    agent = env.get(AGENT_CLIENT_ENV, "")
    chosen: RunOrigin = origin if origin in RUN_ORIGINS else default  # type: ignore[assignment]
    client: AgentClient | None = agent if chosen == "agent" and agent in AGENT_CLIENTS else None  # type: ignore[assignment]
    return chosen, client


OSName = Literal["darwin", "linux", "windows", "other"]
ErrorClass = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")]

MAX_FLUSH_TIMEOUT_S = 2.0
_QUEUE_SIZE = 256
_WORKER_NAME = "hercules-telemetry"

_POSTHOG_CAPTURE_PATH = "/i/v0/e/"
_HTTP_TIMEOUT_S = 5.0
_LIB_NAME = "testzeus-hercules"

_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://|\bwww\.", re.IGNORECASE)
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_PATH = re.compile(r"[/\\][^/\\\s]")
_TOKEN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")


class TelemetryPayloadError(ValueError):
    """An event or property is not allowed in telemetry. Messages never contain values."""


class _Props(BaseModel):
    """Properties shared by every event; the client fills them from ``Settings``."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    llm_provider: Provider | None = None
    model_tier: Tier | None = None
    decider_backend: DeciderBackend | None = None
    egress_policy: EgressPolicy | None = None
    run_origin: RunOrigin | None = None
    agent_client: AgentClient | None = None


class AppStartedProps(_Props):
    pass


class DoctorCompletedProps(_Props):
    duration_ms: NonNegativeInt | None = None
    error_class: ErrorClass | None = None


class RunStartedProps(_Props):
    step_count: NonNegativeInt | None = None


class RunFinishedProps(_Props):
    run_status: RunStatus
    step_count: NonNegativeInt | None = None
    duration_ms: NonNegativeInt | None = None


class StepFailedProps(_Props):
    error_class: ErrorClass | None = None
    duration_ms: NonNegativeInt | None = None


class ErrorProps(_Props):
    error_class: ErrorClass


class AgentSetupProps(_Props):
    """``hercules init`` (or ``hercules setup``) configured a coding agent."""

    agent_client: AgentClient


class ExploreStartedProps(_Props):
    """An exploration session started: its size and time budget only."""

    workers: NonNegativeInt
    reproducers: NonNegativeInt
    time_budget_s: NonNegativeInt


class ExploreFinishedProps(_Props):
    """An exploration session ended: counts and duration only (never what was found)."""

    explore_status: ExploreStatus
    finding_count: NonNegativeInt
    clue_count: NonNegativeInt
    duration_ms: NonNegativeInt | None = None


EVENT_SCHEMAS: Mapping[str, type[_Props]] = MappingProxyType(
    {
        "app_started": AppStartedProps,
        "doctor_completed": DoctorCompletedProps,
        "run_started": RunStartedProps,
        "run_finished": RunFinishedProps,
        "step_failed": StepFailedProps,
        "error": ErrorProps,
        "agent_setup": AgentSetupProps,
        "explore_started": ExploreStartedProps,
        "explore_finished": ExploreFinishedProps,
    }
)


class TelemetryPayload(BaseModel):
    """The complete, documented wire schema: nothing outside these fields is ever sent."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event: TelemetryEvent
    version: str = Field(pattern=r"^[0-9A-Za-z.+-]{1,64}$")
    os: OSName
    python: str = Field(pattern=r"^\d+\.\d+$")
    install_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    llm_provider: Provider | None = None
    model_tier: Tier | None = None
    decider_backend: DeciderBackend | None = None
    egress_policy: EgressPolicy | None = None
    run_origin: RunOrigin | None = None
    agent_client: AgentClient | None = None
    step_count: NonNegativeInt | None = None
    run_status: RunStatus | None = None
    duration_ms: NonNegativeInt | None = None
    error_class: ErrorClass | None = None
    workers: NonNegativeInt | None = None
    reproducers: NonNegativeInt | None = None
    time_budget_s: NonNegativeInt | None = None
    explore_status: ExploreStatus | None = None
    finding_count: NonNegativeInt | None = None
    clue_count: NonNegativeInt | None = None


ALLOWED_FIELDS = frozenset(TelemetryPayload.model_fields)


def find_sensitive_text(value: str) -> str | None:
    """Return what ``value`` looks like (url, email, path, token) or None if it looks harmless."""
    if _URL.search(value):
        return "url"
    if _EMAIL.search(value):
        return "email"
    if _PATH.search(value):
        return "path"
    for match in _TOKEN.finditer(value):
        run = match.group()
        if any(char.isdigit() for char in run) and any(char.isalpha() for char in run):
            return "token"
    return None


def build_payload(event: TelemetryEvent, props: Mapping[str, Any], *, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate an event against its schema and return the payload to send (without install id).

    ``context`` supplies defaults for the shared properties; ``props`` win.
    Raises ``TelemetryPayloadError`` for unknown events, fields outside the
    event's schema, and string values that look like URLs, paths, emails or tokens.
    """
    schema = EVENT_SCHEMAS.get(event)
    if schema is None:
        raise TelemetryPayloadError(f"unknown telemetry event {event!r}")
    defaults = {key: value for key, value in (context or {}).items() if key in _Props.model_fields}
    try:
        validated = schema.model_validate({**defaults, **props})
        payload = TelemetryPayload(
            event=event,
            version=_package_version(),
            os=_os_name(),
            python=f"{sys.version_info.major}.{sys.version_info.minor}",
            **validated.model_dump(exclude_none=True),
        )
    except ValidationError as exc:
        fields = sorted({".".join(str(part) for part in error["loc"]) or "<root>" for error in exc.errors()})
        raise TelemetryPayloadError(f"invalid properties for telemetry event {event!r}: {', '.join(fields)}") from None
    data = payload.model_dump(exclude_none=True)
    _reject_sensitive_values(data)
    return data


def _reject_sensitive_values(payload: Mapping[str, Any]) -> None:
    for key, value in payload.items():
        if key == "install_id" or not isinstance(value, str):
            continue
        kind = find_sensitive_text(value)
        if kind is not None:
            raise TelemetryPayloadError(f"telemetry field {key!r} looks like a {kind}")


@functools.lru_cache(maxsize=1)
def _package_version() -> str:
    try:
        return metadata.version("testzeus-hercules")
    except metadata.PackageNotFoundError:
        return "unknown"


def _os_name() -> OSName:
    system = platform.system().lower()
    if system in ("darwin", "linux", "windows"):
        return system  # type: ignore[return-value]
    return "other"


def load_or_create_install_id(path: Path) -> str:
    """Return the anonymous installation id stored at ``path``, creating it on first use.

    The id is a random UUID4, never derived from the machine or user. If the
    file cannot be written, a fresh id is used for this process only.
    """
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        existing = None
    if existing is not None and _is_uuid4(existing):
        return existing
    install_id = str(uuid.uuid4())
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if existing is None:
            return _create_exclusive(path, install_id)
        _replace_atomically(path, install_id)
    except OSError as exc:
        logger.debug("telemetry install id not persisted: %s", exc.__class__.__name__)
    return install_id


def _create_exclusive(path: Path, install_id: str) -> str:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process created it first; prefer its id if valid.
        existing = path.read_text(encoding="utf-8").strip()
        if _is_uuid4(existing):
            return existing
        _replace_atomically(path, install_id)
        return install_id
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(install_id + "\n")
    return install_id


def _replace_atomically(path: Path, install_id: str) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".installation_id.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(install_id + "\n")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _is_uuid4(text: str) -> bool:
    try:
        parsed = uuid.UUID(text)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == text


class TelemetryTransport(Protocol):
    """Delivers payloads. Called from the telemetry worker thread only.

    ``flush`` must return within ``timeout`` seconds. A transport may also
    provide ``close(timeout)``, called once when the client closes.
    """

    def send(self, payload: Mapping[str, Any]) -> None: ...

    def flush(self, timeout: float) -> None: ...


class RecordingTransport:
    """Keeps payloads in memory; for tests and local debugging."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.payloads: list[dict[str, Any]] = []
        self.flush_calls = 0

    def send(self, payload: Mapping[str, Any]) -> None:
        with self._lock:
            self.payloads.append(dict(payload))

    def flush(self, timeout: float) -> None:
        with self._lock:
            self.flush_calls += 1


class PostHogTransport:
    """Sends each payload to PostHog's capture API as one anonymous event.

    The HTTP client is created on first send, never at import. Every event is
    rebuilt from the re-validated payload by :func:`posthog_event`, so nothing
    outside the documented schema can be sent. Does nothing while ``token`` is empty.
    """

    def __init__(self, token: str = POSTHOG_PROJECT_TOKEN, host: str = POSTHOG_HOST, *, http_transport: Any = None) -> None:
        self._token = token
        self._url = host.rstrip("/") + _POSTHOG_CAPTURE_PATH
        self._http_transport = http_transport
        self._lock = threading.Lock()
        self._client: Any = None

    def send(self, payload: Mapping[str, Any]) -> None:
        if not self._token:
            return
        event = posthog_event(payload, self._token)
        if event is None:
            return
        response = self._get_client().post(self._url, json=event)
        response.raise_for_status()

    def flush(self, timeout: float) -> None:
        """Nothing to do: ``send`` delivers each event before returning."""

    def close(self, timeout: float) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()

    def _get_client(self) -> Any:
        with self._lock:
            if self._client is None:
                import httpx

                self._client = httpx.Client(
                    timeout=_HTTP_TIMEOUT_S,
                    transport=self._http_transport,
                    headers={"User-Agent": f"{_LIB_NAME}/{_package_version()}"},
                    trust_env=True,
                    follow_redirects=False,
                )
            return self._client


def posthog_event(payload: Mapping[str, Any], token: str) -> dict[str, Any] | None:
    """The PostHog capture request body for ``payload``, or None when the payload is not a valid, clean telemetry payload.

    The random installation id is the distinct id. ``$process_person_profile``
    is false (no person profile is created) and ``$geoip_disable`` is true (no
    location is derived from the sender's IP address).
    """
    try:
        clean = TelemetryPayload.model_validate(dict(payload)).model_dump(exclude_none=True)
        _reject_sensitive_values(clean)
    except (ValidationError, TelemetryPayloadError):
        return None
    install_id = clean.pop("install_id", None)
    if install_id is None:
        return None
    event = clean.pop("event")
    properties: dict[str, Any] = {
        **clean,
        "$process_person_profile": False,
        "$geoip_disable": True,
        "$lib": _LIB_NAME,
        "$lib_version": clean["version"],
    }
    return {
        "api_key": token,
        "event": event,
        "distinct_id": install_id,
        "properties": properties,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def telemetry_enabled(settings: Settings) -> bool:
    """Whether telemetry may be sent: ``telemetry.enabled``, and never under egress policy ``local`` (nothing leaves the machine)."""
    return settings.telemetry.enabled and settings.egress.policy != "local"


class TelemetryClient:
    """Per-process telemetry emitter; a no-op when telemetry is off (see :func:`telemetry_enabled`).

    ``emit`` never raises and never blocks on the network. Call ``close`` (or
    use the client as a context manager) on shutdown to deliver queued events
    within at most ``MAX_FLUSH_TIMEOUT_S``.
    """

    def __init__(
        self,
        settings: Settings,
        paths: HerculesPaths,
        *,
        transport: TelemetryTransport | None = None,
        run_origin: RunOrigin | None = None,
        agent_client: AgentClient | None = None,
    ) -> None:
        self._enabled = telemetry_enabled(settings)
        self._context: Mapping[str, Any] = MappingProxyType(
            {
                "llm_provider": settings.llm.provider,
                "model_tier": settings.llm.tier,
                "decider_backend": settings.decider.backend,
                "egress_policy": settings.egress.policy,
                **({"run_origin": run_origin} if run_origin else {}),
                **({"agent_client": agent_client} if agent_client else {}),
            }
        )
        self._install_id_file = paths.installation_id_file
        self._install_id: str | None = None
        self._transport = transport
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=_QUEUE_SIZE)
        self._idle = threading.Condition()
        self._pending = 0
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._closed = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def emit(self, event: TelemetryEvent, /, **props: Any) -> None:
        """Queue one event. Invalid events are dropped; nothing is ever raised."""
        if not self._enabled or self._closed:
            return
        try:
            payload = build_payload(event, props, context=self._context)
            self._ensure_worker()
            with self._idle:
                self._pending += 1
            try:
                self._queue.put_nowait(payload)
            except queue.Full:
                self._mark_done()
                logger.debug("telemetry queue full; event dropped")
        except Exception as exc:  # telemetry must never break the caller
            logger.debug("telemetry event dropped: %s", exc)

    def flush(self, timeout: float = MAX_FLUSH_TIMEOUT_S) -> bool:
        """Wait (at most ``MAX_FLUSH_TIMEOUT_S``) for queued events; True if all were handed over."""
        if not self._enabled:
            return True
        deadline = time.monotonic() + max(0.0, min(timeout, MAX_FLUSH_TIMEOUT_S))
        with self._idle:
            drained = self._idle.wait_for(lambda: self._pending == 0, timeout=max(0.0, deadline - time.monotonic()))
        remaining = deadline - time.monotonic()
        if not drained or self._transport is None or remaining <= 0:
            return drained
        try:
            self._transport.flush(remaining)
        except Exception as exc:
            logger.debug("telemetry flush failed: %s", exc.__class__.__name__)
            return False
        return True

    def close(self, timeout: float = MAX_FLUSH_TIMEOUT_S) -> None:
        """Flush, stop the worker and close the transport; idempotent."""
        if self._closed:
            return
        self._closed = True
        if not self._enabled or self._worker is None:
            return
        deadline = time.monotonic() + max(0.0, min(timeout, MAX_FLUSH_TIMEOUT_S))
        self.flush(timeout)
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        close = getattr(self._transport, "close", None)
        remaining = deadline - time.monotonic()
        if callable(close) and remaining > 0:
            try:
                close(remaining)
            except Exception as exc:
                logger.debug("telemetry transport close failed: %s", exc.__class__.__name__)

    def __enter__(self) -> TelemetryClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name=_WORKER_NAME, daemon=True)
                self._worker.start()

    def _run(self) -> None:
        while True:
            payload = self._queue.get()
            if payload is None:
                return
            try:
                self._deliver(payload)
            except Exception as exc:
                logger.debug("telemetry send failed: %s", exc.__class__.__name__)
            finally:
                self._mark_done()

    def _deliver(self, payload: dict[str, Any]) -> None:
        if self._install_id is None:
            self._install_id = load_or_create_install_id(self._install_id_file)
        if self._transport is None:
            self._transport = PostHogTransport()
        self._transport.send({**payload, "install_id": self._install_id})

    def _mark_done(self) -> None:
        with self._idle:
            self._pending -= 1
            self._idle.notify_all()
