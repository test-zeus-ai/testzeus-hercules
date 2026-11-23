"""Native app runs end to end through the run graph: a local Appium server and the device clouds (BrowserStack,
Sauce Labs), all played by a fake WebDriver server on loopback. No browser is ever started."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.cases.models import RunResult, Step, TestCase
from testzeus_hercules.decide.backends.stub import StubDecider
from testzeus_hercules.decide.contract import NoulAnswer
from testzeus_hercules.engine.run_graph import RunDeps, run_case
from testzeus_hercules.export import ExportError, export_run
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.run_context import ListEventSink, RunContext
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import RecordingTransport, TelemetryClient

from tests.v2.fixtures.fake_appium import FakeDevice, serve_fake_appium
from tests.v2.test_mobile_session import ScriptedModel, tap

USER, KEY = "cloud-user-51ab", "cloud-key-77de"


class NoBrowser:
    def open(self, **kwargs: Any) -> Any:
        raise AssertionError("a native app case must not start a browser")


class Interpreter:
    """The run's model, as the run graph reaches it (``interpreter.model``)."""

    model_id = ""

    def __init__(self, model: Any) -> None:
        self.model = model


def project(tmp_path: Path, config: str = "") -> Path:
    path = tmp_path / "project"
    (path / ".hercules").mkdir(parents=True)
    (path / ".hercules" / "config.yaml").write_text(config or "telemetry:\n  enabled: false\n")
    return path


def run(case: TestCase, path: Path, *, model: Any = None, decider: Any = None, env: dict[str, str] | None = None, events: ListEventSink | None = None) -> tuple[RunResult, Path]:
    settings = Settings.load(path, env={"HERCULES_HOME": str(path.parent / "home")})
    telemetry = TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=RecordingTransport())
    ctx = RunContext.create(settings, telemetry=telemetry, events=events)
    deps = RunDeps(decider=decider or StubDecider(), interpreter=Interpreter(model or ScriptedModel()), launcher=NoBrowser(), env=env or {})  # type: ignore[arg-type]
    result = asyncio.run(run_case(case, ctx, deps))
    return result, ctx.paths.run_dir(result.run_id)


def settings_case(**mobile: Any) -> TestCase:
    app = {"platform": "android", "package": "com.example.settings", "activity": ".Settings", **mobile}
    return TestCase(
        id="android-settings",
        title="Open the network settings",
        environment={"mobile": app},  # type: ignore[arg-type]
        preconditions=["The settings list is open"],
        steps=[Step(action="Tap 'Network & internet'", expected="'Internet' is visible"), Step(action="verify", expected="'SIMs' is visible")],
    )


def test_a_native_app_case_runs_on_a_local_appium_server(tmp_path: Path) -> None:
    with serve_fake_appium() as (url, device):
        path = project(tmp_path, f"telemetry:\n  enabled: false\nmobile:\n  appium_url: {url}\n")
        events = ListEventSink()
        decider = StubDecider({"e1": NoulAnswer(noul=0.9)})  # the precondition holds
        result, run_dir = run(settings_case(), path, model=ScriptedModel(tap("Network & internet"), {"action": "done"}), decider=decider, events=events)
    assert result.status == "passed", result.hints
    assert [step.status for step in result.steps] == ["passed", "passed"]
    assert result.assertions.passed == 2 and result.verdict_source == "ledger"
    assert device.ended == 1 and len(device.sessions) == 1
    assert (run_dir / "run.json").is_file() and (run_dir / "junit.xml").is_file()
    assert 'tests="2"' in (run_dir / "junit.xml").read_text() and 'failures="0"' in (run_dir / "junit.xml").read_text()
    assert any(uri.endswith("steps/1/plain.jpg") for uri in result.steps[0].evidence)
    assert any(uri.endswith("page_source.xml") for uri in result.steps[0].evidence)
    assert decider.calls[0].purpose == "precondition"
    kinds = [event.kind for event in events.events]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished" and "screenshot" in kinds and kinds.count("step_finished") == 2
    assert result.automation is not None and result.automation.app is not None and result.automation.steps[0].kind == "mobile"
    with pytest.raises(ExportError, match="native app"):
        export_run(result, "playwright-ts")
    assert '"kind": "mobile"' in export_run(result, "spec").code


def test_an_unmet_precondition_stops_the_run_before_any_step(tmp_path: Path) -> None:
    with serve_fake_appium() as (url, device):
        path = project(tmp_path, f"mobile:\n  appium_url: {url}\n")
        result, _ = run(settings_case(), path, model=ScriptedModel(), decider=StubDecider({"e1": NoulAnswer(noul=0.05)}))
    assert result.status == "error" and "precondition not met" in result.hints[0]
    assert device.ended == 1 and all(step.status == "skipped" for step in result.steps)


def test_no_appium_server_is_a_setup_error_with_the_fix(tmp_path: Path) -> None:
    path = project(tmp_path, "mobile:\n  appium_url: http://127.0.0.1:9\n")
    result, _ = run(settings_case(), path)
    assert result.status == "error" and "no Appium server answers" in result.hints[0] and "appium driver install uiautomator2" in result.hints[0]
    assert result.failure is not None


def test_a_remote_appium_server_under_the_local_policy_is_never_contacted(tmp_path: Path) -> None:
    with serve_fake_appium() as (url, device):
        remote = url.replace("127.0.0.1", "localhost.example")  # a host that is not this machine
        path = project(tmp_path, "egress:\n  policy: local\n")
        result, _ = run(settings_case(appium_url=remote), path)
    assert result.status == "error" and "egress policy 'local' forbids the remote Appium server" in result.hints[0]
    assert device.requests == []


def _cloud_project(tmp_path: Path, url: str, provider: str, policy: str = "hybrid") -> Path:
    path = project(tmp_path, f"egress:\n  policy: {policy}\nmobile:\n  {provider}:\n    hub_url: {url}/wd/hub\n    api_url: {url}\n")
    (path / "apps").mkdir()
    (path / "apps" / "demo.apk").write_bytes(b"PK\x03\x04 fake apk")
    return path


CLOUD_ENV = {"browserstack": {"BROWSERSTACK_USERNAME": USER, "BROWSERSTACK_ACCESS_KEY": KEY}, "saucelabs": {"SAUCE_USERNAME": USER, "SAUCE_ACCESS_KEY": KEY}}


@pytest.mark.parametrize("provider, app_ref, status_path", [("browserstack", "bs://abc123", "/sessions/s1.json"), ("saucelabs", "storage:f00d", f"/rest/v1/{USER}/jobs/s1")])
def test_a_device_cloud_uploads_the_app_runs_and_reports_the_result(tmp_path: Path, provider: str, app_ref: str, status_path: str) -> None:
    with serve_fake_appium(FakeDevice(auth=(USER, KEY))) as (url, device):
        path = _cloud_project(tmp_path, url, provider)
        case = settings_case(cloud=provider, app="apps/demo.apk", device="Google Pixel 7", platform_version="13").model_copy(update={"preconditions": []})
        case = TestCase.model_validate({**case.model_dump(exclude_none=True), "environment": {"mobile": {**case.environment.mobile.model_dump(exclude_none=True), "package": None, "activity": None}}})  # type: ignore[union-attr]
        result, run_dir = run(case, path, model=ScriptedModel(tap("Network & internet"), {"action": "done"}), env=CLOUD_ENV[provider])
    assert result.status == "passed", result.hints
    assert len(device.uploads) == 1 and device.uploads[0][1] > 0
    caps = device.sessions[0]
    assert caps["appium:app"] == app_ref
    if provider == "browserstack":
        assert caps["bstack:options"]["deviceName"] == "Google Pixel 7" and caps["bstack:options"]["osVersion"] == "13"
    else:
        assert caps["appium:deviceName"] == "Google Pixel 7" and caps["sauce:options"]["build"] == "hercules"
    assert device.statuses and device.statuses[0][0] == status_path
    assert device.statuses[0][1] in ({"status": "passed"}, {"passed": True})
    expected_auth = "Basic " + base64.b64encode(f"{USER}:{KEY}".encode()).decode()
    assert all(auth == expected_auth for _, _, _, auth in device.requests)
    for file in run_dir.rglob("*"):
        if file.is_file() and file.suffix != ".jpg":
            text = file.read_text()
            assert KEY not in text and USER not in text, file.name
    assert KEY not in json.dumps(caps)


def test_a_failed_cloud_run_is_reported_as_failed(tmp_path: Path) -> None:
    with serve_fake_appium(FakeDevice(auth=(USER, KEY))) as (url, device):
        path = _cloud_project(tmp_path, url, "browserstack")
        case = TestCase(id="c", title="c", environment={"mobile": {"platform": "android", "app": "bs://abc", "cloud": "browserstack"}}, steps=[Step(action="verify", expected="'Bluetooth' is visible")])  # type: ignore[arg-type]
        result, _ = run(case, path, env=CLOUD_ENV["browserstack"])
    assert result.status == "failed"
    assert device.uploads == [] and device.sessions[0]["appium:app"] == "bs://abc"
    assert device.statuses[0][1] == {"status": "failed"}


def test_device_clouds_need_an_egress_policy_that_allows_remote_endpoints(tmp_path: Path) -> None:
    with serve_fake_appium() as (url, device):
        path = _cloud_project(tmp_path, url, "browserstack", policy="local")
        case = TestCase(id="c", title="c", environment={"mobile": {"platform": "android", "app": "bs://abc", "cloud": "browserstack"}}, steps=[Step(action="Tap 'x'")])  # type: ignore[arg-type]
        result, _ = run(case, path, env=CLOUD_ENV["browserstack"])
    assert result.status == "error" and "egress policy 'local' forbids the device cloud browserstack" in result.hints[0]
    assert device.requests == []


def test_refused_cloud_credentials_are_reported_without_the_values(tmp_path: Path) -> None:
    with serve_fake_appium(FakeDevice(auth=(USER, "the-right-key"))) as (url, _):
        path = _cloud_project(tmp_path, url, "browserstack")
        case = TestCase(id="c", title="c", environment={"mobile": {"platform": "android", "app": "apps/demo.apk", "cloud": "browserstack"}}, steps=[Step(action="Tap 'x'")])  # type: ignore[arg-type]
        result, run_dir = run(case, path, env=CLOUD_ENV["browserstack"])
    assert result.status == "error" and "credentials were refused" in result.hints[0]
    assert KEY not in (run_dir / "run.json").read_text() and USER not in (run_dir / "run.json").read_text()


def test_missing_cloud_credentials_name_the_variables(tmp_path: Path) -> None:
    path = project(tmp_path)
    case = TestCase(id="c", title="c", environment={"mobile": {"platform": "ios", "app": "storage:f00d", "cloud": "saucelabs"}}, steps=[Step(action="Tap 'x'")])  # type: ignore[arg-type]
    result, _ = run(case, path)
    assert result.status == "error" and "SAUCE_USERNAME, SAUCE_ACCESS_KEY" in result.hints[0]


def test_api_steps_in_a_native_app_case_run_beside_the_device(tmp_path: Path) -> None:
    from testzeus_hercules.cases.models import ApiCall

    with serve_fake_appium() as (url, device):
        path = project(tmp_path, f"mobile:\n  appium_url: {url}\n")
        case = TestCase(
            id="mixed",
            title="mixed",
            environment={"mobile": {"platform": "android", "package": "com.example.settings"}, "base_url": url},  # type: ignore[arg-type]
            steps=[Step(action="Check the server", api=ApiCall(method="GET", path="/status", expect={"status": 200})), Step(action="verify", expected="'Network & internet' is visible")],  # type: ignore[arg-type]
        )
        result, _ = run(case, path)
    assert [step.status for step in result.steps] == ["passed", "passed"]
