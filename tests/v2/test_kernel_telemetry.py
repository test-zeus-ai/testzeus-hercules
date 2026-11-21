"""Tests for the v2 anonymous telemetry client."""

import json
import os
import stat
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

import pytest

from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import (
    ALLOWED_FIELDS,
    TELEMETRY_EVENTS,
    TELEMETRY_NOTICE,
    PostHogTransport,
    RecordingTransport,
    TelemetryClient,
    TelemetryPayloadError,
    build_payload,
    find_sensitive_text,
    load_or_create_install_id,
    posthog_event,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _settings(tmp_path: Path, *, enabled: bool = True) -> Settings:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home")}, overrides={"telemetry": {"enabled": enabled}})


def _client(tmp_path: Path, transport: Any, *, enabled: bool = True) -> TelemetryClient:
    settings = _settings(tmp_path, enabled=enabled)
    return TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=transport)


def _telemetry_threads() -> set[threading.Thread]:
    return {thread for thread in threading.enumerate() if thread.name == "hercules-telemetry"}


class FailingTransport:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, payload: Mapping[str, Any]) -> None:
        self.calls += 1
        raise ConnectionError("network unreachable")

    def flush(self, timeout: float) -> None:
        raise ConnectionError("network unreachable")


class HangingTransport:
    def __init__(self) -> None:
        self.release = threading.Event()

    def send(self, payload: Mapping[str, Any]) -> None:
        self.release.wait(10)

    def flush(self, timeout: float) -> None:
        self.release.wait(10)


def test_notice_explains_collection_and_opt_out() -> None:
    assert "HERCULES_TELEMETRY=0" in TELEMETRY_NOTICE
    assert "enabled: false" in TELEMETRY_NOTICE
    assert "never" in TELEMETRY_NOTICE.lower()


def test_event_names_are_a_closed_set() -> None:
    assert set(TELEMETRY_EVENTS) == {"app_started", "doctor_completed", "run_started", "run_finished", "step_failed", "error", "agent_setup", "explore_started", "explore_finished"}


def test_allowed_fields_are_the_documented_allowlist() -> None:
    assert ALLOWED_FIELDS == {
        "event",
        "version",
        "os",
        "python",
        "model_tier",
        "llm_provider",
        "decider_backend",
        "egress_policy",
        "run_origin",
        "agent_client",
        "step_count",
        "run_status",
        "duration_ms",
        "error_class",
        "install_id",
        "workers",
        "reproducers",
        "time_budget_s",
        "explore_status",
        "finding_count",
        "clue_count",
    }


def test_explore_events_carry_counts_only() -> None:

    started = build_payload("explore_started", {"workers": 3, "reproducers": 1, "time_budget_s": 1800})
    assert {key: started[key] for key in ("workers", "reproducers", "time_budget_s")} == {"workers": 3, "reproducers": 1, "time_budget_s": 1800}
    finished = build_payload("explore_finished", {"explore_status": "finished", "finding_count": 2, "clue_count": 9, "duration_ms": 1000})
    assert finished["finding_count"] == 2 and finished["explore_status"] == "finished"
    with pytest.raises(TelemetryPayloadError):
        build_payload("explore_finished", {"explore_status": "finished", "finding_count": 1, "clue_count": 1, "title": "Cart total wrong"})


def test_enabled_client_sends_only_allowlisted_fields(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client = _client(tmp_path, transport)

    client.emit("run_finished", run_status="passed", step_count=7, duration_ms=1234)
    assert client.flush(timeout=2.0)

    (payload,) = transport.payloads
    assert set(payload) <= ALLOWED_FIELDS
    assert payload["event"] == "run_finished"
    assert payload["run_status"] == "passed"
    assert payload["step_count"] == 7
    assert payload["duration_ms"] == 1234
    assert payload["llm_provider"] == "ollama"
    assert payload.get("decider_backend") is None
    assert payload["egress_policy"] == "hybrid"
    assert payload["model_tier"] == "auto"
    assert payload["python"] == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert payload["os"] in {"darwin", "linux", "windows", "other"}
    uuid.UUID(payload["install_id"])
    client.close()


def test_disabled_client_never_touches_transport_or_disk(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client = _client(tmp_path, transport, enabled=False)
    workers_before = _telemetry_threads()

    client.emit("app_started")
    client.emit("run_finished", run_status="failed")
    client.flush()
    client.close()

    assert transport.payloads == []
    assert transport.flush_calls == 0
    assert not (tmp_path / "home").exists()
    assert _telemetry_threads() <= workers_before


@pytest.mark.parametrize(
    "field",
    ["url", "prompt", "step_text", "page_text", "file_path", "hostname", "username", "email", "api_key", "screenshot", "case_name", "test_case"],
)
def test_forbidden_fields_are_rejected(field: str) -> None:
    with pytest.raises(TelemetryPayloadError):
        build_payload("run_started", {field: "x"})


def test_unknown_event_is_rejected() -> None:
    with pytest.raises(TelemetryPayloadError):
        build_payload("page_visited", {})  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["run_status", "step_count"])
def test_fields_not_in_the_event_schema_are_rejected(field: str) -> None:
    with pytest.raises(TelemetryPayloadError):
        build_payload("error", {"error_class": "TimeoutError", field: 1})


def test_emit_drops_invalid_events_silently(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client = _client(tmp_path, transport)

    client.emit("run_started", url="https://shop.example/checkout")
    client.emit("error", error_class="https://shop.example/checkout")
    client.emit("nope")  # type: ignore[arg-type]
    client.flush(timeout=1.0)
    client.close()

    assert transport.payloads == []


@pytest.mark.parametrize(
    "value",
    [
        "https://shop.example/checkout",
        "http://127.0.0.1:8080",
        "www.example.com",
        "/Users/alice/project",
        "C:\\Users\\bob\\cases",
        "cases/login.feature",
        "~/secret",
        "alice@example.com",
        "sk-or-v1-0123456789abcdef0123456789abcdef",
        "eyJhbGciOiJIUzI1NiJ9eyJzdWIiOiIxMjM0NTY3ODkwIn0",
        "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5",
    ],
)
def test_sensitive_strings_are_detected(value: str) -> None:
    assert find_sensitive_text(value) is not None


@pytest.mark.parametrize("value", ["TimeoutError", "PlaywrightNavigationTimeoutErrorWrapper", "3.12", "darwin", "1.0.1", "passed", "hybrid-text"])
def test_benign_strings_pass(value: str) -> None:
    assert find_sensitive_text(value) is None


def test_build_payload_fills_context_and_drops_unset_fields() -> None:
    payload = build_payload("error", {"error_class": "TimeoutError"}, context={"llm_provider": "lmstudio", "egress_policy": "hybrid"})

    assert payload["event"] == "error"
    assert payload["error_class"] == "TimeoutError"
    assert payload["llm_provider"] == "lmstudio"
    assert payload["egress_policy"] == "hybrid"
    assert "step_count" not in payload
    assert "install_id" not in payload


def test_caller_props_override_context() -> None:
    payload = build_payload("doctor_completed", {"model_tier": "standard"}, context={"model_tier": "auto"})

    assert payload["model_tier"] == "standard"


def test_failing_transport_never_raises_or_blocks(tmp_path: Path) -> None:
    transport = FailingTransport()
    client = _client(tmp_path, transport)

    started = time.monotonic()
    for _ in range(50):
        client.emit("step_failed", error_class="TimeoutError", duration_ms=10)
    emit_elapsed = time.monotonic() - started
    client.flush(timeout=1.0)
    client.close(timeout=1.0)

    assert emit_elapsed < 0.5
    assert transport.calls == 50


def test_hanging_transport_cannot_hang_the_caller(tmp_path: Path) -> None:
    transport = HangingTransport()
    client = _client(tmp_path, transport)

    started = time.monotonic()
    client.emit("app_started")
    client.emit("run_started", step_count=3)
    emitted = time.monotonic() - started
    flushed = client.flush(timeout=0.2)
    client.close(timeout=0.2)
    total = time.monotonic() - started
    transport.release.set()

    assert emitted < 0.1
    assert flushed is False
    assert total < 1.5


def test_flush_timeout_is_capped(tmp_path: Path) -> None:
    transport = HangingTransport()
    client = _client(tmp_path, transport)
    client.emit("app_started")

    started = time.monotonic()
    client.flush(timeout=30.0)
    elapsed = time.monotonic() - started
    transport.release.set()

    assert elapsed < 3.0


def test_install_id_is_stable_across_clients(tmp_path: Path) -> None:
    first, second = RecordingTransport(), RecordingTransport()
    client_a = _client(tmp_path, first)
    client_b = _client(tmp_path, second)

    client_a.emit("app_started")
    client_a.flush()
    client_b.emit("app_started")
    client_b.flush()
    client_a.close()
    client_b.close()

    id_file = HerculesPaths.from_settings(_settings(tmp_path)).installation_id_file
    assert id_file.is_relative_to(tmp_path / "home")
    stored = id_file.read_text(encoding="utf-8").strip()
    assert uuid.UUID(stored).version == 4
    assert first.payloads[0]["install_id"] == second.payloads[0]["install_id"] == stored


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_install_id_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "home" / "installation_id"

    load_or_create_install_id(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_corrupt_install_id_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "installation_id"
    path.write_text("alice@example.com\n", encoding="utf-8")

    install_id = load_or_create_install_id(path)

    assert uuid.UUID(install_id).version == 4
    assert path.read_text(encoding="utf-8").strip() == install_id


def test_unwritable_home_still_yields_an_anonymous_id(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8")

    install_id = load_or_create_install_id(blocker / "installation_id")

    assert uuid.UUID(install_id).version == 4


def test_construction_and_emit_while_off_open_no_http_client(tmp_path: Path) -> None:
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from testzeus_hercules.kernel.telemetry import TelemetryClient\n"
        "from testzeus_hercules.kernel.paths import HerculesPaths\n"
        "from testzeus_hercules.kernel.settings import Settings\n"
        "s = Settings.load(Path(sys.argv[1]), env={'HERCULES_HOME': sys.argv[2], 'HERCULES_TELEMETRY': '0'})\n"
        "c = TelemetryClient(s, HerculesPaths.from_settings(s))\n"
        "c.emit('app_started')\n"
        "c.close()\n"
        "assert c._transport is None, 'transport created'\n"
        "assert not any(t.name == 'hercules-telemetry' for t in __import__('threading').enumerate()), 'worker started'\n"
    )
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}

    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(tmp_path / "home")],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def _install_payload(event: str = "error", **props: Any) -> dict[str, Any]:
    return {**build_payload(event, props or {"error_class": "TimeoutError"}), "install_id": str(uuid.uuid4())}


def test_posthog_event_is_anonymous_and_holds_only_the_payload() -> None:
    payload = _install_payload("run_finished", run_status="passed", step_count=2, duration_ms=50)

    event = posthog_event(payload, "phc_test")

    assert event is not None
    assert set(event) == {"api_key", "event", "distinct_id", "properties", "timestamp"}
    assert event["api_key"] == "phc_test"
    assert event["event"] == "run_finished"
    assert event["distinct_id"] == payload["install_id"]
    props = event["properties"]
    assert props["$process_person_profile"] is False
    assert props["$geoip_disable"] is True
    assert props["$lib"] == "testzeus-hercules"
    plain = {key for key in props if not key.startswith("$")}
    assert plain <= ALLOWED_FIELDS - {"event", "install_id"}
    assert props["run_status"] == "passed" and props["step_count"] == 2


def test_posthog_event_drops_invalid_payloads() -> None:
    good = _install_payload()
    assert posthog_event({**good, "url": "https://shop.example"}, "phc_test") is None
    assert posthog_event({**good, "error_class": "/Users/alice"}, "phc_test") is None
    assert posthog_event({key: value for key, value in good.items() if key != "install_id"}, "phc_test") is None
    assert posthog_event({"event": "boom"}, "phc_test") is None


def _capturing_transport(status: int = 200) -> tuple[Any, list[Any]]:
    httpx = pytest.importorskip("httpx")
    requests: list[Any] = []

    def handler(request: Any) -> Any:
        requests.append(request)
        return httpx.Response(status, json={"status": "Ok"})

    return httpx.MockTransport(handler), requests


def test_posthog_transport_posts_one_capture_request_per_event(tmp_path: Path) -> None:
    http, requests = _capturing_transport()
    transport = PostHogTransport("phc_test", "https://ph.example/", http_transport=http)
    client = _client(tmp_path, transport)

    client.emit("run_finished", run_status="passed", step_count=2, duration_ms=50)
    client.flush()
    client.close()

    (request,) = requests
    assert request.method == "POST"
    assert str(request.url) == "https://ph.example/i/v0/e/"
    body = json.loads(request.content)
    assert body["event"] == "run_finished"
    assert body["properties"]["$process_person_profile"] is False
    assert {key for key in body["properties"] if not key.startswith("$")} <= ALLOWED_FIELDS
    assert request.headers["user-agent"].startswith("testzeus-hercules/")
    assert "cookie" not in request.headers


def test_posthog_transport_without_a_token_sends_nothing(tmp_path: Path) -> None:
    http, requests = _capturing_transport()
    client = _client(tmp_path, PostHogTransport("", http_transport=http))

    client.emit("app_started")
    client.flush()
    client.close()

    assert requests == []


def test_posthog_server_errors_never_reach_the_caller(tmp_path: Path) -> None:
    http, requests = _capturing_transport(status=503)
    client = _client(tmp_path, PostHogTransport("phc_test", http_transport=http))

    client.emit("app_started")
    assert client.flush() is True
    client.close()

    assert len(requests) == 1


def test_notice_names_posthog_and_every_opt_out() -> None:
    for text in ("PostHog", "--no-telemetry", "HERCULES_TELEMETRY=0", "DO_NOT_TRACK=1", "--telemetry off", "telemetry: {enabled: false}"):
        assert text in TELEMETRY_NOTICE
