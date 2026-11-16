"""Browsers and Playwright options in an environment: validation, the launch, saved environments, the exported code,
cloud projects, and real launches in Chromium, Firefox and WebKit."""

from __future__ import annotations

import asyncio
import functools
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from testzeus_hercules.cases.automation import AutomationAction, AutomationSpec, AutomationStep
from testzeus_hercules.cases.browser_options import BrowserOptions, context_parameters, launch_parameters
from testzeus_hercules.cases.models import EnvironmentRef, RunResult, StepResult, TestCase
from testzeus_hercules.clouds.bundle import WebOptions, web_bundle
from testzeus_hercules.daemon.cases import apply_environment, save_environment
from testzeus_hercules.daemon.models import Environment
from testzeus_hercules.engine.browser_launch import BrowserSetupError, browser_launch
from testzeus_hercules.engine.run_graph import PlaywrightLauncher
from testzeus_hercules.export import export_run
from testzeus_hercules.export.common import ExportError

OPTIONS = {
    "locale": "de-DE",
    "timezone_id": "Asia/Tokyo",
    "color_scheme": "dark",
    "extra_http_headers": {"X-Test": "hello", "Authorization": {"secret": "API_TOKEN"}},
    "storage_state": "auth/state.json",
    "args": ["--lang=de-DE"],
    "context_extra": {"strict_selectors": True},
}

# --- the model ---------------------------------------------------------------------------------------------------


def test_every_playwright_browser_and_option_name_is_accepted() -> None:
    for browser in ("chromium", "chrome", "msedge", "firefox", "webkit"):
        assert EnvironmentRef(base_url="http://x.test", browser=browser).browser == browser
    env = EnvironmentRef.model_validate({"base_url": "http://x.test", "browser": "firefox", "browser_options": OPTIONS})
    assert env.browser_options is not None and env.browser_options.secret_names() == ["API_TOKEN"]
    assert {"timeout", "ignore_default_args", "chromium_sandbox"} <= launch_parameters()
    assert {"screen", "strict_selectors", "record_video_size"} <= context_parameters()


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"browser": "safari"}, "'chromium', 'chrome', 'msedge', 'firefox' or 'webkit'"),
        ({"browser": "firefox", "device": "iPhone 15"}, "Firefox cannot emulate"),
        ({"browser_options": {"firefox_user_prefs": {"a": 1}}}, "Firefox only"),
        ({"browser": "firefox", "browser_options": {"is_mobile": True}}, "not supported in Firefox"),
        ({"browser_options": {"context_extra": {"record_har_path": "x.har"}}}, "is not accepted"),
        ({"browser_options": {"launch_extra": {"executable_path": "/bin/sh"}}}, "is not accepted"),
        ({"browser_options": {"context_extra": {"stric_selectors": True}}}, "did you mean 'strict_selectors'"),
        ({"browser_options": {"context_extra": {"locale": "de"}}}, "with its own field"),
        ({"browser_options": {"storage_state": "/etc/state.json"}}, "inside the project"),
        ({"browser_options": {"storage_state": "../state.json"}}, "inside the project"),
        ({"browser_options": {"storage_state": "state.txt"}}, ".json file"),
        ({"browser_options": {"timezone_id": "Mars/Base"}}, "unknown time zone"),
        ({"browser_options": {"args": ["lang=de"]}}, "command-line switches"),
        ({"browser_options": {"extra_http_headers": {"Bad Header": "x"}}}, "not a valid header name"),
        ({"browser_options": {"http_credentials": {"username": "u", "password": "plain"}}}, "Secret"),
    ],
)
def test_options_that_cannot_work_are_refused_with_a_reason(environment: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        EnvironmentRef.model_validate({"base_url": "http://x.test", **environment})


def test_a_native_app_takes_no_browser_options() -> None:
    with pytest.raises(ValidationError, match="browser_options"):
        EnvironmentRef.model_validate({"mobile": {"platform": "android", "package": "com.example.app"}, "browser_options": {"locale": "de-DE"}})


# --- the launch ---------------------------------------------------------------------------------------------------


def _options(**extra: Any) -> BrowserOptions:
    return BrowserOptions.model_validate({**OPTIONS, **extra})


def test_the_launch_uses_the_options_and_reads_secrets_from_the_run_environment(tmp_path: Path) -> None:
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "state.json").write_text("{}")
    setup = browser_launch(
        "firefox", _options(record_video=True, slow_mo=50), env={"API_TOKEN": "t0ken"}, headless=True, project_dir=tmp_path, video_dir=tmp_path / "video", executable_path="/opt/chromium"
    )
    assert setup.engine == "firefox"
    assert setup.launch == {"headless": True, "slow_mo": 50, "args": ["--lang=de-DE"]}, "HERCULES_CHROMIUM is for Chromium only"
    assert setup.context["extra_http_headers"] == {"X-Test": "hello", "Authorization": "t0ken"}
    assert setup.context["storage_state"] == str((tmp_path / "auth" / "state.json").resolve())
    assert setup.context["record_video_dir"] == str(tmp_path / "video")
    assert setup.context["strict_selectors"] is True and setup.context["timezone_id"] == "Asia/Tokyo"
    assert "t0ken" not in repr(setup)


def test_chrome_and_edge_are_channels_and_headless_follows_the_run(tmp_path: Path) -> None:
    chrome = browser_launch("chrome", None, env={}, headless=True, executable_path="/opt/chromium")
    assert chrome.engine == "chromium" and chrome.launch == {"headless": True, "channel": "chrome"}
    shown = browser_launch(None, BrowserOptions(headless=False), env={}, headless=True, executable_path="/opt/chromium")
    assert shown.launch == {"headless": False, "executable_path": "/opt/chromium"}
    assert browser_launch(None, BrowserOptions(headless=True), env={}, headless=False).launch["headless"] is False, "--headed always shows the window"


def test_a_missing_secret_or_auth_file_is_named_never_shown(tmp_path: Path) -> None:
    with pytest.raises(BrowserSetupError, match="API_TOKEN, which is not set"):
        browser_launch(None, _options(), env={}, headless=True, project_dir=tmp_path)
    with pytest.raises(BrowserSetupError, match="auth/state.json does not exist"):
        browser_launch(None, _options(), env={"API_TOKEN": "x"}, headless=True, project_dir=tmp_path)


# --- saved environments -------------------------------------------------------------------------------------------


def test_a_saved_environment_gives_its_browser_and_the_case_overrides_single_options(tmp_path: Path) -> None:
    save_environment(
        tmp_path,
        Environment.model_validate({"name": "de", "base_url": "http://shop.test", "browser": "webkit", "browser_options": {"locale": "de-DE", "timezone_id": "Europe/Berlin"}}),
        overwrite=False,
    )
    case = TestCase.model_validate({"id": "c", "title": "C", "steps": [{"action": "Open the shop"}], "environment": {"ref": "de", "browser_options": {"locale": "fr-FR"}}})
    applied = apply_environment(case, tmp_path, environment=None, base_url=None).environment
    assert applied.browser == "webkit"
    assert applied.browser_options is not None and (applied.browser_options.locale, applied.browser_options.timezone_id) == ("fr-FR", "Europe/Berlin")


# --- the exported code --------------------------------------------------------------------------------------------


def _result(browser: str | None = "firefox", **options: Any) -> RunResult:
    spec = AutomationSpec(
        case_id="shop",
        title="Shop in another browser",
        base_url="http://127.0.0.1:8000",
        browser_name=browser,  # type: ignore[arg-type]
        browser_options=BrowserOptions.model_validate({**OPTIONS, **options}),
        steps=[AutomationStep(index=1, action="Open the home page", status="passed", actions=[AutomationAction(kind="navigate", url="/")])],
    )
    return RunResult(run_id="r_1", status="passed", case_id="shop", steps=[StepResult(index=1, action="Open the home page", status="passed")], automation=spec)


def test_the_pytest_export_sets_the_browser_launch_and_context(tmp_path: Path) -> None:
    exported = export_run(_result(record_video=True, firefox_user_prefs={"intl.accept_languages": "de"}), "playwright-py")
    code = exported.code
    compile(code, exported.filename, "exec")
    assert 'pytestmark = pytest.mark.only_browser("firefox")' in code and "pytest --browser firefox" in code
    assert 'args["firefox_user_prefs"] = {"intl.accept_languages": "de"}' in code
    assert 'args["extra_http_headers"] = {"X-Test": "hello", "Authorization": os.environ["API_TOKEN"]}' in code
    assert 'args["storage_state"] = os.environ.get("STORAGE_STATE", "auth/state.json")' in code
    assert 'args["strict_selectors"] = True' in code and 'args["record_video_dir"] = "videos/"' in code
    assert "HERCULES_CHROMIUM" not in code, "a Chromium build path does not apply to Firefox"
    env = {item.name: item.required for item in exported.env}
    assert env["API_TOKEN"] is True and env["STORAGE_STATE"] is False


def test_the_playwright_test_export_uses_test_use(tmp_path: Path) -> None:
    exported = export_run(_result("chrome"), "playwright-ts")
    code = exported.code
    assert 'test.use({ browserName: "chromium", channel: "chrome", launchOptions: { args: ["--lang=de-DE"] } });' in code
    assert 'locale: "de-DE", timezoneId: "Asia/Tokyo", colorScheme: "dark"' in code
    assert 'extraHTTPHeaders: {"X-Test": "hello", "Authorization": env("API_TOKEN")}' in code
    assert 'storageState: process.env.STORAGE_STATE ?? "auth/state.json"' in code and "contextOptions: { strictSelectors: true }" in code
    from tests.v2.export_harness import node, typescript_syntax_errors

    if node() is not None:
        assert typescript_syntax_errors(code, tmp_path) == []


def test_a_chromium_run_without_options_exports_as_before() -> None:
    plain = AutomationSpec(
        case_id="c", title="C", base_url="http://127.0.0.1:8000", steps=[AutomationStep(index=1, action="Open", status="passed", actions=[AutomationAction(kind="navigate", url="/")])]
    )
    code = export_run(RunResult(run_id="r", status="passed", case_id="c", steps=[StepResult(index=1, action="Open", status="passed")], automation=plain), "playwright-py").code
    assert "only_browser" not in code and '"executable_path": executable} if executable else browser_type_launch_args' in code


# --- cloud projects -----------------------------------------------------------------------------------------------


def test_a_firefox_run_goes_to_the_clouds_firefox_with_its_auth_file(tmp_path: Path) -> None:
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "state.json").write_text('{"cookies": [], "origins": []}')
    bundle = web_bundle(_result(), "browserstack", WebOptions(), project_dir=tmp_path)
    conftest = bundle.files["conftest.py"]
    compile(conftest, "conftest.py", "exec")
    assert bundle.web == WebOptions("firefox", "windows")
    assert 'BROWSER = os.environ.get("HERCULES_CLOUD_BROWSER", "firefox")' in conftest and "getattr(playwright, ENGINE).connect" in conftest
    assert "playwright-firefox" in conftest and "browserName: playwright-firefox" in bundle.files["browserstack.yml"]
    assert bundle.files["auth/state.json"].startswith('{"cookies"') and "Signed-in state" in bundle.files["README.md"]
    testmu = web_bundle(_result("webkit"), "testmu", WebOptions(), project_dir=tmp_path)
    assert "pw-webkit" in testmu.files["conftest.py"] and "playwright install webkit" in testmu.files["hyperexecute.yaml"]
    sauce = web_bundle(_result(), "saucelabs", WebOptions(), project_dir=tmp_path)
    assert "browserName: firefox" in sauce.files[".sauce/config.yml"] and "auth/state.json" in sauce.files


def test_cloud_browsers_must_match_the_runs_engine_and_the_auth_file_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="ran in Firefox"):
        web_bundle(_result(storage_state=None), "browserstack", WebOptions("chrome"))
    with pytest.raises(ExportError, match="ran in Chromium"):
        web_bundle(_result("chromium", storage_state=None), "browserstack", WebOptions("firefox"))
    assert web_bundle(_result("chromium", storage_state=None), "testmu", WebOptions()).web == WebOptions("chrome", "windows")
    with pytest.raises(ExportError, match="not in the project"):
        web_bundle(_result(), "browserstack", WebOptions(), project_dir=tmp_path)


# --- real browsers ------------------------------------------------------------------------------------------------


class _Echo(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        if self.path == "/headers":
            body = json.dumps({"x_test": self.headers.get("X-Test"), "authorization": self.headers.get("Authorization"), "cookie": self.headers.get("Cookie")}).encode()
            content_type = "application/json"
        else:
            body = b"<!doctype html><title>echo</title><p>hello</p>"
            content_type = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture
def echo() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@functools.lru_cache(maxsize=None)
def _launchable(engine: str) -> str | None:
    """None when Playwright's ``engine`` starts here, else why not (not installed, or it cannot start on this machine)."""
    import subprocess
    import sys

    found = subprocess.run([sys.executable, "-m", "playwright", "install", "--dry-run", engine], capture_output=True, text=True, check=False).stdout
    places = [line.split(":", 1)[1].strip() for line in found.splitlines() if "Install location:" in line]
    if not places or not Path(places[0]).is_dir():
        return f"Playwright's {engine} is not installed (python -m playwright install {engine})"

    async def probe() -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            launched = await getattr(playwright, engine).launch(timeout=30_000)
            await launched.close()

    try:
        asyncio.run(probe())
    except Exception as exc:  # e.g. Firefox on some macOS setups: run these in a Linux container
        return f"{engine} does not start on this machine ({type(exc).__name__})"
    return None


@pytest.mark.parametrize("browser", ["chromium", "firefox", "webkit"])
def test_each_engine_opens_with_the_options(browser: str, echo: str, tmp_path: Path) -> None:
    reason = _launchable(browser)
    if reason:
        pytest.skip(reason)
    (tmp_path / "auth").mkdir()
    state = {"cookies": [{"name": "session", "value": "signed-in", "domain": "127.0.0.1", "path": "/", "expires": -1, "httpOnly": False, "secure": False, "sameSite": "Lax"}], "origins": []}
    (tmp_path / "auth" / "state.json").write_text(json.dumps(state))
    options = BrowserOptions.model_validate({**OPTIONS, "args": [], "record_video": True})

    async def go() -> dict[str, Any]:
        launcher = PlaywrightLauncher(env={"API_TOKEN": "t0ken"})
        async with launcher.open(viewport=(800, 600), headless=True, browser=browser, options=options, project_dir=tmp_path, video_dir=tmp_path / "video") as page:
            await page.goto(echo + "/")
            seen = await page.evaluate(
                "async () => ({ language: navigator.language, zone: Intl.DateTimeFormat().resolvedOptions().timeZone,"
                " dark: matchMedia('(prefers-color-scheme: dark)').matches, headers: await (await fetch('/headers')).json() })"
            )
            seen["engine"] = page.context.browser.browser_type.name
            await page.wait_for_timeout(800)  # a page shown for less than about half a second leaves no video
            return seen

    seen = asyncio.run(go())
    assert seen["engine"] == browser
    assert seen["language"] == "de-DE" and seen["zone"] == "Asia/Tokyo" and seen["dark"] is True
    assert seen["headers"] == {"x_test": "hello", "authorization": "t0ken", "cookie": "session=signed-in"}
    assert list((tmp_path / "video").glob("*.webm")), "the video is kept with the run's evidence"


def test_the_live_view_and_take_over_work_without_cdp(echo: str) -> None:
    from testzeus_hercules.engine.live import LiveView

    reason = _launchable("firefox")
    if reason:
        pytest.skip(reason)

    frames: list[tuple[int, int]] = []

    async def go() -> str:
        live = LiveView(lambda jpeg, width, height, captured: frames.append((width, height)))
        async with PlaywrightLauncher().open(viewport=(640, 480), headless=True, browser="firefox") as page:
            await page.goto(echo + "/")
            live.request(True)
            await live.attach(page)
            for _ in range(40):
                if frames:
                    break
                await asyncio.sleep(0.1)
            done = await live.dispatch({"kind": "click", "x": 0.5, "y": 0.5})
            await live.close()
            return done

    assert asyncio.run(go()).startswith("clicked at")
    assert frames and frames[0] == (640, 480), "screenshots stand in for the screencast"


def test_a_firefox_or_webkit_export_has_no_chromium_build_fixture() -> None:
    code = export_run(_result("webkit", args=[], storage_state=None, extra_http_headers={}), "playwright-py").code
    assert "HERCULES_CHROMIUM" not in code and "def browser_type_launch_args" not in code
    assert 'pytestmark = pytest.mark.only_browser("webkit")' in code
