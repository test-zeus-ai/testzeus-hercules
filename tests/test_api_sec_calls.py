"""Offline tests for the nuclei wrapper (core.tools.api_sec_calls): log redaction and path confinement."""

import asyncio

import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools import api_sec_calls
from testzeus_hercules.utils.path_guard import PathConfinementError

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"


@pytest.fixture
def roots(tmp_path):
    conf = get_global_conf()
    original = dict(conf.get_config())
    conf.get_config()["PROJECT_SOURCE_ROOT"] = str(tmp_path)
    conf.get_config()["TEST_DATA_PATH"] = str(tmp_path)
    conf.get_config().pop("API_ALLOWED_PATHS", None)
    yield tmp_path
    conf.get_config().clear()
    conf.get_config().update(original)


@pytest.fixture
def fake_nuclei(monkeypatch):
    """Replace the installer and subprocess launch; record the argv and every log line."""
    seen = {"argv": None, "logs": []}

    async def _installed():
        return None

    class _Process:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def _spawn(*argv, **kwargs):
        seen["argv"] = list(argv)
        return _Process()

    monkeypatch.setattr(api_sec_calls, "ensure_nuclei_installed", _installed)
    monkeypatch.setattr(api_sec_calls.asyncio, "create_subprocess_exec", _spawn)
    monkeypatch.setattr(api_sec_calls, "file_logger", lambda message: seen["logs"].append(str(message)))
    monkeypatch.setattr(api_sec_calls.logger, "info", lambda message, *a, **k: seen["logs"].append(str(message)))
    return seen


def _run(roots, spec_path=None, target_url="http://example.test/", headers=None, is_spec=False):
    return asyncio.run(api_sec_calls.run_nuclei_command(is_spec, spec_path, target_url, "cve", roots / "out.txt", headers))


def test_bearer_and_jwt_headers_are_redacted_in_logs_but_executed_unchanged(roots, fake_nuclei) -> None:
    headers = [("Authorization", f"Bearer {JWT}"), ("X-Custom", "plain-secret"), ("Authorization", f"JWT {JWT}")]
    _run(roots, headers=headers)
    logged = "\n".join(fake_nuclei["logs"])
    assert "Running Nuclei command" in logged
    assert JWT not in logged
    assert "plain-secret" not in logged
    assert "Authorization: [REDACTED]" in logged and "X-Custom: [REDACTED]" in logged
    # The process still receives the real values.
    argv = fake_nuclei["argv"]
    assert f"Authorization: Bearer {JWT}" in argv
    assert "X-Custom: plain-secret" in argv


def test_target_url_credentials_are_redacted_in_logs(roots, fake_nuclei) -> None:
    _run(roots, target_url="https://user:hunter2@example.test/path?token=abc123")
    logged = "\n".join(fake_nuclei["logs"])
    assert "hunter2" not in logged and "abc123" not in logged
    assert "https://user:hunter2@example.test/path?token=abc123" in fake_nuclei["argv"]


def test_redact_command_for_log_handles_header_without_colon_and_trailing_flag() -> None:
    assert api_sec_calls.redact_command_for_log(["nuclei", "-H", "opaquevalue"]) == "nuclei -H [REDACTED]"
    assert api_sec_calls.redact_command_for_log(["nuclei", "-H"]) == "nuclei -H"


def test_open_api_spec_inside_roots_is_resolved_and_passed(roots, fake_nuclei) -> None:
    spec = roots / "spec.yaml"
    spec.write_text("openapi: 3.0.0\n", encoding="utf-8")
    _run(roots, spec_path="spec.yaml", target_url=None, is_spec=True)
    argv = fake_nuclei["argv"]
    assert argv[argv.index("-l") + 1] == str(spec.resolve())


def test_open_api_spec_outside_roots_is_refused_before_anything_runs(roots, fake_nuclei, tmp_path_factory) -> None:
    outside = tmp_path_factory.mktemp("outside") / "spec.yaml"
    outside.write_text("openapi: 3.0.0\n", encoding="utf-8")
    with pytest.raises(PathConfinementError):
        _run(roots, spec_path=str(outside), target_url=None, is_spec=True)
    assert fake_nuclei["argv"] is None


def test_open_api_spec_traversal_is_refused(roots, fake_nuclei) -> None:
    with pytest.raises(PathConfinementError):
        _run(roots, spec_path="../../../../etc/passwd", target_url=None, is_spec=True)
    assert fake_nuclei["argv"] is None
