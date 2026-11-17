"""The daemon's cloud API: connect BrowserStack, Sauce Labs and TestMu AI, and run exported tests on them.

* ``GET /api/clouds``: each cloud, whether it is connected (and from where), and what it can run here.
* ``PUT /api/clouds/{cloud}``: save a user name and access key (and Sauce Labs region) to
  ``<hercules home>/clouds.env`` (mode 0600) and check them. ``PUT /api/clouds/saucelabs/region`` saves only the
  region and checks the current keys there. ``POST /api/clouds/{cloud}/check`` checks the current ones; ``DELETE``
  removes the saved ones. Access keys are accepted, never returned.
* ``POST /api/runs/{run_id}/cloud-runs``: run a passed run's exported test on a cloud;
  ``GET /api/runs/{run_id}/cloud-runs`` lists them, ``GET /api/cloud-runs/{id}`` is one with its output,
  ``POST /api/cloud-runs/{id}/cancel`` stops it.
* ``GET /api/runs/{run_id}/cloud-bundle``: the same project as a ZIP, for CI.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from testzeus_hercules.clouds.bundle import (
    BROWSERS,
    OS_NAMES,
    Browser,
    MobileOptions,
    OsName,
    WebOptions,
)
from testzeus_hercules.clouds.check import check_credentials
from testzeus_hercules.clouds.credentials import (
    SAUCE_REGION_ENV,
    SAUCELABS_REGIONS,
    CredentialError,
    clouds_file,
    find_credentials,
    placeholder_problem,
    remove_credentials,
    save_credentials,
    save_region,
)
from testzeus_hercules.clouds.registry import CLOUD_IDS, CLOUDS, CloudId
from testzeus_hercules.clouds.runner import CloudRunError, CloudRuns, requirements

if TYPE_CHECKING:
    from testzeus_hercules.daemon.app import Daemon

# The HTTP client the connection check uses (a seam for tests).
check_client_factory: Any = None

_DEVICE = r"^[A-Za-z0-9 ._()+-]{1,80}$"
_REGION = r"^[a-z0-9-]{1,40}$"
_VERSION = r"^[A-Za-z0-9._-]{1,20}$"


class CloudKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=512)
    access_key: str = Field(min_length=1, max_length=512)
    region: str | None = Field(default=None, pattern=_REGION)


class CloudRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    region: str = Field(pattern=_REGION)


class CloudRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cloud: CloudId
    browser: Browser | None = None  # None: the run's own browser (Chrome for a Chromium run)
    os: OsName = "windows"
    device: str | None = Field(default=None, pattern=_DEVICE)
    platform_version: str | None = Field(default=None, pattern=_VERSION)
    allow_failed: bool = False


def _api_error(status: int, message: str) -> Exception:
    from testzeus_hercules.daemon.app import ApiError

    return ApiError(status, message)


def _cloud(request: Request) -> CloudId:
    cloud = request.path_params["cloud"]
    if cloud not in CLOUD_IDS:
        raise _api_error(404, f"unknown cloud {cloud!r}; use one of {', '.join(CLOUD_IDS)}")
    return cloud  # type: ignore[no-any-return]


def cloud_view(daemon: Daemon, cloud: CloudId) -> dict[str, Any]:
    settings = daemon.config.settings
    info = CLOUDS[cloud]
    found = find_credentials(cloud, settings, daemon.config.env)
    return {
        "id": cloud,
        "name": info.name,
        "connected": found.found,
        "source": found.source,
        "username": found.username,
        "username_env": found.username_env,
        "access_key_env": found.access_key_env,
        "region": found.region,
        "region_source": found.region_source,
        "regions": list(SAUCELABS_REGIONS) if cloud == "saucelabs" else [],
        "problem": placeholder_problem(found) or found.problem,
        "web_mode": info.web_mode,
        "web_ready": requirements("web", info.web_mode),
        "mobile_ready": requirements("mobile", "appium"),
        "website_url": info.website_url,
        "dashboard_url": info.dashboard_url,
        "keys_url": info.keys_url,
        "signup_url": info.signup_url,
        "ci_runner": info.ci_runner,
        "ci_config": info.ci_config,
    }


async def list_clouds(request: Request, daemon: Daemon) -> Response:
    settings = daemon.config.settings
    return JSONResponse(
        {
            "clouds": [cloud_view(daemon, cloud) for cloud in CLOUD_IDS],
            "keys_file": str(clouds_file(settings.home_dir)),
            "egress_allows": settings.egress.policy != "local",
            "browsers": list(BROWSERS),
            "os_names": list(OS_NAMES),
        }
    )


async def save_cloud(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _body, _parse

    cloud = _cloud(request)
    keys: CloudKeys = _parse(CloudKeys, await _body(request))
    settings = daemon.config.settings
    try:
        await asyncio.to_thread(save_credentials, cloud, settings, keys.username, keys.access_key, keys.region)
    except CredentialError as exc:
        raise _api_error(422, str(exc)) from None
    view = cloud_view(daemon, cloud)
    found = find_credentials(cloud, settings, daemon.config.env)
    check = await _check(cloud, found, settings)
    note = None
    if found.source == "env":
        note = f"saved, but the environment variables {found.username_env} and {found.access_key_env} are set and win over the saved keys"
    return JSONResponse({**view, "check": {"ok": check.ok, "message": check.message}, "note": note})


async def save_cloud_region(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _body, _parse

    cloud = _cloud(request)
    body: CloudRegion = _parse(CloudRegion, await _body(request))
    settings = daemon.config.settings
    try:
        await asyncio.to_thread(save_region, cloud, settings, body.region)
    except CredentialError as exc:
        raise _api_error(422, str(exc)) from None
    view = cloud_view(daemon, cloud)
    found = find_credentials(cloud, settings, daemon.config.env)
    check = await _check(cloud, found, settings) if found.found else None
    note = None
    if found.region_source == "env":
        note = f"saved, but the environment variable {SAUCE_REGION_ENV} is set to {found.region} and wins over the saved region"
    return JSONResponse({**view, "check": None if check is None else {"ok": check.ok, "message": check.message}, "note": note})


async def check_cloud(request: Request, daemon: Daemon) -> Response:
    cloud = _cloud(request)
    found = find_credentials(cloud, daemon.config.settings, daemon.config.env)
    check = await _check(cloud, found, daemon.config.settings)
    return JSONResponse({"ok": check.ok, "message": check.message})


async def remove_cloud(request: Request, daemon: Daemon) -> Response:
    cloud = _cloud(request)
    try:
        removed = await asyncio.to_thread(remove_credentials, cloud, daemon.config.settings)
    except CredentialError as exc:
        raise _api_error(409, str(exc)) from None
    view = cloud_view(daemon, cloud)
    note = None
    if view["source"] == "env":
        note = f"the environment variables {view['username_env']} and {view['access_key_env']} are still set; unset them and restart the daemon to disconnect"
    return JSONResponse({**view, "removed": removed, "note": note})


async def _check(cloud: CloudId, found: Any, settings: Any) -> Any:
    if check_client_factory is None:
        return await check_credentials(cloud, found, settings)
    return await check_credentials(cloud, found, settings, check_client_factory)


def runs(daemon: Daemon) -> CloudRuns:
    if daemon.cloud_runs is None:
        raise _api_error(503, "the daemon is still starting; try again")
    return daemon.cloud_runs


def _finished_result(daemon: Daemon, run_id: str) -> Any:
    from testzeus_hercules.daemon.app import _record
    from testzeus_hercules.daemon.manager import read_result
    from testzeus_hercules.daemon.store import FINAL_STATUSES

    record = _record(daemon, run_id)
    if record["status"] not in FINAL_STATUSES:
        raise _api_error(409, f"run {run_id} has not finished ({record['status']}); run it on a cloud once it has passed")
    result = read_result(daemon.config.paths.runs_dir / run_id)
    if result is None:
        raise _api_error(409, f"run {run_id} has no result to export")
    return result


def record_view(daemon: Daemon, record: Any, *, log: str | None = None) -> dict[str, Any]:
    view = record.to_dict()
    view.pop("owner_pid", None)
    view["cloud_name"] = CLOUDS[record.cloud].name
    if log is not None:
        view["log"] = log
    return view


async def create_cloud_run(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _body, _parse

    run_id = request.path_params["run_id"]
    body: CloudRunCreate = _parse(CloudRunCreate, await _body(request))
    result = _finished_result(daemon, run_id)
    if result.status != "passed" and not body.allow_failed:
        raise _api_error(409, f"run {run_id} did not pass ({result.status}); only passed runs run on a cloud")
    cloud_runs = runs(daemon)
    web = WebOptions(body.browser, body.os)
    try:
        prepared = await asyncio.to_thread(cloud_runs.prepare, result, body.cloud, web, MobileOptions(body.device, body.platform_version))
    except CloudRunError as exc:
        raise _api_error(409, str(exc)) from None
    cloud_runs.launch(*prepared, web)
    return JSONResponse(record_view(daemon, prepared[0]), status_code=202)


async def list_cloud_runs(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _record

    run_id = request.path_params["run_id"]
    _record(daemon, run_id)
    return JSONResponse({"cloud_runs": [record_view(daemon, record) for record in runs(daemon).records(run_id)]})


async def get_cloud_run(request: Request, daemon: Daemon) -> Response:
    record = runs(daemon).get(request.path_params["cloud_run_id"])
    if record is None:
        raise _api_error(404, "no such cloud run")
    log = await asyncio.to_thread(runs(daemon).log_tail, record.id)
    return JSONResponse(record_view(daemon, record, log=log))


async def cancel_cloud_run(request: Request, daemon: Daemon) -> Response:
    record = await runs(daemon).cancel(request.path_params["cloud_run_id"])
    if record is None:
        raise _api_error(404, "no such cloud run")
    return JSONResponse(record_view(daemon, record))


async def cloud_bundle(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _parse

    run_id = request.path_params["run_id"]
    query = {key: value for key, value in request.query_params.items() if key in CloudRunCreate.model_fields}
    if "allow_failed" in query:
        query["allow_failed"] = query["allow_failed"].lower() in ("1", "true", "yes")  # type: ignore[assignment]
    body: CloudRunCreate = _parse(CloudRunCreate, query)
    result = _finished_result(daemon, run_id)
    try:
        bundle = await asyncio.to_thread(runs(daemon).bundle, result, body.cloud, WebOptions(body.browser, body.os), MobileOptions(body.device, body.platform_version), allow_failed=body.allow_failed)
    except CloudRunError as exc:
        raise _api_error(409, str(exc)) from None
    name = f"{bundle.export.case_id}-{body.cloud}.zip"
    return Response(bundle.zip_bytes(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
