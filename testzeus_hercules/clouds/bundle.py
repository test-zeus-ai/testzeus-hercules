"""A ready-to-run test project for one cloud, made from a passed run's export.

Web runs on BrowserStack and TestMu AI (``connect``): the pytest-playwright export, a ``conftest.py`` whose
``browser`` fixture connects to the cloud's browser over a WebSocket and marks the session passed or failed,
``requirements.txt``, the cloud's own runner config for CI (``browserstack.yml`` for the BrowserStack SDK,
``hyperexecute.yaml`` for TestMu HyperExecute) and a README.

Web runs on Sauce Labs (``saucectl``): the Playwright Test (TypeScript) export, ``package.json``,
``playwright.config.ts``, ``.sauce/config.yml`` and a README; ``saucectl run`` uploads the project and runs it
in Sauce's cloud.

Native app runs: the Appium export for the chosen cloud, ``requirements.txt`` and a README.

Credentials are never written into any file: every file reads them from environment variables.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field, replace
from importlib import metadata
from pathlib import Path
from typing import Literal

from testzeus_hercules.cases.browser_options import engine_of
from testzeus_hercules.cases.models import RunResult
from testzeus_hercules.clouds.registry import CloudId, cloud_info
from testzeus_hercules.export import export_run
from testzeus_hercules.export.common import EnvVar, ExportError, ExportResult, safe_id, uploads_download

Browser = Literal["chrome", "edge", "chromium", "firefox", "webkit"]
OsName = Literal["windows", "macos"]
BROWSERS: tuple[Browser, ...] = ("chrome", "edge", "chromium", "firefox", "webkit")
# The cloud browser for a run's own browser (a Chromium run defaults to Chrome, as before).
_FOR_RUN: dict[str, Browser] = {"chromium": "chrome", "chrome": "chrome", "msedge": "edge", "firefox": "firefox", "webkit": "webkit"}
# The Playwright browser type that drives each cloud browser.
ENGINES: dict[str, str] = {"chrome": "chromium", "edge": "chromium", "chromium": "chromium", "firefox": "firefox", "webkit": "webkit"}
OS_NAMES: tuple[OsName, ...] = ("windows", "macos")
# Read by the connect conftest; "0" leaves the browser to pytest-playwright (under the BrowserStack SDK or HyperExecute).
CONNECT_ENV = "HERCULES_CLOUD_CONNECT"
BROWSER_ENV = "HERCULES_CLOUD_BROWSER"
OS_ENV = "HERCULES_CLOUD_OS"
BUILD_ENV = "HERCULES_CLOUD_BUILD"
# A Playwright server to connect to instead of the cloud's (a self-hosted grid, or a local server in tests).
WS_ENDPOINT_ENV = "HERCULES_CLOUD_WS_ENDPOINT"
# The connect conftest prints this marker with the cloud's session link, for the Hercules runner to pick up.
SESSION_MARKER = "HERCULES_CLOUD_SESSION_URL="

_BROWSERSTACK_BROWSER = {"chrome": "chrome", "edge": "edge", "chromium": "playwright-chromium", "firefox": "playwright-firefox", "webkit": "playwright-webkit"}
_BROWSERSTACK_OS = {"windows": ("Windows", "11"), "macos": ("OS X", "Sonoma")}
_TESTMU_BROWSER = {"chrome": "Chrome", "edge": "MicrosoftEdge", "chromium": "pw-chromium", "firefox": "pw-firefox", "webkit": "pw-webkit"}
_TESTMU_OS = {"windows": "Windows 11", "macos": "MacOS Sonoma"}
_SAUCE_OS = {"windows": "Windows 11", "macos": "macOS 13"}


@dataclass(frozen=True)
class WebOptions:
    browser: Browser | None = None  # None: the run's own browser (Chrome for a Chromium run)
    os: OsName = "windows"

    def for_run(self, result: RunResult) -> WebOptions:
        """These options with the browser filled in from the run, checked against the browser the run used."""
        spec = result.automation
        used = (spec.browser_name if spec is not None else None) or "chromium"
        browser = self.browser or _FOR_RUN.get(used, "chrome")
        engine = engine_of(used)
        if engine != "chromium" and ENGINES[browser] != engine:
            raise ExportError(f"run {result.run_id} ran in {engine.capitalize()}: run it on the cloud in {engine} (its test sets {engine} options), not {browser}")
        if engine == "chromium" and ENGINES[browser] != "chromium":
            raise ExportError(f"run {result.run_id} ran in Chromium ({used}): choose chrome, edge or chromium on the cloud, or run the case in {browser} first")
        return WebOptions(browser, self.os)


@dataclass(frozen=True)
class MobileOptions:
    device: str | None = None
    platform_version: str | None = None


@dataclass(frozen=True)
class Bundle:
    """The project's files (path -> text), how the Hercules runner starts it, and what it needs from the environment."""

    cloud: CloudId
    kind: Literal["web", "mobile"]
    mode: Literal["connect", "saucectl", "appium"]
    files: dict[str, str | bytes]
    export: ExportResult
    test_path: str
    env: tuple[EnvVar, ...] = field(default_factory=tuple)
    web: WebOptions | None = None  # a web project's browser and OS, with the browser filled in from the run

    def zip_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, text in sorted(self.files.items()):
                archive.writestr(path, text)
        return buffer.getvalue()


def playwright_version() -> str:
    try:
        return metadata.version("playwright")
    except metadata.PackageNotFoundError:
        return "1.49.0"


def build_label(result: RunResult) -> str:
    return f"hercules-{safe_id(result.case_id)}"


def credential_env(cloud: CloudId) -> tuple[str, str]:
    """The default variables holding the cloud's user name and access key (``mobile.<cloud>`` in the settings)."""
    from testzeus_hercules.kernel.settings import MobileSettings

    config = getattr(MobileSettings(), cloud)
    return config.username_env, config.access_key_env


def web_bundle(
    result: RunResult,
    cloud: CloudId,
    options: WebOptions,
    *,
    credentials: tuple[str, str] | None = None,
    allow_failed: bool = False,
    sauce_region: str = "us-west-1",
    project_dir: Path | None = None,
) -> Bundle:
    """The web project for ``cloud``. ``credentials``: the names of the variables holding the user name and key;
    ``project_dir``: where the run's auth state file (``browser_options.storage_state``) is, to put it in the project."""
    if result.automation is not None and result.automation.app is not None:
        raise ExportError(f"run {result.run_id} tested a native app: run it on a cloud as a native app test, not in a cloud browser")
    options = options.for_run(result)
    names = credentials or credential_env(cloud)
    auth = {**_auth_files(result, project_dir), **_upload_files(result, project_dir)}
    if cloud_info(cloud).web_mode == "saucectl":
        if options.browser == "edge":
            raise ExportError("Sauce Labs runs Playwright tests in Chromium, Chrome, Firefox or WebKit, not Edge; choose chrome or chromium")
        exported = export_run(result, "playwright-ts", allow_failed=allow_failed, target_dir="tests")
        bundle = _sauce_bundle(result, exported, options, names, sauce_region)
        return replace(bundle, files={**bundle.files, **auth}, web=options)
    exported = export_run(result, "playwright-py", allow_failed=allow_failed, target_dir="tests")
    files = {
        **auth,
        exported.filename: exported.code,
        "conftest.py": connect_conftest(cloud, options, names, build_label(result)),
        "requirements.txt": f"pytest>=8\npytest-playwright>=0.5\nplaywright=={playwright_version()}\n",
    }
    if cloud == "browserstack":
        files["browserstack.yml"] = _browserstack_yml(options, names, build_label(result))
    else:
        files["hyperexecute.yaml"] = _hyperexecute_yaml(options, names, build_label(result))
    files["README.md"] = _web_readme(cloud, exported, names) + (_AUTH_NOTE if auth else "")
    return Bundle(cloud, "web", "connect", files, exported, exported.filename, exported.env, web=options)


_AUTH_NOTE = "\n## Signed-in state\n\nThe project holds the auth state file the run started with (cookies and local storage): keep it private, and refresh it when the session expires.\n"


def _auth_files(result: RunResult, project_dir: Path | None) -> dict[str, str]:
    """The run's auth state file, at the same path in the cloud project as in this one."""
    spec = result.automation
    state = spec.browser_options.storage_state if spec is not None and spec.browser_options is not None else None
    if state is None:
        return {}
    if project_dir is None:
        raise ExportError(f"run {result.run_id} starts signed in ({state}): the cloud project needs that file; build it from the project folder")
    root = project_dir.resolve()
    path = (root / state).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ExportError(f"the run's auth state file {state} is not in the project any more; save it again before running on a cloud")
    return {state: path.read_text(encoding="utf-8")}


def _upload_files(result: RunResult, project_dir: Path | None) -> dict[str, str | bytes]:
    """The files the run's upload steps attached, at the same paths in the cloud project as in this one."""
    spec = result.automation
    wanted = sorted({path for step in (spec.steps if spec is not None else []) for action in step.actions if action.kind == "upload" and not uploads_download(action) for path in action.files or []})
    if not wanted:
        return {}
    if project_dir is None:
        raise ExportError(f"run {result.run_id} uploads {', '.join(wanted)}: the cloud project needs those files; build it from the project folder")
    root = project_dir.resolve()
    found: dict[str, str | bytes] = {}
    for relative in wanted:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ExportError(f"the file {relative} that the run uploaded is not in the project any more; add it again before running on a cloud")
        found[relative] = path.read_bytes()
    return found


def connect_conftest(cloud: CloudId, options: WebOptions, names: tuple[str, str], build: str) -> str:
    """A conftest.py that runs this folder's Playwright tests in the cloud's browser."""
    name = cloud_info(cloud).name
    user, key = names
    header = f'''"""Runs this folder's Playwright tests in a {name} browser.

Needs {user} and {key} in the environment. Choose the browser and OS with {BROWSER_ENV}
(chrome, edge, chromium, firefox or webkit) and {OS_ENV} (windows or macos). Set {CONNECT_ENV}=0 to use a local browser
(or the one a cloud runner such as the BrowserStack SDK or HyperExecute provides), or {WS_ENDPOINT_ENV}
to the address of another Playwright server.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from importlib.metadata import version

import pytest

BROWSER = os.environ.get("{BROWSER_ENV}", "{options.browser or "chrome"}")
ENGINE = {ENGINES!r}.get(BROWSER, "chromium")
OS_NAME = os.environ.get("{OS_ENV}", "{options.os}")
BUILD = os.environ.get("{BUILD_ENV}", "{build}")
CONNECT = os.environ.get("{CONNECT_ENV}", "1") != "0"
WS_ENDPOINT = os.environ.get("{WS_ENDPOINT_ENV}")
'''
    if cloud == "browserstack":
        endpoint = f"""

def _endpoint(name: str) -> str:
    os_name, os_version = {_BROWSERSTACK_OS!r}[OS_NAME]
    caps = {{
        "browser": {_BROWSERSTACK_BROWSER!r}[BROWSER],
        "os": os_name,
        "os_version": os_version,
        "name": name,
        "build": BUILD,
        "project": "Hercules",
        "browserstack.username": os.environ["{user}"],
        "browserstack.accessKey": os.environ["{key}"],
        "client.playwrightVersion": version("playwright"),
    }}
    return "wss://cdp.browserstack.com/playwright?caps=" + urllib.parse.quote(json.dumps(caps))


def _status_script(passed: bool, reason: str) -> str:
    arguments = {{"status": "passed" if passed else "failed", "reason": reason[:250]}}
    return "browserstack_executor: " + json.dumps({{"action": "setSessionStatus", "arguments": arguments}})


DETAILS_SCRIPT = "browserstack_executor: " + json.dumps({{"action": "getSessionDetails"}})
"""
    else:
        endpoint = f"""

def _endpoint(name: str) -> str:
    caps = {{
        "browserName": {_TESTMU_BROWSER!r}[BROWSER],
        "browserVersion": "latest",
        "LT:Options": {{
            "platform": {_TESTMU_OS!r}[OS_NAME],
            "build": BUILD,
            "name": name,
            "user": os.environ["{user}"],
            "accessKey": os.environ["{key}"],
            "network": True,
            "video": True,
            "console": True,
            "playwrightClientVersion": version("playwright"),
        }},
    }}
    return "wss://cdp.lambdatest.com/playwright?capabilities=" + urllib.parse.quote(json.dumps(caps))


def _status_script(passed: bool, reason: str) -> str:
    arguments = {{"status": "passed" if passed else "failed", "remark": reason[:250]}}
    return "lambdatest_action: " + json.dumps({{"action": "setTestStatus", "arguments": arguments}})


DETAILS_SCRIPT = "lambdatest_action: " + json.dumps({{"action": "getTestDetails"}})
"""
    fixtures = f'''

@pytest.fixture(scope="session")
def browser(playwright, launch_browser):
    """The cloud's browser (one session per test run), or a local one when {CONNECT_ENV}=0."""
    if not CONNECT:
        browser = launch_browser()
        yield browser
        browser.close()
        return
    browser = getattr(playwright, ENGINE).connect(WS_ENDPOINT or _endpoint(BUILD))
    yield browser
    browser.close()


def pytest_configure(config):
    """Without --browser, run the tests in the cloud browser's engine (a test marked only_browser needs it)."""
    if not getattr(config.option, "browser", None):
        config.option.browser = [ENGINE]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, "rep_" + report.when, report)


@pytest.fixture(autouse=True)
def _cloud_session(request):
    """Prints the session's link, then marks the session passed or failed in {name}."""
    page = request.getfixturevalue("page") if CONNECT and "page" in request.fixturenames else None
    if page is not None:
        try:
            details = str(page.evaluate("_ => {{}}", DETAILS_SCRIPT))
            found = re.search(r"https://[^\\s\\"']+", details)
            if found:
                print("\\n{SESSION_MARKER}" + found.group(0), flush=True)
        except Exception:  # the link is a convenience; never fail the test for it
            pass
    yield
    if page is None:
        return
    report = getattr(request.node, "rep_call", None) or getattr(request.node, "rep_setup", None)
    passed = bool(report is not None and report.passed)
    reason = "" if passed else str(getattr(report, "longrepr", "") or "failed").splitlines()[-1]
    try:
        page.evaluate("_ => {{}}", _status_script(passed, reason))
    except Exception:
        pass
'''
    return header + endpoint + fixtures


def _browserstack_yml(options: WebOptions, names: tuple[str, str], build: str) -> str:
    os_name, os_version = _BROWSERSTACK_OS[options.os]
    browser = _BROWSERSTACK_BROWSER[options.browser or "chrome"]
    return (
        "# BrowserStack SDK configuration, for CI: pip install browserstack-sdk && browserstack-sdk pytest tests\n"
        f"# The SDK provides the browser itself: run it with {CONNECT_ENV}=0.\n"
        f"userName: ${{{names[0]}}}\n"
        f"accessKey: ${{{names[1]}}}\n"
        "framework: pytest\n"
        "projectName: Hercules\n"
        f"buildName: {build}\n"
        "platforms:\n"
        f"  - os: {os_name}\n"
        f"    osVersion: {os_version}\n"
        f"    browserName: {browser}\n"
        "    browserVersion: latest\n"
        "parallelsPerPlatform: 1\n"
        "browserstackLocal: false\n"
    )


def _hyperexecute_yaml(options: WebOptions, names: tuple[str, str], build: str) -> str:
    runson = "win" if options.os == "windows" else "mac"
    return (
        f"# TestMu AI HyperExecute configuration, for CI: ./hyperexecute --user ${names[0]} --key ${names[1]} --config hyperexecute.yaml\n"
        "# HyperExecute runs the tests on its own machines with a local browser.\n"
        'version: "0.1"\n'
        f"runson: {runson}\n"
        "concurrency: 1\n"
        "autosplit: true\n"
        "globalTimeout: 90\n"
        "testSuiteTimeout: 90\n"
        "testSuiteStep: 90\n"
        "retryOnFailure: false\n"
        "env:\n"
        f'  {CONNECT_ENV}: "0"\n'
        "pre:\n"
        "  - pip install -r requirements.txt\n"
        f"  - python -m playwright install {ENGINES[options.browser or 'chrome']}\n"
        "testDiscovery:\n"
        "  type: raw\n"
        "  mode: dynamic\n"
        "  command: grep -rl 'def test_' tests\n"
        "testRunnerCommand: python -m pytest $test -v\n"
        f"jobLabel: [hercules, {build}]\n"
    )


def _env_lines(env: tuple[EnvVar, ...]) -> list[str]:
    return [f"- `{item.name}`{' (required)' if item.required else ''}: {item.purpose}" for item in env]


def _web_readme(cloud: CloudId, exported: ExportResult, names: tuple[str, str]) -> str:
    info = cloud_info(cloud)
    ci = (
        "pip install browserstack-sdk\nHERCULES_CLOUD_CONNECT=0 browserstack-sdk pytest tests"
        if cloud == "browserstack"
        else f'./hyperexecute --user "${names[0]}" --key "${names[1]}" --config hyperexecute.yaml'
    )
    lines = [
        f"# {exported.case_id} on {info.name}",
        "",
        f"Exported by Hercules from run {exported.run_id}. `conftest.py` runs the test in a {info.name} browser.",
        "",
        "## Run it",
        "",
        "```bash",
        "pip install -r requirements.txt",
        f"export {names[0]}=... {names[1]}=...",
        "pytest tests",
        "```",
        "",
        f"Choose the browser with `{BROWSER_ENV}` (chrome, edge, chromium, firefox, webkit) and the OS with `{OS_ENV}` (windows, macos).",
        "",
        f"## With {info.ci_runner}",
        "",
        f"`{info.ci_config}` is a starting point for {info.ci_runner}:",
        "",
        "```bash",
        ci,
        "```",
        "",
        "## Environment",
        "",
        f"- `{names[0]}`, `{names[1]}` (required): your {info.name} user name and access key",
        *_env_lines(exported.env),
        "",
    ]
    return "\n".join(lines)


def _sauce_bundle(result: RunResult, exported: ExportResult, options: WebOptions, names: tuple[str, str], region: str) -> Bundle:
    browser = {"chromium": "chromium", "firefox": "firefox", "webkit": "webkit"}.get(options.browser or "chrome", "chrome")
    build = build_label(result)
    # Only the required variables: saucectl would send an unset optional one as an empty value, overriding the test's default.
    env_names = [item.name for item in exported.env if item.required]
    config = [
        f"# saucectl configuration: saucectl run (needs {names[0]} and {names[1]})",
        "apiVersion: v1alpha",
        "kind: playwright",
        "sauce:",
        f"  region: {region}",
        "  concurrency: 1",
        "  metadata:",
        f"    build: {build}",
        "    tags: [hercules]",
        "playwright:",
        "  version: package.json",
        "rootDir: ./",
    ]
    if env_names:
        config += ["env:", *[f"  {name}: ${name}" for name in env_names]]
    optional = [item.name for item in exported.env if not item.required]
    if optional:
        config += [f"# To override {', '.join(optional)} on Sauce Labs, add {'it' if len(optional) == 1 else 'them'} under env: (NAME: $NAME)."]
    config += [
        "suites:",
        f"  - name: {json.dumps(exported.case_id)}",
        f"    platformName: {_SAUCE_OS[options.os]}",
        "    testMatch: ['.*\\.spec\\.ts']",
        "    params:",
        f"      browserName: {browser}",
        "",
    ]
    package = {
        "name": re.sub(r"[^a-z0-9-]+", "-", build.lower()).strip("-") or "hercules-test",
        "private": True,
        "scripts": {"test": "playwright test"},
        "devDependencies": {"@playwright/test": playwright_version()},
    }
    playwright_config = (
        'import { defineConfig } from "@playwright/test";\n\n'
        "export default defineConfig({\n"
        '  testDir: "./tests",\n'
        "  timeout: 120_000,\n"
        "  retries: 0,\n"
        '  use: { trace: "retain-on-failure", video: "on" },\n'
        "});\n"
    )
    readme = "\n".join(
        [
            f"# {exported.case_id} on Sauce Labs",
            "",
            f"Exported by Hercules from run {exported.run_id}. saucectl uploads this folder and runs the test in Sauce Labs.",
            "",
            "```bash",
            "npm i -g saucectl   # once",
            f"export {names[0]}=... {names[1]}=...",
            "saucectl run",
            "```",
            "",
            f"Run it locally with `npm install && npx playwright install {ENGINES[options.browser or 'chrome']} && npx playwright test`.",
            "",
            "## Environment",
            "",
            f"- `{names[0]}`, `{names[1]}` (required): your Sauce Labs user name and access key",
            *_env_lines(exported.env),
            "",
        ]
    )
    files = {
        exported.filename: exported.code,
        "package.json": json.dumps(package, indent=2) + "\n",
        "playwright.config.ts": playwright_config,
        ".sauce/config.yml": "\n".join(config),
        ".sauceignore": "node_modules/\n",
        "README.md": readme,
    }
    return Bundle("saucelabs", "web", "saucectl", files, exported, exported.filename, exported.env)


def mobile_bundle(result: RunResult, cloud: CloudId, options: MobileOptions, *, allow_failed: bool = False) -> Bundle:
    """The native app project for ``cloud``: the Appium export targeting it."""
    from testzeus_hercules.clouds.mobile import appium_export_for_cloud

    exported = appium_export_for_cloud(result, cloud, device=options.device, platform_version=options.platform_version, allow_failed=allow_failed)
    info = cloud_info(cloud)
    readme = "\n".join(
        [
            f"# {exported.case_id} on {info.name} (native app)",
            "",
            f"Exported by Hercules from run {exported.run_id}. The test drives the app on a {info.name} device through its Appium hub.",
            "",
            "```bash",
            "pip install -r requirements.txt",
            "pytest tests",
            "```",
            "",
            "## Environment",
            "",
            *_env_lines(exported.env),
            "",
        ]
    )
    files = {exported.filename: exported.code, "requirements.txt": "pytest>=8\nAppium-Python-Client>=4\n", "README.md": readme}
    return Bundle(cloud, "mobile", "appium", files, exported, exported.filename, exported.env)
