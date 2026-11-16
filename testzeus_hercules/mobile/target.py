"""Where a native app case runs: the Appium server or device cloud hub, its credentials and the session's capabilities.

:func:`resolve_target` turns a case's :class:`~testzeus_hercules.cases.models.MobileApp` and the project's
``mobile`` settings into a :class:`DeviceTarget`, and refuses what the run may not do:

* an app file outside the project, or one that does not exist;
* a remote server under the ``local`` egress policy: a device cloud is always remote, and an Appium server
  counts as local only on this machine (loopback) or on a host listed in ``egress.local_hosts``;
* a device cloud whose credentials are missing: they are read from the environment variables the settings
  name (``mobile.browserstack.username_env``, ...), else from ``<hercules home>/clouds.env`` (saved from the UI's
  Integrations screen; see :mod:`testzeus_hercules.clouds.credentials`), never from the case.

:func:`capabilities` builds the W3C ``alwaysMatch`` capabilities: ``appium:``-prefixed for a local server and
Sauce Labs, ``bstack:options`` for BrowserStack, ``lt:options`` for TestMu AI (formerly LambdaTest).
"""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from testzeus_hercules.act.files import is_secret_file
from testzeus_hercules.cases.models import CLOUD_APP_PATTERN, MobileApp
from testzeus_hercules.clouds.credentials import find_credentials
from testzeus_hercules.kernel.settings import DeviceCloudSettings, Settings
from testzeus_hercules.llm.providers import is_local_url

BROWSERSTACK_HUB = "https://hub-cloud.browserstack.com/wd/hub"
BROWSERSTACK_API = "https://api-cloud.browserstack.com/app-automate"
# TestMu AI (formerly LambdaTest) real devices: the Appium hub and the app upload API (hosts are still lambdatest.com).
TESTMU_HUB = "https://mobile-hub.lambdatest.com/wd/hub"
TESTMU_API = "https://manual-api.lambdatest.com"
# Sauce Labs data centers: the region becomes part of host names that receive the account's credentials, so only a
# known data center is used.
SAUCELABS_REGIONS = frozenset({"us-west-1", "us-east-4", "eu-central-1"})
_CLOUD_APP = re.compile(CLOUD_APP_PATTERN)


class TargetError(ValueError):
    """The case's device cannot be used as configured; the message is safe to show (no secrets)."""


@dataclass(frozen=True)
class DeviceTarget:
    """A resolved device: where to connect, how to log in, and what to ask for."""

    app: MobileApp
    hub_url: str
    provider: str  # local | browserstack | saucelabs | testmu
    is_local: bool
    auth: tuple[str, str] | None = field(default=None, repr=False)
    api_url: str | None = None
    # The app file inside the project (absolute), to pass to a local server or upload to a device cloud.
    app_file: Path | None = None
    region: str | None = None
    # The environment variables the credentials came from (their names only), for the exported test to read.
    credential_env: tuple[str, str] | None = None

    @property
    def label(self) -> str:
        where = "a local Appium server" if self.provider == "local" and self.is_local else self.provider if self.provider != "local" else "an Appium server"
        device = self.app.device or self.app.udid or "the first available device"
        return f"{self.app.platform} app {self.app.app_id} on {device} ({where})"


def resolve_target(app: MobileApp, settings: Settings, env: Mapping[str, str], project_dir: Path) -> DeviceTarget:
    """The device target of ``app`` under the project's settings; :class:`TargetError` when the run may not use it."""
    policy = settings.egress.policy
    local_hosts = settings.egress.local_hosts
    app_file = _app_file(app, project_dir)
    if app.cloud is None:
        hub = (app.appium_url or settings.mobile.appium_url).rstrip("/")
        local = is_local_url(hub, local_hosts)
        if policy == "local" and not local:
            raise TargetError(
                f"egress policy 'local' forbids the remote Appium server {hub}: run Appium on this machine, list its host in "
                "egress.local_hosts, or allow remote endpoints with egress.policy: hybrid-text (or hybrid)"
            )
        return DeviceTarget(app=app, hub_url=hub, provider="local", is_local=local, app_file=app_file)
    if policy == "local":
        raise TargetError(f"egress policy 'local' forbids the device cloud {app.cloud}: it is a remote service. Allow it with egress.policy: hybrid-text (or hybrid), or use a local Appium server")
    cloud: DeviceCloudSettings = getattr(settings.mobile, app.cloud)
    found = find_credentials(app.cloud, settings, env)
    if found.auth is None:
        missing = [name for name, value in ((cloud.username_env, env.get(cloud.username_env, "").strip()), (cloud.access_key_env, env.get(cloud.access_key_env, "").strip())) if not value]
        problem = f" ({found.problem})" if found.problem else ""
        raise TargetError(
            f"the {app.cloud} credentials are missing: set the environment variable(s) {', '.join(missing or (cloud.username_env, cloud.access_key_env))} "
            f"(named in mobile.{app.cloud} of the project's config), or save them from the UI's Integrations screen (kept in {settings.home_dir / 'clouds.env'}){problem}"
        )
    hub, api, region = cloud_endpoints(app.cloud, settings)
    return DeviceTarget(
        app=app,
        hub_url=hub,
        provider=app.cloud,
        is_local=is_local_url(hub, local_hosts),
        auth=found.auth,
        api_url=api,
        app_file=app_file,
        region=region,
        credential_env=(cloud.username_env, cloud.access_key_env),
    )


def cloud_endpoints(provider: str, settings: Settings) -> tuple[str, str, str | None]:
    """The device cloud's Appium hub, REST API (app upload, session status) and region: the configured ones
    (``mobile.<provider>.hub_url`` / ``api_url`` / ``region``), else the cloud's defaults. :class:`TargetError` for an
    unknown Sauce Labs region (it becomes part of host names that receive the credentials)."""
    cloud: DeviceCloudSettings = getattr(settings.mobile, provider)
    if provider == "browserstack":
        return (cloud.hub_url or BROWSERSTACK_HUB).rstrip("/"), (cloud.api_url or BROWSERSTACK_API).rstrip("/"), None
    if provider == "testmu":
        return (cloud.hub_url or TESTMU_HUB).rstrip("/"), (cloud.api_url or TESTMU_API).rstrip("/"), None
    region = (cloud.region or "us-west-1").lower()
    if region not in SAUCELABS_REGIONS:
        raise TargetError(f"mobile.saucelabs.region must be one of {', '.join(sorted(SAUCELABS_REGIONS))}")
    hub = cloud.hub_url or f"https://ondemand.{region}.saucelabs.com:443/wd/hub"
    return hub.rstrip("/"), (cloud.api_url or f"https://api.{region}.saucelabs.com").rstrip("/"), region


def _app_file(app: MobileApp, project_dir: Path) -> Path | None:
    if app.app is None or _CLOUD_APP.match(app.app):
        return None
    root = Path(os.path.realpath(project_dir))
    # Resolved with its links: an app file (or an iOS .app folder) must really be inside the project.
    path = Path(os.path.realpath(root / app.app))
    if not path.is_relative_to(root) or path == root:
        raise TargetError(f"the app {app.app!r} resolves outside the project; keep the app inside the project folder")
    if is_secret_file(path):
        raise TargetError(f"the app {app.app!r} looks like a secret or state file and cannot be used")
    if not (path.is_file() or (path.is_dir() and path.suffix.lower() == ".app")):
        raise TargetError(f"the app file {app.app!r} was not found in the project")
    return path


def without_userinfo(url: str) -> tuple[str, bool]:
    """``url`` without a user name and password in it, and whether it had them."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url, False
    if "@" not in parts.netloc:
        return url, False
    return urllib.parse.urlunsplit(parts._replace(netloc=parts.netloc.rsplit("@", 1)[1])), True


def capabilities(target: DeviceTarget, *, app_ref: str | None = None, run_name: str = "") -> dict[str, Any]:
    """The session's W3C capabilities. ``app_ref``: the app as the server knows it (an uploaded cloud app's id)."""
    app = target.app
    caps: dict[str, Any] = {
        "platformName": "Android" if app.platform == "android" else "iOS",
        "appium:automationName": "UiAutomator2" if app.platform == "android" else "XCUITest",
        "appium:noReset": app.no_reset,
        "appium:newCommandTimeout": 300,
    }
    reference = app_ref or (str(target.app_file) if target.app_file is not None else app.app)
    if reference:
        caps["appium:app"] = reference
    if app.package:
        caps["appium:appPackage"] = app.package
    if app.activity:
        caps["appium:appActivity"] = app.activity
    if app.bundle_id:
        caps["appium:bundleId"] = app.bundle_id
    if app.udid:
        caps["appium:udid"] = app.udid
    if app.platform == "android" and target.provider == "local":
        # A cold emulator can take longer than UiAutomator2's 30 s default to start its server.
        caps["appium:uiautomator2ServerLaunchTimeout"] = 90_000
    if app.platform == "ios" and target.provider == "local":
        # A simulator needs no window: Appium otherwise opens the Simulator app, which recent Xcode keeps elsewhere.
        caps["appium:isHeadless"] = True
        # WebDriverAgent can take longer than XCUITest's 60 s default to start on a simulator that just booted.
        caps["appium:wdaLaunchTimeout"] = 120_000
    if target.provider == "browserstack":
        options: dict[str, Any] = {"projectName": "Hercules", "buildName": "hercules", "sessionName": run_name or "hercules"}
        if app.device:
            options["deviceName"] = app.device
        if app.platform_version:
            options["osVersion"] = app.platform_version
        caps["bstack:options"] = options
    elif target.provider == "testmu":
        lt_options: dict[str, Any] = {"platformName": caps["platformName"], "isRealMobile": True, "build": "hercules", "name": run_name or "hercules", "w3c": True}
        if app.device:
            lt_options["deviceName"] = app.device
        if app.platform_version:
            lt_options["platformVersion"] = app.platform_version
        caps["lt:options"] = lt_options
    else:
        if app.device:
            caps["appium:deviceName"] = app.device
        if app.platform_version:
            caps["appium:platformVersion"] = app.platform_version
        if target.provider == "saucelabs":
            caps["sauce:options"] = {"name": run_name or "hercules", "build": "hercules"}
    for key, value in app.capabilities.items():
        if isinstance(value, dict) and isinstance(caps.get(key), dict):
            caps[key] = {**caps[key], **value}
        else:
            caps[key] = value
    return caps
