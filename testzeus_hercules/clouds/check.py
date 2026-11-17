"""Check that a cloud accepts its credentials: one read-only REST call with HTTP Basic authentication.

* BrowserStack: ``GET https://api.browserstack.com/automate/plan.json``
* Sauce Labs: ``GET https://api.<region>.saucelabs.com/rest/v1/users/<username>``; when the region refuses them, the
  other Sauce Labs regions are asked too, to name the one that accepts them
* TestMu AI: ``GET https://api.lambdatest.com/automation/api/v1/builds?limit=1``

The answer says only whether the cloud accepted the credentials; it never contains them.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from testzeus_hercules.clouds.credentials import (
    SAUCELABS_REGIONS,
    CloudCredentials,
    sauce_region,
)
from testzeus_hercules.clouds.registry import CloudId, cloud_info
from testzeus_hercules.kernel.settings import Settings

CHECK_TIMEOUT_S = 15.0

ClientFactory = Callable[..., Any]


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str


def _sauce_url(region: str, credentials: CloudCredentials) -> str:
    return f"https://api.{region}.saucelabs.com/rest/v1/users/{urllib.parse.quote(credentials.username or '', safe='')}"


def check_url(cloud: CloudId, credentials: CloudCredentials, settings: Settings) -> str:
    if cloud == "browserstack":
        return "https://api.browserstack.com/automate/plan.json"
    if cloud == "saucelabs":
        return _sauce_url(sauce_region(credentials), credentials)
    return "https://api.lambdatest.com/automation/api/v1/builds?limit=1"


async def _accepting_sauce_region(client: Any, credentials: CloudCredentials, refused: str) -> str | None:
    for region in SAUCELABS_REGIONS:
        if region == refused:
            continue
        try:
            response = await client.get(_sauce_url(region, credentials), headers={"Accept": "application/json"})
        except httpx.HTTPError:
            continue
        if response.status_code < 400:
            return region
    return None


async def check_credentials(cloud: CloudId, credentials: CloudCredentials, settings: Settings, client_factory: ClientFactory = httpx.AsyncClient) -> CheckResult:
    """Whether ``cloud`` accepts ``credentials``."""
    name = cloud_info(cloud).name
    if credentials.auth is None:
        return CheckResult(False, f"no {name} credentials: set {credentials.username_env} and {credentials.access_key_env}, or connect {name} in the tester UI")
    try:
        url = check_url(cloud, credentials, settings)
    except ValueError as exc:
        return CheckResult(False, str(exc))
    accepted_in: str | None = None
    try:
        async with client_factory(timeout=CHECK_TIMEOUT_S, auth=credentials.auth, follow_redirects=False) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
            if cloud == "saucelabs" and response.status_code in (401, 403):
                accepted_in = await _accepting_sauce_region(client, credentials, sauce_region(credentials))
    except httpx.HTTPError as exc:
        return CheckResult(False, f"{name} could not be reached ({type(exc).__name__}); check the network or proxy settings")
    if accepted_in is not None:
        return CheckResult(
            False, f"{name} refused the credentials in region {sauce_region(credentials)} (HTTP {response.status_code}) but accepts them in {accepted_in}: choose the region {accepted_in}"
        )
    if cloud == "saucelabs" and response.status_code in (401, 403):
        return CheckResult(False, f"{name} refused the credentials in region {sauce_region(credentials)} (HTTP {response.status_code}): check the user name, access key and region")
    if response.status_code in (401, 403):
        return CheckResult(False, f"{name} refused the credentials (HTTP {response.status_code}): check the user name and access key")
    if response.status_code >= 400:
        return CheckResult(False, f"{name} answered HTTP {response.status_code}")
    return CheckResult(True, f"connected to {name} as {credentials.username}")
