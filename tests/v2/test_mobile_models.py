"""Case, environment, settings and import formats of mobile testing: device profiles and native apps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from testzeus_hercules.cases.devices import UnknownDevice, canonical_name, context_options, device_names, viewport_of
from testzeus_hercules.cases.gherkin import GherkinError, compile_feature
from testzeus_hercules.cases.importers.v1 import V1ImportOptions, import_v1
from testzeus_hercules.cases.models import EnvironmentRef, MobileApp, Step, TestCase
from testzeus_hercules.daemon.cases import CaseError, apply_environment, save_environment
from testzeus_hercules.daemon.models import Environment
from testzeus_hercules.kernel.settings import Settings, SettingsError

SETTINGS_APP = {"platform": "android", "package": "com.android.settings", "activity": ".Settings"}


def case(environment: dict) -> TestCase:
    return TestCase(id="c", title="c", environment=environment, steps=[Step(action="Tap 'Network & internet'")])  # type: ignore[arg-type]


# ── device profiles ──────────────────────────────────────────────────────────


def test_device_profiles_come_from_playwright() -> None:
    names = device_names()
    assert "iPhone 15" in names and "Pixel 7" in names
    options = context_options("iPhone 15")
    assert options["is_mobile"] is True and options["has_touch"] is True and options["device_scale_factor"] == 3
    assert "iPhone" in options["user_agent"] and "default_browser_type" not in options
    assert viewport_of("Pixel 7") == (options_viewport := tuple(context_options("Pixel 7")["viewport"].values())) and options_viewport[0] < 600


def test_device_names_are_matched_without_case_and_unknown_ones_are_refused() -> None:
    assert canonical_name("iphone   15") == "iPhone 15"
    assert case({"base_url": "http://shop.test", "device": "pixel 7"}).environment.device == "Pixel 7"
    with pytest.raises(UnknownDevice, match="did you mean"):
        canonical_name("iPhone")
    with pytest.raises(ValidationError, match="unknown device"):
        case({"base_url": "http://shop.test", "device": "Nokia 3310"})


# ── native apps ──────────────────────────────────────────────────────────────


def test_a_native_app_by_package_by_bundle_or_by_file() -> None:
    assert case({"mobile": SETTINGS_APP}).environment.mobile.app_id == "com.android.settings"  # type: ignore[union-attr]
    ios = MobileApp(platform="ios", bundle_id="com.apple.Preferences", device="iPhone 15", platform_version="17.5")
    assert ios.app_id == "com.apple.Preferences"
    assert MobileApp(platform="android", app="apps/demo.apk").app == "apps/demo.apk"
    assert MobileApp(platform="ios", app="build/Demo.app").app == "build/Demo.app"
    assert MobileApp(platform="android", app="bs://c700ce60cf13ae8ed97705a55b8e022f13c5827c", cloud="browserstack").cloud == "browserstack"
    assert MobileApp(platform="ios", app="storage:filename=Demo.zip", cloud="saucelabs").cloud == "saucelabs"


@pytest.mark.parametrize(
    "fields, problem",
    [
        ({"platform": "android"}, "name the app"),
        ({"platform": "android", "bundle_id": "com.example.app"}, "bundle_id is for iOS"),
        ({"platform": "ios", "package": "com.example.app"}, "package and activity are for Android"),
        ({"platform": "android", "activity": ".Main", "app": "a.apk"}, "activity needs package"),
        ({"platform": "android", "app": "/etc/passwd.apk"}, "inside the project"),
        ({"platform": "android", "app": "../outside/app.apk"}, "inside the project"),
        ({"platform": "android", "app": "https://example.com/app.apk"}, "inside the project"),
        ({"platform": "android", "app": "notes.txt"}, "an app file"),
        ({"platform": "android", "app": "bs://abc"}, "needs cloud"),
        ({"platform": "android", "app": "a.apk", "cloud": "browserstack", "appium_url": "http://127.0.0.1:4723"}, "do not go together"),
        ({"platform": "android", "package": "com.example.app", "cloud": "saucelabs"}, "runs an app you give it"),
        ({"platform": "android", "package": "com.example.app", "appium_url": "http://user:pw@host:4723"}, "appium_url"),
        ({"platform": "android", "package": "com.example.app", "capabilities": {"bstack:options": {"accessKey": "x"}}}, "may not hold credentials"),
        ({"platform": "android", "package": "com.example.app", "capabilities": {"sauce:options": {"username": "u"}}}, "may not hold credentials"),
        ({"platform": "android", "package": "com.example.app", "capabilities": {"appium:app": "x.apk"}}, "with the app's own fields"),
    ],
)
def test_invalid_native_apps_are_refused_with_a_reason(fields: dict, problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        MobileApp.model_validate(fields)


def test_a_native_app_never_takes_browser_settings() -> None:
    for extra in ({"device": "iPhone 15"}, {"viewport": "390x844"}, {"browser": "chromium"}):
        with pytest.raises(ValidationError, match="runs on a device, not in a browser"):
            case({"mobile": SETTINGS_APP, **extra})


def test_extra_capabilities_are_kept() -> None:
    app = MobileApp(platform="android", package="com.example.app", capabilities={"appium:language": "en", "appium:locale": "US"})
    assert app.capabilities["appium:language"] == "en"


def test_the_case_schema_describes_device_and_mobile() -> None:
    schema = json.loads((Path(__file__).parents[2] / "testzeus_hercules" / "cases" / "schemas" / "test_case.schema.json").read_text())
    environment = schema["$defs"]["EnvironmentRef"]["properties"]
    assert "device" in environment and "mobile" in environment
    assert set(schema["$defs"]["MobileApp"]["properties"]) >= {"platform", "app", "package", "activity", "bundle_id", "device", "appium_url", "cloud", "capabilities"}


# ── Gherkin ──────────────────────────────────────────────────────────────────


def test_gherkin_tags_name_the_environment_and_the_device() -> None:
    feature = '@env:android-settings @device:iPhone_15\nFeature: x\n  Scenario: Open settings\n    When I tap "Network & internet"\n    Then "Internet" is visible\n'
    compiled = compile_feature(feature)
    assert compiled.environment.ref == "android-settings"
    assert compiled.environment.device == "iPhone 15"
    with pytest.raises(GherkinError, match="unknown device"):
        compile_feature(feature.replace("iPhone_15", "Brick_Phone"))


# ── saved environments (daemon) ──────────────────────────────────────────────


def test_saved_environments_hold_a_device_or_a_native_app(tmp_path: Path) -> None:
    web = Environment(name="phone", base_url="http://shop.test", device="iphone 15")
    assert web.device == "iPhone 15"
    native = Environment(name="android", mobile=MobileApp.model_validate(SETTINGS_APP))
    assert native.base_url is None
    with pytest.raises(ValidationError, match="needs base_url"):
        Environment(name="nothing")
    with pytest.raises(ValidationError, match="not in a browser"):
        Environment(name="both", mobile=MobileApp.model_validate(SETTINGS_APP), device="iPhone 15")
    for env in (web, native):
        save_environment(tmp_path, env, overwrite=False)

    from_native = apply_environment(case({"ref": "android"}), tmp_path, environment=None, base_url=None)
    assert from_native.environment.mobile is not None and from_native.environment.mobile.package == "com.android.settings"
    from_web = apply_environment(case({"ref": "phone"}), tmp_path, environment=None, base_url=None)
    assert from_web.environment.device == "iPhone 15" and from_web.environment.base_url == "http://shop.test"
    # A case's own target wins over the saved environment's.
    own = apply_environment(case({"ref": "phone", "mobile": SETTINGS_APP}), tmp_path, environment=None, base_url=None)
    assert own.environment.mobile is not None and own.environment.device is None
    with pytest.raises(CaseError):
        apply_environment(case({}), tmp_path, environment="missing", base_url=None)


# ── settings ─────────────────────────────────────────────────────────────────


def test_mobile_settings_name_credential_variables_only(tmp_path: Path) -> None:
    home = {"HERCULES_HOME": str(tmp_path / "home")}
    settings = Settings.load(tmp_path, env={**home, "HERCULES_APPIUM_URL": "http://127.0.0.1:4800"})
    assert settings.mobile.appium_url == "http://127.0.0.1:4800"
    assert settings.mobile.browserstack.username_env == "BROWSERSTACK_USERNAME"
    assert settings.mobile.saucelabs.region == "us-west-1"
    config = tmp_path / ".hercules" / "config.yaml"
    config.parent.mkdir()
    config.write_text("mobile:\n  browserstack:\n    hub_url: http://127.0.0.1:9/wd/hub\n  saucelabs:\n    access_key_env: MY_SAUCE_KEY\n")
    loaded = Settings.load(tmp_path, env=home)
    assert loaded.mobile.browserstack.hub_url == "http://127.0.0.1:9/wd/hub" and loaded.mobile.browserstack.username_env == "BROWSERSTACK_USERNAME"
    assert loaded.mobile.saucelabs.access_key_env == "MY_SAUCE_KEY"
    config.write_text("mobile:\n  browserstack:\n    access_key: abc123\n")
    with pytest.raises(SettingsError, match="looks like a secret"):
        Settings.load(tmp_path, env=home)


# ── v1 import ────────────────────────────────────────────────────────────────


def _v1_project(root: Path, env: str) -> Path:
    (root / "opt" / "input").mkdir(parents=True)
    (root / "opt" / "input" / "settings.feature").write_text("Feature: Settings\n  Scenario: Open network\n    When I tap Network\n    Then I see Internet\n")
    (root / ".env").write_text(env)
    return root


def test_v1_run_device_becomes_the_environment_device(tmp_path: Path) -> None:
    source = _v1_project(tmp_path / "v1", "BASE_URL=https://shop.example.com\nRUN_DEVICE=iPhone 15 Pro Max\n")
    project = tmp_path / "project"
    import_v1(V1ImportOptions(source_dir=source, project_dir=project))
    env = json.loads((project / ".hercules" / "envs" / "default.json").read_text())
    assert env["device"] == "iPhone 15 Pro Max" and env["base_url"] == "https://shop.example.com"


def test_v1_appium_settings_become_a_native_app_environment(tmp_path: Path) -> None:
    caps = json.dumps({"appium:appPackage": "com.android.settings", "appium:appActivity": ".Settings"})
    source = _v1_project(tmp_path / "v1", f"DEVICE_MANAGER=appium\nDEVICE_OS=android\nDEVICE_NAME=emulator-5554\nAPPIUM_CAPABILITIES={caps}\nAPP_PATH=/builds/app.apk\n")
    project = tmp_path / "project"
    report = import_v1(V1ImportOptions(source_dir=source, project_dir=project))
    env = Environment.model_validate_json((project / ".hercules" / "envs" / "default.json").read_text())
    assert env.base_url is None and env.mobile is not None
    assert (env.mobile.package, env.mobile.activity, env.mobile.device) == ("com.android.settings", ".Settings", "emulator-5554")
    imported = TestCase.model_validate_json(next((project / ".hercules" / "cases").glob("*.json")).read_text())
    assert imported.environment.ref == "default"
    assert any("app.apk" in item.reason for item in report.skipped)


def test_the_daemon_saves_mobile_environments_and_lists_devices(tmp_path: Path) -> None:
    from tests.v2.fixtures.daemon_support import client_for, make_config

    with client_for(make_config(tmp_path)) as client:
        devices = client.get("/api/devices").json()["devices"]
        native = client.post("/api/environments", json={"name": "android", "mobile": SETTINGS_APP})
        phone = client.post("/api/environments", json={"name": "phone", "base_url": "http://127.0.0.1:8000", "device": "pixel 7"})
        bad_device = client.post("/api/environments", json={"name": "bad", "base_url": "http://127.0.0.1:8000", "device": "Brick"})
        credentials = client.post("/api/environments", json={"name": "cloud", "mobile": {**SETTINGS_APP, "capabilities": {"bstack:options": {"accessKey": "x"}}}})
        listing = {env["name"]: env for env in client.get("/api/environments").json()["environments"]}
    assert "iPhone 15" in devices and devices == sorted(devices)
    assert native.status_code == 201 and phone.status_code == 201
    assert bad_device.status_code == 422 and credentials.status_code == 422
    assert listing["phone"]["device"] == "Pixel 7" and listing["android"]["mobile"]["package"] == "com.android.settings" and listing["android"]["base_url"] is None
