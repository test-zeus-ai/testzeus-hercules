"""Native app runs on a device cloud: the Appium export for a chosen cloud, and the app upload it needs.

The cloud runner exports a finished native app run as an Appium (pytest) test for one of the clouds
(:func:`appium_export_for_cloud`, whatever cloud the run used, or a local server), uploads the app the run used
(:func:`app_file_of`, :func:`upload_app_to_cloud`) and runs the test with the uploaded app's id in its environment.

What the exported test reads from the environment (only the credentials are required besides the app):

* ``APPIUM_APP`` (:data:`APP_ENV`): the app to install, a file or the cloud's app id (``bs://...`` on BrowserStack,
  ``storage:<id>`` on Sauce Labs, ``lt://...`` on TestMu AI). Required unless the run already used that cloud's id;
  the runner sets it to the id :func:`upload_app_to_cloud` returns.
* ``APPIUM_DEVICE_NAME`` / ``APPIUM_PLATFORM_VERSION`` (:data:`DEVICE_NAME_ENV`, :data:`PLATFORM_VERSION_ENV`):
  optional overrides of the device and OS version written into the test (the ``device`` / ``platform_version``
  given to :func:`appium_export_for_cloud`, else the run's).
* ``APPIUM_URL`` (:data:`APPIUM_URL_ENV`): optional, the hub (the cloud's default hub is written into the test).
* The cloud's credentials: ``BROWSERSTACK_USERNAME`` / ``BROWSERSTACK_ACCESS_KEY``, ``SAUCE_USERNAME`` /
  ``SAUCE_ACCESS_KEY`` or ``LT_USERNAME`` / ``LT_ACCESS_KEY`` (the names the run read them from when it ran on that
  cloud). They are never written into the code.

:data:`RUNNER_SET_ENV` names the variables the runner itself supplies for a cloud run (besides credentials).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import httpx

from testzeus_hercules.cases.models import APP_SUFFIXES, CLOUD_APP_PATTERN, RunResult
from testzeus_hercules.clouds.credentials import CloudCredentials
from testzeus_hercules.clouds.registry import CLOUD_IDS, CloudId
from testzeus_hercules.export import export_run
from testzeus_hercules.export.appium import APP_ENV, APPIUM_URL_ENV, for_cloud
from testzeus_hercules.export.common import ExportError, ExportResult
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.mobile.cloud import CloudError, upload_file
from testzeus_hercules.mobile.target import TargetError, cloud_endpoints
from testzeus_hercules.mobile.webdriver import ClientFactory

__all__ = [
    "APPIUM_URL_ENV",
    "APP_ENV",
    "DEVICE_NAME_ENV",
    "PLATFORM_VERSION_ENV",
    "RUNNER_SET_ENV",
    "app_file_of",
    "appium_export_for_cloud",
    "upload_app_to_cloud",
]

DEVICE_NAME_ENV = "APPIUM_DEVICE_NAME"
PLATFORM_VERSION_ENV = "APPIUM_PLATFORM_VERSION"
# What the runner supplies for a cloud run, besides the cloud's credentials: the uploaded app's id.
RUNNER_SET_ENV: frozenset[str] = frozenset({APP_ENV})
_CLOUD_APP = re.compile(CLOUD_APP_PATTERN)


def _check_cloud(cloud: str) -> CloudId:
    if cloud not in CLOUD_IDS:
        raise ValueError(f"unknown cloud {cloud!r}; use one of {', '.join(CLOUD_IDS)}")
    return cloud  # type: ignore[return-value]


def appium_export_for_cloud(result: RunResult, cloud: CloudId, *, device: str | None = None, platform_version: str | None = None, allow_failed: bool = False) -> ExportResult:
    """The run as an Appium (pytest) test for ``cloud`` (``tests/test_<case>.py``), whichever cloud or server the run
    used; ``device`` / ``platform_version`` replace the run's. :class:`ExportError` for a web run, a run without an
    automation record, or one that did not pass (unless ``allow_failed``)."""
    _check_cloud(cloud)
    spec = result.automation
    if spec is None:
        raise ExportError(f"run {result.run_id} has no automation record (it was made by an older Hercules, or stopped before its first step); run the case again")
    if spec.app is None:
        raise ExportError(f"run {result.run_id} tested a web app in a browser: only a native app run can be exported as Appium code for a device cloud")
    retargeted = result.model_copy(update={"automation": for_cloud(spec, cloud, device=device, platform_version=platform_version)})
    return export_run(retargeted, "appium-py", allow_failed=allow_failed, target_dir="tests")


def app_file_of(result: RunResult, project_dir: Path) -> Path | None:
    """The app file (or iOS ``.app`` folder) the run used, when it is inside the project; None for an app already in a
    cloud, an app installed on the device, or a file that is gone or outside the project."""
    app = result.automation.app if result.automation is not None else None
    reference = app.get("app") if app else None
    if not isinstance(reference, str) or not reference or _CLOUD_APP.match(reference):
        return None
    if not reference.replace("\\", "/").rstrip("/").lower().endswith(APP_SUFFIXES):
        return None
    root = Path(os.path.realpath(project_dir))
    path = Path(os.path.realpath(root / reference))
    if not path.is_relative_to(root) or path == root:
        return None
    if path.is_file() or (path.is_dir() and path.suffix.lower() == ".app"):
        return path
    return None


async def upload_app_to_cloud(app_file: Path, cloud: CloudId, credentials: CloudCredentials, settings: Settings, *, client_factory: ClientFactory = httpx.AsyncClient) -> str:
    """Upload ``app_file`` (an iOS ``.app`` folder is zipped) to ``cloud``'s app storage (``mobile.<cloud>.api_url``,
    else the cloud's API); returns the id the cloud knows it by (``bs://...``, ``storage:<id>``, ``lt://...``).
    :class:`CloudError` when it fails; no message holds a credential."""
    _check_cloud(cloud)
    if credentials.cloud != cloud:
        raise CloudError(f"the credentials given are for {credentials.cloud}, not {cloud}")
    if credentials.auth is None:
        raise CloudError(f"the {cloud} credentials are missing: set {credentials.username_env} and {credentials.access_key_env}, or save them from the UI's Integrations screen")
    if not (app_file.is_file() or (app_file.is_dir() and app_file.suffix.lower() == ".app")):
        raise CloudError(f"the app file {app_file.name!r} was not found")
    try:
        _, api, _ = cloud_endpoints(cloud, settings, credentials.region)
    except TargetError as exc:
        raise CloudError(str(exc)) from None
    return await upload_file(app_file, cloud, api, credentials.auth, client_factory)
