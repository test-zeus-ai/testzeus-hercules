"""Shared fixtures for the v2 tests."""

import pytest
from testzeus_hercules.kernel import telemetry


@pytest.fixture(autouse=True)
def _no_real_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telemetry is on by default; tests must never reach the real PostHog endpoint.

    Any client that would build its default transport gets a recording one instead.
    Tests that exercise ``PostHogTransport`` itself import the class directly and inject
    a capturing HTTP transport.
    """
    monkeypatch.setattr(telemetry, "PostHogTransport", lambda *args, **kwargs: telemetry.RecordingTransport())
