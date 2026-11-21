"""The device and browser clouds Hercules can run exported tests on, and what each supports.

* BrowserStack and TestMu AI (formerly LambdaTest) accept a Playwright connection to a cloud browser over a
  WebSocket, so Hercules runs an exported Playwright (Python) test itself, in the browser they provide.
* Sauce Labs runs Playwright tests only through its own runner, ``saucectl``, which uploads the test project and
  runs it in Sauce's cloud; Hercules hands an exported Playwright Test (TypeScript) project to it.
* All three run native app tests through their Appium hubs.

Credentials are read from the environment variables named in ``mobile.<cloud>`` (``username_env``,
``access_key_env``), else from ``<hercules home>/clouds.env`` (see :mod:`testzeus_hercules.clouds.credentials`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

CloudId = Literal["browserstack", "saucelabs", "testmu"]
CLOUD_IDS: tuple[CloudId, ...] = get_args(CloudId)

WebMode = Literal["connect", "saucectl"]


@dataclass(frozen=True)
class CloudInfo:
    id: CloudId
    name: str
    # How a web test runs on it: Hercules connects the browser itself, or the cloud's own runner runs the test.
    web_mode: WebMode
    website_url: str
    dashboard_url: str
    signup_url: str
    keys_url: str
    # The cloud's own runner for CI, and the file the export writes for it.
    ci_runner: str
    ci_config: str


CLOUDS: dict[CloudId, CloudInfo] = {
    "browserstack": CloudInfo(
        id="browserstack",
        name="BrowserStack",
        web_mode="connect",
        website_url="https://www.browserstack.com",
        dashboard_url="https://automate.browserstack.com/dashboard",
        signup_url="https://www.browserstack.com/users/sign_up",
        keys_url="https://www.browserstack.com/accounts/profile/details",
        ci_runner="BrowserStack SDK (browserstack-sdk pytest)",
        ci_config="browserstack.yml",
    ),
    "saucelabs": CloudInfo(
        id="saucelabs",
        name="Sauce Labs",
        web_mode="saucectl",
        website_url="https://saucelabs.com",
        dashboard_url="https://app.saucelabs.com/dashboard/tests",
        signup_url="https://saucelabs.com/sign-up",
        keys_url="https://app.saucelabs.com/user-settings",
        ci_runner="saucectl",
        ci_config=".sauce/config.yml",
    ),
    "testmu": CloudInfo(
        id="testmu",
        name="TestMu AI",
        web_mode="connect",
        website_url="https://www.testmuai.com",
        dashboard_url="https://automation.lambdatest.com/build",
        signup_url="https://accounts.lambdatest.com/register",
        keys_url="https://accounts.lambdatest.com/security",
        ci_runner="HyperExecute (hyperexecute CLI)",
        ci_config="hyperexecute.yaml",
    ),
}


def cloud_info(cloud: str) -> CloudInfo:
    """The cloud named ``cloud``; ValueError for an unknown name."""
    try:
        return CLOUDS[cloud]  # type: ignore[index]
    except KeyError:
        raise ValueError(f"unknown cloud {cloud!r}; use one of {', '.join(CLOUD_IDS)}") from None
