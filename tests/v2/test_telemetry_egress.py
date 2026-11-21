"""Telemetry stays off under egress policy local: nothing, not even the installation id, leaves the machine."""

from pathlib import Path

import pytest

from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import RecordingTransport, TelemetryClient, telemetry_enabled


def _settings(tmp_path: Path, policy: str) -> Settings:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home")}, overrides={"telemetry": {"enabled": True}, "egress": {"policy": policy}})


def test_local_egress_sends_no_events_and_creates_no_install_id(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "local")
    transport = RecordingTransport()
    client = TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=transport)

    client.emit("app_started")
    client.emit("run_finished", run_status="passed")
    client.flush()
    client.close()

    assert client.enabled is False and telemetry_enabled(settings) is False
    assert transport.payloads == []
    assert not HerculesPaths.from_settings(settings).installation_id_file.exists()


@pytest.mark.parametrize("policy", ["hybrid", "hybrid-text"])
def test_hosted_egress_policies_keep_telemetry_as_configured(tmp_path: Path, policy: str) -> None:
    settings = _settings(tmp_path, policy)
    transport = RecordingTransport()
    client = TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=transport)

    client.emit("app_started")
    client.close()

    assert client.enabled is True
    assert [payload["event"] for payload in transport.payloads] == ["app_started"]
