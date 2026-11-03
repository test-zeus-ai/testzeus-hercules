"""Offline environment for observability tests.

Scrubs every execution-tracing switch so the tests exercise the no-tracing
default, disables telemetry, and hard-blocks outbound sockets so a tracing
integration can never phone home from these tests. Blocking happens at the
socket level: any connect attempt raises AssertionError.

Note: environment changes happen only inside fixtures (per-test, restored
afterwards) — mutating the process environment at conftest import time would
leak into unrelated test modules collected later in the same session.
"""

import socket

import pytest

_TRACING_ENV_VARS = (
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGCHAIN_PROJECT",
    "LANGSMITH_ENDPOINT",
    "LANGCHAIN_ENDPOINT",
)


@pytest.fixture(autouse=True)
def tracing_env_off(monkeypatch):
    """Force tracing off and remove every tracing credential/endpoint."""
    for var in _TRACING_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("ENABLE_TELEMETRY", "0")


@pytest.fixture(autouse=True)
def telemetry_events_disabled(monkeypatch):
    """Drop telemetry events so nothing is queued for background delivery."""
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - telemetry optional
        return
    monkeypatch.setattr(sentry_sdk.Client, "capture_event", lambda self, event, hint=None: None, raising=False)


@pytest.fixture(autouse=True)
def network_egress_blocked(monkeypatch):
    """Any socket connect inside these tests is a test bug, not a feature."""

    def _deny_connect(self, address):
        raise AssertionError(f"network egress is disabled in observability tests (connect to {address!r})")

    monkeypatch.setattr(socket.socket, "connect", _deny_connect)
