"""TestMu AI (formerly LambdaTest) for native app runs, credentials saved in ``clouds.env``, and the Appium export for
any device cloud (:mod:`testzeus_hercules.clouds.mobile`). Offline: HTTP is played by ``httpx.MockTransport`` or the
fake Appium server on loopback."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from testzeus_hercules.cases.automation import AutomationSpec, DeviceAction
from testzeus_hercules.cases.models import MobileApp, Step, TestCase
from testzeus_hercules.clouds.credentials import CloudCredentials, find_credentials, save_credentials
from testzeus_hercules.clouds.mobile import APP_ENV, DEVICE_NAME_ENV, PLATFORM_VERSION_ENV, RUNNER_SET_ENV, app_file_of, appium_export_for_cloud, upload_app_to_cloud
from testzeus_hercules.export import ExportError
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.mobile.cloud import CloudError, report_status, upload_app
from testzeus_hercules.mobile.target import TargetError, capabilities, resolve_target

from tests.v2.fixtures.fake_appium import FakeDevice, serve_fake_appium
from tests.v2.test_export_appium import app_spec, mobile
from tests.v2.test_export_render import result
from tests.v2.test_mobile_run import project, run

USER, KEY = "lt-user-3c9e", "lt-key-81fa"
LT_ENV = {"LT_USERNAME": USER, "LT_ACCESS_KEY": KEY}
CREDENTIAL_ENV = {"browserstack": ("BROWSERSTACK_USERNAME", "BROWSERSTACK_ACCESS_KEY"), "saucelabs": ("SAUCE_USERNAME", "SAUCE_ACCESS_KEY"), "testmu": ("LT_USERNAME", "LT_ACCESS_KEY")}


def settings(tmp_path: Path, policy: str = "hybrid") -> Settings:
    return Settings.load(tmp_path, env={"HERCULES_HOME": str(tmp_path / "home"), "HERCULES_EGRESS": policy})


def creds(cloud: str) -> CloudCredentials:
    names = CREDENTIAL_ENV[cloud]
    return CloudCredentials(cloud, *names, source="env", username=USER, access_key=KEY)  # type: ignore[arg-type]


class Recorder:
    """An ``httpx.AsyncClient`` factory answering every request with ``answer`` and keeping the requests."""

    def __init__(self, answer: Callable[[httpx.Request], httpx.Response]) -> None:
        self.answer = answer
        self.requests: list[httpx.Request] = []

    def __call__(self, **kwargs: Any) -> httpx.AsyncClient:
        def handle(request: httpx.Request) -> httpx.Response:
            request.read()
            self.requests.append(request)
            return self.answer(request)

        kwargs.pop("trust_env", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)


# ---------------------------------------------------------------------------- the case and the target


def test_a_testmu_app_id_needs_a_cloud() -> None:
    assert MobileApp(platform="android", app="lt://APP1016abc_x-y", cloud="testmu").cloud == "testmu"
    with pytest.raises(ValueError, match="browserstack, saucelabs or testmu"):
        MobileApp(platform="android", app="lt://APP1016")
    with pytest.raises(ValueError, match="lt://"):
        MobileApp(platform="android", app="lt://bad/id", cloud="testmu")


def test_testmu_runs_on_its_mobile_hub_with_lt_options(tmp_path: Path) -> None:
    app = MobileApp(platform="android", app="lt://APP1016", cloud="testmu", device="Galaxy S21 5G", platform_version="11", capabilities={"lt:options": {"video": True}})
    target = resolve_target(app, settings(tmp_path), LT_ENV, tmp_path)
    assert target.hub_url == "https://mobile-hub.lambdatest.com/wd/hub" and target.api_url == "https://manual-api.lambdatest.com"
    assert target.auth == (USER, KEY) and target.credential_env == ("LT_USERNAME", "LT_ACCESS_KEY") and not target.is_local
    caps = capabilities(target, run_name="run-1")
    assert caps["appium:app"] == "lt://APP1016"
    assert caps["lt:options"] == {
        "platformName": "Android",
        "deviceName": "Galaxy S21 5G",
        "platformVersion": "11",
        "isRealMobile": True,
        "build": "hercules",
        "name": "run-1",
        "w3c": True,
        "video": True,
    }
    assert "appium:deviceName" not in caps and KEY not in json.dumps(caps) and USER not in json.dumps(caps)
    with pytest.raises(TargetError, match="egress policy 'local' forbids the device cloud testmu"):
        resolve_target(app, settings(tmp_path, "local"), LT_ENV, tmp_path)


def test_credentials_saved_from_the_ui_are_used_and_missing_ones_say_where_to_put_them(tmp_path: Path) -> None:
    app = MobileApp(platform="ios", app="lt://APP1", cloud="testmu")
    config = settings(tmp_path)
    with pytest.raises(TargetError) as missing:
        resolve_target(app, config, {}, tmp_path)
    message = str(missing.value)
    assert "LT_USERNAME, LT_ACCESS_KEY" in message and "Integrations" in message and "clouds.env" in message
    save_credentials("testmu", config, USER, KEY)
    target = resolve_target(app, config, {}, tmp_path)
    assert target.auth == (USER, KEY)
    assert resolve_target(app, config, {"LT_USERNAME": "env-user", "LT_ACCESS_KEY": "env-key"}, tmp_path).auth == ("env-user", "env-key"), "the environment wins"
    with pytest.raises(TargetError) as partial:
        resolve_target(MobileApp(platform="ios", app="bs://abc", cloud="browserstack"), config, {"BROWSERSTACK_USERNAME": "u"}, tmp_path)
    assert "BROWSERSTACK_ACCESS_KEY" in str(partial.value) and "BROWSERSTACK_USERNAME" not in str(partial.value).split("(")[0]


# ---------------------------------------------------------------------------- upload and status


def _testmu_target(tmp_path: Path) -> Any:
    (tmp_path / "apps").mkdir(exist_ok=True)
    (tmp_path / "apps" / "demo.apk").write_bytes(b"PK\x03\x04 fake apk")
    return resolve_target(MobileApp(platform="android", app="apps/demo.apk", cloud="testmu"), settings(tmp_path), LT_ENV, tmp_path)


def test_a_testmu_upload_posts_app_file_and_name_and_returns_the_lt_id(tmp_path: Path) -> None:
    recorder = Recorder(lambda request: httpx.Response(200, json={"app_id": "APP1016", "name": "demo.apk", "type": "android", "app_url": "lt://APP1016"}))
    reference = asyncio.run(upload_app(_testmu_target(tmp_path), recorder))
    assert reference == "lt://APP1016"
    request = recorder.requests[0]
    assert request.method == "POST" and str(request.url) == "https://manual-api.lambdatest.com/app/upload/realDevice"
    assert request.headers["Authorization"] == "Basic " + base64.b64encode(f"{USER}:{KEY}".encode()).decode()
    body = request.content
    assert b'name="appFile"; filename="demo.apk"' in body and b'name="name"' in body and b"fake apk" in body


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (httpx.Response(401, json={"message": "unauthorized"}), "credentials were refused"),
        (httpx.Response(200, json={"message": "ok"}), "did not say where"),
        (httpx.Response(200, json={"app_url": "bs://not-ours"}), "did not say where"),
    ],
)
def test_a_failed_testmu_upload_is_a_cloud_error_without_secrets(tmp_path: Path, answer: httpx.Response, reason: str) -> None:
    with pytest.raises(CloudError, match=reason) as error:
        asyncio.run(upload_app(_testmu_target(tmp_path), Recorder(lambda request: answer)))
    assert KEY not in str(error.value) and USER not in str(error.value)


def test_testmu_status_is_set_by_a_script_in_the_session_before_it_ends(tmp_path: Path) -> None:
    recorder = Recorder(lambda request: httpx.Response(200, json={"value": None}))
    target = _testmu_target(tmp_path)
    asyncio.run(report_status(target, "sess-9", False, recorder))
    (request,) = recorder.requests
    assert str(request.url) == "https://mobile-hub.lambdatest.com/wd/hub/session/sess-9/execute/sync"
    assert json.loads(request.content) == {"script": "lambda-status=failed", "args": []}
    failing = Recorder(lambda request: httpx.Response(500, json={"value": {"error": "unknown error", "message": "no"}}))
    asyncio.run(report_status(target, "sess-9", True, failing))  # never raises
    assert [request.method for request in failing.requests] == ["POST"], "the session is not ended by the status call"


def test_a_testmu_run_end_to_end_reports_its_result_through_the_session(tmp_path: Path) -> None:
    with serve_fake_appium(FakeDevice(auth=(USER, KEY))) as (url, device):
        path = project(tmp_path, f"egress:\n  policy: hybrid\nmobile:\n  testmu:\n    hub_url: {url}/wd/hub\n    api_url: {url}\n")
        case = TestCase(id="c", title="c", environment={"mobile": {"platform": "android", "app": "lt://APP77", "cloud": "testmu", "device": "Pixel 7"}}, steps=[Step(action="verify", expected="'Network & internet' is visible")])  # type: ignore[arg-type]
        result_, run_dir = run(case, path, env=LT_ENV)
    assert result_.status == "passed", result_.hints
    caps = device.sessions[0]
    assert caps["appium:app"] == "lt://APP77" and caps["lt:options"]["deviceName"] == "Pixel 7" and caps["lt:options"]["isRealMobile"] is True
    scripts = [body for method, where, body, _ in device.requests if method == "POST" and where.endswith("/execute/sync")]
    assert scripts == [{"script": "lambda-status=passed", "args": []}]
    assert device.ended == 1
    assert KEY not in (run_dir / "run.json").read_text() and USER not in (run_dir / "run.json").read_text()


@pytest.mark.parametrize(
    ("cloud", "url", "field", "answer", "expected"),
    [
        ("browserstack", "https://api-cloud.browserstack.com/app-automate/upload", b'name="file"', {"app_url": "bs://abc"}, "bs://abc"),
        ("saucelabs", "https://api.us-west-1.saucelabs.com/v1/storage/upload", b'name="payload"', {"item": {"id": "f00d"}}, "storage:f00d"),
        ("testmu", "https://manual-api.lambdatest.com/app/upload/realDevice", b'name="appFile"', {"app_url": "lt://APP9"}, "lt://APP9"),
    ],
)
def test_upload_app_to_cloud_zips_an_ios_build_and_returns_the_cloud_s_id(tmp_path: Path, cloud: str, url: str, field: bytes, answer: dict[str, Any], expected: str) -> None:
    build = tmp_path / "Demo.app"
    build.mkdir()
    (build / "Info.plist").write_text("<plist/>")
    recorder = Recorder(lambda request: httpx.Response(200, json=answer))
    assert asyncio.run(upload_app_to_cloud(build, cloud, creds(cloud), settings(tmp_path), client_factory=recorder)) == expected  # type: ignore[arg-type]
    request = recorder.requests[0]
    assert str(request.url) == url and field in request.content and b'filename="Demo.app.zip"' in request.content
    archive = request.content[request.content.index(b"PK\x03\x04") :]
    assert "Demo.app/Info.plist" in zipfile.ZipFile(io.BytesIO(archive)).namelist()


def test_upload_app_to_cloud_refuses_missing_files_and_credentials(tmp_path: Path) -> None:
    config = settings(tmp_path)
    with pytest.raises(CloudError, match="not found"):
        asyncio.run(upload_app_to_cloud(tmp_path / "gone.apk", "testmu", creds("testmu"), config))
    (tmp_path / "a.apk").write_bytes(b"x")
    none = find_credentials("testmu", config, {})
    with pytest.raises(CloudError, match="LT_USERNAME and LT_ACCESS_KEY"):
        asyncio.run(upload_app_to_cloud(tmp_path / "a.apk", "testmu", none, config))
    with pytest.raises(CloudError, match="for saucelabs, not testmu"):
        asyncio.run(upload_app_to_cloud(tmp_path / "a.apk", "testmu", creds("saucelabs"), config))
    refused = Recorder(lambda request: httpx.Response(401, json={}))
    with pytest.raises(CloudError) as error:
        asyncio.run(upload_app_to_cloud(tmp_path / "a.apk", "browserstack", creds("browserstack"), config, client_factory=refused))
    assert KEY not in str(error.value)


def test_app_file_of_names_only_a_file_inside_the_project(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "shop.apk").write_bytes(b"x")

    def of(app: dict[str, Any]) -> Path | None:
        return app_file_of(result(automation=app_spec([mobile(1, "Back", DeviceAction(kind="back"))], app=app)), tmp_path)

    assert of({"platform": "android", "app": "apps/shop.apk"}) == (tmp_path / "apps" / "shop.apk").resolve()
    assert of({"platform": "android", "app": "lt://APP1", "cloud": "testmu"}) is None
    assert of({"platform": "android", "package": "com.example.shop"}) is None
    assert of({"platform": "android", "app": "apps/missing.apk"}) is None
    assert of({"platform": "android", "app": "../outside.apk"}) is None
    assert app_file_of(result(), tmp_path) is None, "a web run has no app"


# ---------------------------------------------------------------------------- the export for any cloud


def _run_on(app: dict[str, Any], server: dict[str, Any]) -> Any:
    return result(automation=app_spec([mobile(1, "Back", DeviceAction(kind="back"))], app=app, appium=server))


def _black(code: str, tmp_path: Path) -> None:
    path = tmp_path / "test_export.py"
    path.write_text(code, encoding="utf-8")
    black = subprocess.run([sys.executable, "-m", "black", "--target-version", "py313", "-l", "200", "--check", "--diff", "--quiet", str(path)], capture_output=True, text=True)
    if black.returncode == 1 and "No module named black" in black.stderr:
        return
    assert black.returncode == 0, black.stdout + black.stderr


RUNS = {
    "local": ({"platform": "android", "app": "apps/shop.apk", "device": "emulator-5554", "udid": "emulator-5554"}, {"url": "http://127.0.0.1:4723", "provider": "local"}),
    "browserstack": (
        {"platform": "android", "app": "bs://abc", "cloud": "browserstack", "device": "Pixel 7", "capabilities": {"bstack:options": {"networkLogs": True}, "appium:language": "fr"}},
        {"url": "https://hub-cloud.browserstack.com/wd/hub", "provider": "browserstack", "username_env": "MY_BS_USER", "access_key_env": "MY_BS_KEY", "app_ref": "bs://abc"},
    ),
    "testmu": ({"platform": "ios", "app": "lt://APP5", "cloud": "testmu", "device": "iPhone 15"}, {"url": "https://mobile-hub.lambdatest.com/wd/hub", "provider": "testmu"}),
}


@pytest.mark.parametrize("ran_on", sorted(RUNS))
@pytest.mark.parametrize("cloud", ["browserstack", "saucelabs", "testmu"])
def test_any_native_run_exports_for_any_cloud(tmp_path: Path, ran_on: str, cloud: str) -> None:
    app, server = RUNS[ran_on]
    exported = appium_export_for_cloud(_run_on(app, server), cloud, platform_version="14")  # type: ignore[arg-type]
    code = exported.code
    compile(code, exported.filename, "exec")
    _black(code, tmp_path)
    assert exported.format == "appium-py" and exported.filename == "tests/test_shop_checkout.py"
    hub = {"browserstack": "https://hub-cloud.browserstack.com/wd/hub", "saucelabs": "https://ondemand.us-west-1.saucelabs.com:443/wd/hub", "testmu": "https://mobile-hub.lambdatest.com/wd/hub"}[cloud]
    assert f'APPIUM_URL = os.environ.get("APPIUM_URL", "{hub}")' in code
    username, key = ("MY_BS_USER", "MY_BS_KEY") if ran_on == cloud == "browserstack" else CREDENTIAL_ENV[cloud]
    assert f'os.environ["{username}"]' in code and f'os.environ["{key}"]' in code
    names = {item.name: item for item in exported.env}
    assert names[username].required and names[key].required and "APPIUM_UDID" not in names
    same_app = ran_on == cloud
    assert names[APP_ENV].required is not same_app
    assert (f'os.environ["{APP_ENV}"]' in code) is not same_app
    assert names[PLATFORM_VERSION_ENV].purpose.endswith("(default 14)") and DEVICE_NAME_ENV in names
    vendor = {"browserstack": "bstack:options", "saucelabs": "sauce:options", "testmu": "lt:options"}
    assert all(vendor[other] not in code for other in vendor.keys() - {cloud})
    if ran_on != cloud:
        assert "bs://abc" not in code and "lt://APP5" not in code
    if ran_on == "local":
        assert "emulator-5554" not in code, "a local device's name names no cloud device"
    if cloud == "testmu":
        assert 'caps["lt:options"]' in code and '"isRealMobile": True,' in code and '"w3c": True,' in code
        assert 'options["platformVersion"] = os.environ.get("APPIUM_PLATFORM_VERSION", "14")' in code
        assert 'session.execute_script("lambda-status=" + ("failed" if request.session.testsfailed > failed_before else "passed"))' in code
    else:
        assert "lambda-status" not in code


def test_the_export_keeps_the_case_s_own_capabilities_but_not_another_cloud_s() -> None:
    app, server = RUNS["browserstack"]
    code = appium_export_for_cloud(_run_on(app, server), "testmu").code
    assert 'caps.update({"appium:language": "fr"})' in code and "networkLogs" not in code
    assert "networkLogs" in appium_export_for_cloud(_run_on(app, server), "browserstack").code


def test_device_and_platform_version_override_the_run_s() -> None:
    app, server = RUNS["testmu"]
    code = appium_export_for_cloud(_run_on(app, server), "testmu", device="iPhone 16", platform_version="18").code
    assert 'os.environ.get("APPIUM_DEVICE_NAME", "iPhone 16")' in code and 'os.environ.get("APPIUM_PLATFORM_VERSION", "18")' in code
    assert 'caps["appium:app"] = os.environ.get("APPIUM_APP", "lt://APP5")' in code


def test_the_exported_test_never_holds_credential_values(tmp_path: Path) -> None:
    with serve_fake_appium(FakeDevice(auth=(USER, KEY))) as (url, _):
        path = project(tmp_path, f"egress:\n  policy: hybrid\nmobile:\n  testmu:\n    hub_url: {url}/wd/hub\n    api_url: {url}\n")
        case = TestCase(id="c", title="c", environment={"mobile": {"platform": "android", "app": "lt://APP77", "cloud": "testmu"}}, steps=[Step(action="verify", expected="'Network & internet' is visible")])  # type: ignore[arg-type]
        recorded, _ = run(case, path, env=LT_ENV)
    for cloud in ("browserstack", "saucelabs", "testmu"):
        exported = appium_export_for_cloud(recorded, cloud)  # type: ignore[arg-type]
        assert KEY not in exported.code and USER not in exported.code and url not in exported.code.replace("http://", "")
        compile(exported.code, exported.filename, "exec")


def test_a_web_run_or_a_run_without_a_record_is_not_exported_for_a_device_cloud() -> None:
    with pytest.raises(ExportError, match="web app"):
        appium_export_for_cloud(result(), "testmu")
    with pytest.raises(ExportError, match="no automation record"):
        appium_export_for_cloud(result().model_copy(update={"automation": None}), "testmu")
    with pytest.raises(ValueError, match="unknown cloud"):
        appium_export_for_cloud(_run_on(*RUNS["testmu"]), "nowhere")  # type: ignore[arg-type]


def test_a_failed_run_is_exported_for_a_cloud_only_when_allowed() -> None:
    app, server = RUNS["testmu"]
    spec: AutomationSpec = app_spec([mobile(1, "Back", DeviceAction(kind="back")), mobile(2, "Home", DeviceAction(kind="home"))], app=app, appium=server)
    failed = result(status="failed", automation=spec, step_statuses=["passed", "failed"])
    with pytest.raises(ExportError, match="did not pass"):
        appium_export_for_cloud(failed, "browserstack")
    assert "PARTIAL" in appium_export_for_cloud(failed, "browserstack", allow_failed=True).code


def test_the_runner_supplies_the_app_id() -> None:
    assert APP_ENV == "APPIUM_APP" and RUNNER_SET_ENV == frozenset({"APPIUM_APP"})
