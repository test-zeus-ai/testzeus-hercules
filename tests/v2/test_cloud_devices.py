"""The devices and desktop browsers a connected cloud offers: mapping each cloud's answers, the calls, the saved list,
the TestMu AI region, the daemon routes and ``hercules cloud devices``."""

from __future__ import annotations

import asyncio
import io
import json
import stat
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from testzeus_hercules.cli import cloud_cmd
from testzeus_hercules.cli.main import build_parser
from testzeus_hercules.clouds import devices as devices_module
from testzeus_hercules.clouds.credentials import (
    CredentialError,
    find_credentials,
    remove_credentials,
    save_credentials,
    save_region,
)
from testzeus_hercules.clouds.devices import (
    DesktopBrowser,
    DeviceFetchError,
    MobileDevice,
    parse_browserstack_desktop,
    parse_browserstack_mobile,
    catalog_path,
    device_urls,
    fetch_devices,
    load_catalog,
    parse_sauce_desktop,
    parse_sauce_real,
    parse_sauce_virtual,
    save_catalog,
    parse_testmu_desktop,
    parse_testmu_mobile,
    version_key,
)
from testzeus_hercules.daemon import clouds as cloud_api
from testzeus_hercules.kernel.settings import Settings
from tests.v2.fixtures.daemon_support import client_for, make_config

USER = "demo-user"
KEY = "k3y-value-not-shown"

BROWSERSTACK_DEVICES = [
    {"os": "ios", "os_version": "18", "device": "iPhone 16", "realMobile": True, "device_tier": "premium"},
    {"os": "ios", "os_version": "17", "device": "iPad Pro 12.9 2022", "realMobile": True},
    {"os": "android", "os_version": "14.0", "device": "Samsung Galaxy Tab S9", "realMobile": True},
    {"os": "android", "os_version": "15.0", "device": "Google Pixel 9", "realMobile": True},
    {"os": "android", "os_version": "15.0", "device": "Google Pixel 9", "realMobile": True},  # listed twice
    {"os": "windows", "os_version": "11", "device": "Surface"},  # not a phone or tablet
    {"os": "ios", "device": "no version"},
    "not an object",
]
BROWSERSTACK_BROWSERS = [
    {"os": "Windows", "os_version": "11", "browser": "chrome", "browser_version": "130.0", "device": None},
    {"os": "Windows", "os_version": "11", "browser": "chrome", "browser_version": "131.0", "device": None},
    {"os": "OS X", "os_version": "Sonoma", "browser": "safari", "browser_version": "17.3", "device": None},
    {"os": "ios", "os_version": "18", "browser": "iphone", "browser_version": None, "device": "iPhone 16", "real_mobile": True},
]
SAUCE_APPIUM = [
    {"api_name": "android", "long_name": "Google Pixel 8 GoogleAPI Emulator", "short_version": "14.0"},
    {"api_name": "android", "long_name": "Google Pixel 8 GoogleAPI Emulator", "short_version": "15.0"},
    {"api_name": "android", "long_name": "Android GoogleAPI Tablet Emulator", "short_version": "13.0"},
    {"api_name": "iphone", "long_name": "iPhone Simulator", "short_version": "17.5"},
    {"api_name": "ipad", "long_name": "iPad Simulator", "short_version": "18.0"},
    {"api_name": "chrome", "long_name": "Google Chrome", "short_version": "130"},
]
SAUCE_RDC = [
    {"name": "iPhone 15", "os": "IOS", "osVersion": "17.4", "isTablet": False, "isPrivate": True},
    {"name": "Samsung Galaxy Tab S8", "os": "ANDROID", "osVersion": "14", "isTablet": True},
]
SAUCE_WEBDRIVER = [
    {"api_name": "chrome", "long_name": "Google Chrome", "short_version": "130", "os": "Windows 11"},
    {"api_name": "chrome", "long_name": "Google Chrome", "short_version": "latest", "os": "Windows 11"},
    {"api_name": "MicrosoftEdge", "long_name": "Microsoft Edge", "short_version": "129", "os": "Mac 14"},
    {"api_name": "iphone", "long_name": "iPhone", "short_version": "17", "os": "Mac 14"},
    {"api_name": "internet explorer", "long_name": "Internet Explorer", "short_version": "11", "os": "Windows 2012 R2"},
]
TESTMU_DEVICES = [
    {"deviceId": "d1", "deviceName": "Galaxy S24", "platformName": "Android", "platformVersion": "14", "deviceStatus": "Available"},
    {"deviceId": "d2", "deviceName": "iPad Air", "platformName": "iOS", "platformVersion": "17", "deviceStatus": "Busy"},
]
TESTMU_PLATFORMS = {
    "status": "success",
    "platforms": [
        {"platform": "Windows 11", "browsers": [{"browser_name": "Chrome", "version": "130"}, {"browser_name": "Chrome", "version": "131"}]},
        {"platform": "macOS Sonoma", "browsers": [{"browser_name": "Safari", "version": "17"}]},
    ],
}


def _settings(tmp_path: Path, **env: str) -> Settings:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home"), **env})


def _cloud_handler(status: dict[str, int] | None = None, seen: list[httpx.Request] | None = None) -> Any:
    """Every cloud's device endpoints, answering by path; ``status`` overrides the answer for paths containing a key."""
    answers: dict[str, Any] = {
        "/app-automate/devices.json": BROWSERSTACK_DEVICES,
        "/automate/browsers.json": BROWSERSTACK_BROWSERS,
        "/rest/v1/info/platforms/appium": SAUCE_APPIUM,
        "/v1/rdc/devices": SAUCE_RDC,
        "/rest/v1/info/platforms/webdriver": SAUCE_WEBDRIVER,
        "/mobile-automation/api/v1/list": TESTMU_DEVICES,
        "/automation/api/v1/platforms": TESTMU_PLATFORMS,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        for fragment, code in (status or {}).items():
            if fragment in request.url.path:
                if code == 0:
                    raise httpx.ConnectError("down", request=request)
                return httpx.Response(code, json={"message": "no"})
        return httpx.Response(200, json=answers.get(request.url.path, {}))

    return handler


def _factory(handler: Any) -> Any:
    return lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)


def _fetch(tmp_path: Path, cloud: str, handler: Any, region: str | None = None) -> Any:
    settings = _settings(tmp_path)
    save_credentials(cloud, settings, USER, KEY, region)  # type: ignore[arg-type]
    return asyncio.run(fetch_devices(cloud, find_credentials(cloud, settings, {}), _factory(handler)))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- mapping


def test_browserstack_devices_are_mapped_with_their_kind_and_tier() -> None:
    devices = parse_browserstack_mobile(BROWSERSTACK_DEVICES)

    assert MobileDevice("iPhone 16", "ios", "18", "phone", real=True, tier="premium") in devices
    assert {device.name: device.kind for device in devices}["iPad Pro 12.9 2022"] == "tablet"
    assert {device.name: device.kind for device in devices}["Samsung Galaxy Tab S9"] == "tablet"
    assert all(device.name != "Surface" for device in devices)


def test_browserstack_desktop_keeps_only_desktop_browsers_grouped_newest_first() -> None:
    browsers = parse_browserstack_desktop(BROWSERSTACK_BROWSERS)

    assert DesktopBrowser("Windows", "11", "chrome", ("131.0", "130.0")) in browsers
    assert DesktopBrowser("macOS", "Sonoma", "safari", ("17.3",)) in browsers
    assert len(browsers) == 2


def test_sauce_virtual_devices_keep_the_newest_os_of_each_like_the_device_pool_script() -> None:
    devices = {device.name: device for device in parse_sauce_virtual(SAUCE_APPIUM)}

    assert devices["Google Pixel 8 GoogleAPI Emulator"].os_version == "15.0"
    assert devices["Android GoogleAPI Tablet Emulator"].kind == "tablet"
    assert devices["iPad Simulator"] == MobileDevice("iPad Simulator", "ios", "18.0", "tablet", real=False)
    assert devices["iPhone Simulator"].kind == "phone" and "Google Chrome" not in devices


def test_sauce_real_devices_and_desktop_browsers() -> None:
    real = parse_sauce_real(SAUCE_RDC)
    desktop = parse_sauce_desktop(SAUCE_WEBDRIVER)

    assert real == [MobileDevice("iPhone 15", "ios", "17.4", "phone", real=True, tier="private"), MobileDevice("Samsung Galaxy Tab S8", "android", "14", "tablet", real=True)]
    assert DesktopBrowser("Windows", "11", "chrome", ("latest", "130")) in desktop
    assert DesktopBrowser("macOS", "14", "edge", ("129",)) in desktop
    assert DesktopBrowser("Windows", "8.1", "ie", ("11",)) in desktop  # Sauce Labs names it after Windows Server 2012 R2
    assert len(desktop) == 3


def test_testmu_devices_and_platforms() -> None:
    assert parse_testmu_mobile(TESTMU_DEVICES) == [
        MobileDevice("Galaxy S24", "android", "14", "phone", real=True, status="Available"),
        MobileDevice("iPad Air", "ios", "17", "tablet", real=True, status="Busy"),
    ]
    assert parse_testmu_desktop(TESTMU_PLATFORMS) == [DesktopBrowser("Windows", "11", "chrome", ("131", "130")), DesktopBrowser("macOS", "Sonoma", "safari", ("17",))]
    grouped = {"platforms": {"Desktop": TESTMU_PLATFORMS["platforms"], "Mobile": [{"platform": "android", "browsers": [{"browser_name": "chrome"}]}]}}
    assert [browser.os for browser in parse_testmu_desktop(grouped)] == ["Windows", "macOS"]


@pytest.mark.parametrize("payload", [{"unexpected": True}, "text", None, 42])
def test_answers_that_are_not_lists_are_refused(payload: Any) -> None:
    for parse in (parse_browserstack_mobile, parse_browserstack_desktop, parse_sauce_virtual, parse_sauce_real, parse_sauce_desktop, parse_testmu_mobile, parse_testmu_desktop):
        with pytest.raises(ValueError):
            parse(payload)


def test_versions_sort_newest_first_with_words_before_numbers() -> None:
    assert sorted(["9", "latest", "17.4", "17.10", "beta"], key=version_key) == ["beta", "latest", "17.10", "17.4", "9"]


# --------------------------------------------------------------------------- fetching


@pytest.mark.parametrize(
    ("cloud", "region", "urls"),
    [
        ("browserstack", None, ["https://api-cloud.browserstack.com/app-automate/devices.json", "https://api.browserstack.com/automate/browsers.json"]),
        (
            "saucelabs",
            "eu-central-1",
            [
                "https://api.eu-central-1.saucelabs.com/rest/v1/info/platforms/appium",
                "https://api.eu-central-1.saucelabs.com/v1/rdc/devices",
                "https://api.eu-central-1.saucelabs.com/rest/v1/info/platforms/webdriver",
            ],
        ),
        ("testmu", None, ["https://mobile-api.lambdatest.com/mobile-automation/api/v1/list?region=us", "https://api.lambdatest.com/automation/api/v1/platforms"]),
        ("testmu", "eu", ["https://eu-mobile-api.lambdatest.com/mobile-automation/api/v1/list?region=eu", "https://api.lambdatest.com/automation/api/v1/platforms"]),
        ("testmu", "ap", ["https://mobile-api.lambdatest.com/mobile-automation/api/v1/list?region=ap", "https://api.lambdatest.com/automation/api/v1/platforms"]),
    ],
)
def test_each_cloud_is_asked_with_basic_auth_in_its_region(tmp_path: Path, cloud: str, region: str | None, urls: list[str]) -> None:
    seen: list[httpx.Request] = []

    catalog = _fetch(tmp_path, cloud, _cloud_handler(seen=seen), region)

    assert sorted(str(request.url) for request in seen) == sorted(urls)
    assert all(request.headers["authorization"].startswith("Basic ") for request in seen)
    assert catalog.username == USER and catalog.mobile and catalog.desktop
    assert device_urls(cloud, find_credentials(cloud, _settings(tmp_path), {})) == urls  # type: ignore[arg-type]
    assert KEY not in json.dumps(catalog.to_dict())


def test_the_list_is_deduplicated_and_sorted(tmp_path: Path) -> None:
    catalog = _fetch(tmp_path, "browserstack", _cloud_handler())

    names = [(device.platform, device.os_version, device.name) for device in catalog.mobile]
    assert names == [
        ("android", "15.0", "Google Pixel 9"),
        ("android", "14.0", "Samsung Galaxy Tab S9"),
        ("ios", "18", "iPhone 16"),
        ("ios", "17", "iPad Pro 12.9 2022"),
    ]
    assert catalog.region is None and catalog.notes == ()


def test_a_part_that_fails_becomes_a_note_and_the_rest_is_kept(tmp_path: Path) -> None:
    catalog = _fetch(tmp_path, "browserstack", _cloud_handler({"/automate/browsers.json": 0}))

    assert catalog.mobile and catalog.desktop == ()
    assert catalog.notes == ("desktop browsers: BrowserStack could not be reached (ConnectError)",)


def test_sauce_accounts_without_real_devices_get_a_note(tmp_path: Path) -> None:
    catalog = _fetch(tmp_path, "saucelabs", _cloud_handler({"/v1/rdc/devices": 403}))

    assert all(not device.real for device in catalog.mobile) and catalog.desktop
    assert catalog.notes == ("real devices: none on this account (HTTP 403)",)


def test_sauce_accounts_with_an_empty_real_device_list_get_a_note(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[]) if request.url.path == "/v1/rdc/devices" else _cloud_handler()(request)

    catalog = _fetch(tmp_path, "saucelabs", handler)

    assert catalog.notes == ("real devices: none on this account",) and catalog.mobile


def test_testmu_says_its_virtual_devices_are_not_listed(tmp_path: Path) -> None:
    catalog = _fetch(tmp_path, "testmu", _cloud_handler())

    assert all(device.real for device in catalog.mobile) and catalog.region == "us"
    assert any("TestMu AI does not list its emulators" in note for note in catalog.notes)


def test_refused_everywhere_is_an_error_without_the_key(tmp_path: Path) -> None:
    with pytest.raises(DeviceFetchError, match=r"refused the credentials \(HTTP 401\)") as raised:
        _fetch(tmp_path, "browserstack", _cloud_handler({"/": 401}))
    assert KEY not in str(raised.value)


def test_unreadable_answers_everywhere_are_an_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    with pytest.raises(DeviceFetchError, match="does not understand"):
        _fetch(tmp_path, "testmu", handler)


def test_a_cloud_that_is_not_connected_is_not_asked(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(DeviceFetchError, match="not connected"):
        asyncio.run(fetch_devices("browserstack", find_credentials("browserstack", settings, {}), _factory(_cloud_handler())))


# --------------------------------------------------------------------------- the saved list


def test_the_saved_list_is_private_and_reads_back(tmp_path: Path) -> None:
    catalog = _fetch(tmp_path, "saucelabs", _cloud_handler())
    home = tmp_path / "home"

    path = save_catalog(home, catalog)

    assert path == catalog_path(home, "saucelabs") and load_catalog(home, "saucelabs") == catalog
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert KEY not in path.read_text()


def test_an_unreadable_or_foreign_saved_list_is_ignored(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = catalog_path(home, "testmu")
    path.parent.mkdir(parents=True)
    for text in ("not json", json.dumps({"version": 99, "cloud": "testmu"}), json.dumps({"version": 1, "cloud": "browserstack", "username": "x", "fetched_at": "t"})):
        path.write_text(text)
        assert load_catalog(home, "testmu") is None


def test_new_keys_and_disconnecting_forget_the_saved_list(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    catalog = _fetch(tmp_path, "browserstack", _cloud_handler())
    save_catalog(settings.home_dir, catalog)

    save_region("saucelabs", settings, "us-east-4")  # another cloud's settings: kept
    assert load_catalog(settings.home_dir, "browserstack") is not None
    save_credentials("browserstack", settings, "other-user", KEY)
    assert load_catalog(settings.home_dir, "browserstack") is None

    save_catalog(settings.home_dir, catalog)
    remove_credentials("browserstack", settings)
    assert load_catalog(settings.home_dir, "browserstack") is None


# --------------------------------------------------------------------------- the TestMu AI region


def test_the_testmu_region_comes_from_the_environment_then_the_file_then_the_config(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    save_credentials("testmu", settings, USER, KEY)
    assert (find_credentials("testmu", settings, {}).region, find_credentials("testmu", settings, {}).region_source) == ("us", "default")

    save_region("testmu", settings, "AP")
    assert (find_credentials("testmu", settings, {}).region, find_credentials("testmu", settings, {}).region_source) == ("ap", "file")
    assert find_credentials("testmu", settings, {"LT_REGION": "eu"}).region_source == "env"
    assert find_credentials("saucelabs", settings, {}).region == "us-west-1"  # Sauce Labs keeps its own


def test_unknown_testmu_regions_are_refused(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for region in ("us-west-1", "mars", "eu.evil.example"):
        with pytest.raises(CredentialError, match="TestMu AI region must be one of us, eu, ap"):
            save_region("testmu", settings, region)
    with pytest.raises(CredentialError, match="only Sauce Labs and TestMu AI have a region"):
        save_region("browserstack", settings, "us")


def test_an_unknown_testmu_region_from_the_environment_is_not_used_in_a_host_name(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    save_credentials("testmu", settings, USER, KEY)
    seen: list[httpx.Request] = []

    with pytest.raises(DeviceFetchError, match="TestMu AI region must be one of"):
        asyncio.run(fetch_devices("testmu", find_credentials("testmu", settings, {"LT_REGION": "evil.example"}), _factory(_cloud_handler(seen=seen))))
    assert seen == []


# --------------------------------------------------------------------------- the daemon routes


@pytest.fixture()
def clouds(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Every cloud answering; set a path fragment to a status (0: unreachable) to make that part fail."""
    status: dict[str, int] = {}
    accept = lambda request: httpx.Response(200, json={})  # noqa: E731
    monkeypatch.setattr(cloud_api, "check_client_factory", _factory(accept))
    monkeypatch.setattr(cloud_api, "devices_client_factory", lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(_cloud_handler(status)), **kwargs))
    return status


def test_devices_are_fetched_saved_and_forgotten_on_disconnect(tmp_path: Path, clouds: dict[str, int]) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        before = client.get("/api/clouds/browserstack/devices")
        client.put("/api/clouds/browserstack", json={"username": USER, "access_key": KEY})
        fetched = client.post("/api/clouds/browserstack/devices")
        saved = client.get("/api/clouds/browserstack/devices")
        client.delete("/api/clouds/browserstack")
        after = client.get("/api/clouds/browserstack/devices")

    assert before.status_code == 404 and "no BrowserStack devices fetched yet" in before.json()["error"]
    body = fetched.json()
    assert fetched.status_code == 200 and body["cloud"] == "browserstack" and body["stale"] is False
    assert len(body["mobile"]) == 4 and body["desktop"][0]["versions"]
    assert saved.json() == body
    assert after.status_code == 404
    for response in (fetched, saved):
        assert KEY not in response.text


def test_the_saved_list_survives_a_daemon_restart(tmp_path: Path, clouds: dict[str, int]) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        client.put("/api/clouds/testmu", json={"username": USER, "access_key": KEY})
        fetched = client.post("/api/clouds/testmu/devices").json()
    with client_for(make_config(tmp_path)) as client:
        assert client.get("/api/clouds/testmu/devices").json() == fetched


def test_a_list_from_another_region_is_marked_stale(tmp_path: Path, clouds: dict[str, int]) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        client.put("/api/clouds/testmu", json={"username": USER, "access_key": KEY})
        client.post("/api/clouds/testmu/devices")
        region = client.put("/api/clouds/testmu/region", json={"region": "eu"})
        stale = client.get("/api/clouds/testmu/devices").json()
        refreshed = client.post("/api/clouds/testmu/devices").json()

    assert region.status_code == 200 and region.json()["region"] == "eu" and region.json()["regions"] == ["us", "eu", "ap"]
    assert stale["stale"] is True and stale["region"] == "us"
    assert refreshed["stale"] is False and refreshed["region"] == "eu"


def test_fetching_needs_a_connected_cloud_and_reports_a_refusal(tmp_path: Path, clouds: dict[str, int]) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        unconnected = client.post("/api/clouds/saucelabs/devices")
        unknown = client.post("/api/clouds/nowhere/devices")
        client.put("/api/clouds/saucelabs", json={"username": USER, "access_key": KEY})
        clouds["/"] = 401
        refused = client.post("/api/clouds/saucelabs/devices")

    assert unconnected.status_code == 409 and "not connected" in unconnected.json()["error"]
    assert unknown.status_code == 404
    assert refused.status_code == 502 and "refused the credentials" in refused.json()["error"] and KEY not in refused.text


def test_the_clouds_list_offers_testmu_regions(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        listed = {cloud["id"]: cloud for cloud in client.get("/api/clouds").json()["clouds"]}

    assert (listed["testmu"]["region"], listed["testmu"]["region_source"], listed["testmu"]["regions"]) == ("us", "default", ["us", "eu", "ap"])
    assert listed["browserstack"]["regions"] == []


# --------------------------------------------------------------------------- hercules cloud devices


def test_the_cli_fetches_once_then_shows_the_saved_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"HERCULES_HOME": str(tmp_path / "home"), "HERCULES_TELEMETRY": "0", "NO_COLOR": "1"}
    settings = _settings(tmp_path)
    save_credentials("browserstack", settings, USER, KEY)
    seen: list[httpx.Request] = []
    real_fetch = devices_module.fetch_devices

    async def fetch(cloud: Any, credentials: Any) -> Any:
        return await real_fetch(cloud, credentials, _factory(_cloud_handler(seen=seen)))

    monkeypatch.setattr(devices_module, "fetch_devices", fetch)
    monkeypatch.chdir(tmp_path / "project")

    def run(*extra: str) -> tuple[int, str, str]:
        args = build_parser().parse_args(["cloud", "devices", "browserstack", *extra])
        out, err = io.StringIO(), io.StringIO()
        return cloud_cmd.run_cloud_command(args, env=env, out=out, err=err), out.getvalue(), err.getvalue()

    code, text, _ = run()
    again, as_json, _ = run("--json")
    refreshed, _, _ = run("--refresh")

    assert code == 0 and "4 mobile devices, 2 desktop browsers" in text and "iPhone 16" in text and "Windows 11" in text
    assert again == 0 and json.loads(as_json)["cloud"] == "browserstack"
    assert refreshed == 0 and len(seen) == 4  # two calls for the first fetch, none for the saved list, two for --refresh


def test_the_cli_refuses_an_unconnected_cloud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"HERCULES_HOME": str(tmp_path / "home"), "HERCULES_TELEMETRY": "0"}
    (tmp_path / "project").mkdir()
    monkeypatch.chdir(tmp_path / "project")
    err = io.StringIO()

    code = cloud_cmd.run_cloud_command(build_parser().parse_args(["cloud", "devices", "testmu"]), env=env, out=io.StringIO(), err=err)

    assert code == 2 and "TestMu AI is not connected" in err.getvalue()


def test_the_cli_saves_a_testmu_region(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"HERCULES_HOME": str(tmp_path / "home"), "HERCULES_TELEMETRY": "0"}
    (tmp_path / "project").mkdir()
    monkeypatch.chdir(tmp_path / "project")
    monkeypatch.setattr(cloud_cmd, "_check", lambda *args: 0)

    args = build_parser().parse_args(["cloud", "connect", "testmu", "--username", USER, "--region", "ap"])
    code = cloud_cmd.run_cloud_command(args, env=env, out=io.StringIO(), err=io.StringIO(), stdin=io.StringIO(KEY + "\n"))

    assert code == 0 and find_credentials("testmu", Settings.load(tmp_path / "project", env=env), {}).region == "ap"
