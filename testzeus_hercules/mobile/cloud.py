"""Device cloud REST calls around a session: upload the app before it, report pass or fail after it.

* BrowserStack App Automate: ``POST <api>/upload`` (multipart ``file``) answers ``{"app_url": "bs://..."}``;
  ``PUT <api>/sessions/<id>.json`` with ``{"status": "passed"|"failed"}``.
* Sauce Labs: ``POST <api>/v1/storage/upload`` (multipart ``payload``) answers ``{"item": {"id": ...}}``, used as
  ``storage:<id>``; ``PUT <api>/rest/v1/<user>/jobs/<id>`` with ``{"passed": true|false}``.
* TestMu AI (formerly LambdaTest) real devices: ``POST <api>/app/upload/realDevice`` (multipart ``appFile`` and a
  ``name`` field) answers ``{"app_url": "lt://..."}``; the result is set through the session itself, by executing
  the script ``lambda-status=passed`` (or ``failed``) on the hub (``POST <hub>/session/<id>/execute/sync``).

Credentials go in the HTTP Basic header only. An iOS ``.app`` folder is zipped before it is uploaded.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import httpx

from testzeus_hercules.mobile.target import DeviceTarget
from testzeus_hercules.mobile.webdriver import ClientFactory, WebDriverClient, error_text

logger = logging.getLogger(__name__)

_UPLOAD_TIMEOUT_S = 600.0
_STATUS_TIMEOUT_S = 30.0


class CloudError(RuntimeError):
    """A device cloud call failed; the message is safe to show."""


def _payload(path: Path) -> tuple[str, bytes]:
    if path.is_dir():  # an iOS simulator build: upload it zipped
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in sorted(path.rglob("*")):
                if item.is_file() and not item.is_symlink():
                    archive.write(item, Path(path.name) / item.relative_to(path))
        return f"{path.name}.zip", buffer.getvalue()
    return path.name, path.read_bytes()


async def upload_app(target: DeviceTarget, client_factory: ClientFactory = httpx.AsyncClient) -> str:
    """Upload the target's app file; returns the app id the hub knows it by."""
    if target.app_file is None or target.api_url is None or target.auth is None:
        raise CloudError(f"no app file or {target.provider} API to upload it to")
    return await upload_file(target.app_file, target.provider, target.api_url, target.auth, client_factory)


async def upload_file(app_file: Path, provider: str, api_url: str, auth: tuple[str, str], client_factory: ClientFactory = httpx.AsyncClient) -> str:
    """Upload ``app_file`` (an iOS ``.app`` folder zipped) to ``provider``'s app storage at ``api_url``; returns the app
    id its hub knows it by (``bs://...``, ``storage:<id>``, ``lt://...``). :class:`CloudError` when it fails."""
    name, data = _payload(app_file)
    api = api_url.rstrip("/")
    form: dict[str, str] | None = None
    if provider == "browserstack":
        url, files = f"{api}/upload", {"file": (name, data)}
    elif provider == "saucelabs":
        url, files, form = f"{api}/v1/storage/upload", {"payload": (name, data)}, {"name": name}
    elif provider == "testmu":
        url, files, form = f"{api}/app/upload/realDevice", {"appFile": (name, data)}, {"name": name}
    else:
        raise CloudError(f"{provider} has no app storage to upload to")
    async with client_factory(timeout=_UPLOAD_TIMEOUT_S, auth=auth, follow_redirects=True) as client:
        try:
            response = await client.post(url, files=files, data=form)
        except httpx.HTTPError as exc:
            raise CloudError(f"the app could not be uploaded to {provider} ({type(exc).__name__})") from None
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code >= 300 or not isinstance(body, dict):
        reason = "the credentials were refused (HTTP 401)" if response.status_code == 401 else error_text(response, body)
        raise CloudError(f"the app could not be uploaded to {provider}: {reason}")
    if provider == "browserstack" and body.get("app_url"):
        return str(body["app_url"])
    item = body.get("item")
    if provider == "saucelabs" and isinstance(item, dict) and item.get("id"):
        return f"storage:{item['id']}"
    # The docs name the field APP_URL; the API answers app_url (and app_id, the id without its scheme).
    reference = body.get("app_url") or body.get("APP_URL") or (f"lt://{body['app_id']}" if body.get("app_id") else None)
    if provider == "testmu" and isinstance(reference, str) and reference.startswith("lt://"):
        return reference
    raise CloudError(f"{provider} did not say where the uploaded app is")


async def report_status(target: DeviceTarget, session_id: str, passed: bool, client_factory: ClientFactory = httpx.AsyncClient) -> None:
    """Mark the cloud session passed or failed on the provider's dashboard (best effort; never raises)."""
    if target.provider == "testmu" and target.auth is not None:
        await _report_through_session(target, session_id, passed, client_factory)
        return
    if target.provider not in ("browserstack", "saucelabs") or target.api_url is None or target.auth is None:
        return
    if target.provider == "browserstack":
        url, body = f"{target.api_url}/sessions/{session_id}.json", {"status": "passed" if passed else "failed"}
    else:
        url, body = f"{target.api_url}/rest/v1/{target.auth[0]}/jobs/{session_id}", {"passed": passed}
    try:
        async with client_factory(timeout=_STATUS_TIMEOUT_S, auth=target.auth) as client:
            response = await client.put(url, json=body)
        if response.status_code >= 300:
            logger.warning("%s session status not updated (HTTP %s)", target.provider, response.status_code)
    except Exception as exc:  # the dashboard status is a courtesy; the run's result stands
        logger.warning("%s session status not updated (%s)", target.provider, type(exc).__name__)


async def _report_through_session(target: DeviceTarget, session_id: str, passed: bool, client_factory: ClientFactory) -> None:
    """TestMu AI: execute ``lambda-status=passed|failed`` in the session (before it ends; never raises)."""
    client = WebDriverClient(target.hub_url, auth=target.auth, client_factory=client_factory, command_timeout_s=_STATUS_TIMEOUT_S, trust_env=not target.is_local)
    client.session_id = session_id
    try:
        reply = await client.command("POST", "/execute/sync", {"script": f"lambda-status={'passed' if passed else 'failed'}", "args": []})
        if not reply.ok:
            logger.warning("%s session status not updated (%s)", target.provider, reply.error)
    except Exception as exc:  # the dashboard status is a courtesy; the run's result stands
        logger.warning("%s session status not updated (%s)", target.provider, type(exc).__name__)
    finally:
        client.session_id = None  # the session is ended by its owner, not here
        await client.stop()
