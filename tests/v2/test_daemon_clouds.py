"""The daemon's cloud API: connect, check and disconnect clouds; run a passed run on a cloud; download its project."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from testzeus_hercules.daemon import clouds as cloud_api
from tests.v2.fixtures.daemon_support import client_for, isolated_env, make_config, submit, wait_for

KEY = "k3y-value-not-shown"


@pytest.fixture()
def accepting(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(cloud_api, "check_client_factory", lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs))
    return seen


def test_clouds_are_listed_not_connected(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        body = client.get("/api/clouds").json()
    assert [cloud["id"] for cloud in body["clouds"]] == ["browserstack", "saucelabs", "testmu"]
    assert all(not cloud["connected"] and cloud["source"] == "none" for cloud in body["clouds"])
    assert body["egress_allows"] is True and body["keys_file"].endswith("clouds.env")
    testmu = body["clouds"][2]
    assert testmu["name"] == "TestMu AI" and testmu["username_env"] == "LT_USERNAME" and testmu["web_mode"] == "connect"
    assert body["clouds"][1]["web_mode"] == "saucectl"
    assert [cloud["website_url"] for cloud in body["clouds"]] == ["https://www.browserstack.com", "https://saucelabs.com", "https://www.testmuai.com"]


def test_connect_check_and_disconnect_never_return_the_key(tmp_path: Path, accepting: list[httpx.Request]) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        saved = client.put("/api/clouds/testmu", json={"username": "demo", "access_key": KEY})
        listed = client.get("/api/clouds")
        checked = client.post("/api/clouds/testmu/check")
        removed = client.delete("/api/clouds/testmu")
        after = client.get("/api/clouds").json()
    assert saved.status_code == 200 and saved.json()["connected"] and saved.json()["source"] == "file"
    assert saved.json()["check"] == {"ok": True, "message": "connected to TestMu AI as demo"}
    assert checked.json()["ok"] is True and len(accepting) == 2
    assert removed.json()["removed"] is True and not after["clouds"][2]["connected"]
    for response in (saved, listed, checked, removed):
        assert KEY not in response.text


def test_bad_cloud_requests_are_refused(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        unknown = client.put("/api/clouds/selenium-grid", json={"username": "a", "access_key": "b"})
        missing = client.put("/api/clouds/browserstack", json={"username": "a"})
        extra = client.put("/api/clouds/browserstack", json={"username": "a", "access_key": "b", "region": "x"})
        newline = client.put("/api/clouds/browserstack", json={"username": "a", "access_key": "two\nlines"})
    assert unknown.status_code == 404 and missing.status_code == 422 and extra.status_code == 422
    assert newline.status_code == 422 and "two" not in newline.json()["error"]


def test_sauce_region_is_listed_saved_and_checked(tmp_path: Path, accepting: list[httpx.Request]) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        listed = client.get("/api/clouds").json()["clouds"]
        before_keys = client.put("/api/clouds/saucelabs/region", json={"region": "eu-central-1"})
        saved = client.put("/api/clouds/saucelabs", json={"username": "demo", "access_key": KEY, "region": "us-east-4"})
        changed = client.put("/api/clouds/saucelabs/region", json={"region": "eu-central-1"})
        unknown = client.put("/api/clouds/saucelabs/region", json={"region": "mars-1"})
        not_sauce = client.put("/api/clouds/browserstack/region", json={"region": "eu-central-1"})
    assert (listed[1]["region"], listed[1]["region_source"], listed[1]["regions"]) == ("us-west-1", "default", ["us-west-1", "us-east-4", "eu-central-1"])
    assert listed[0]["regions"] == [] and listed[0]["region"] is None
    assert before_keys.status_code == 200 and before_keys.json()["check"] is None and before_keys.json()["region"] == "eu-central-1"
    assert saved.json()["region"] == "us-east-4" and saved.json()["check"]["ok"] is True
    assert changed.json()["region_source"] == "file" and changed.json()["check"]["ok"] is True
    assert [request.url.host for request in accepting] == ["api.us-east-4.saucelabs.com", "api.eu-central-1.saucelabs.com"]
    assert unknown.status_code == 422 and not_sauce.status_code == 422
    assert KEY not in changed.text


def test_an_environment_sauce_region_wins_and_is_named(tmp_path: Path, accepting: list[httpx.Request]) -> None:
    config = make_config(tmp_path, env=isolated_env(tmp_path, SAUCE_REGION="us-east-4"))
    with client_for(config) as client:
        changed = client.put("/api/clouds/saucelabs/region", json={"region": "eu-central-1"}).json()
    assert changed["region"] == "us-east-4" and changed["region_source"] == "env" and "SAUCE_REGION" in changed["note"]


def test_environment_credentials_are_reported_and_win(tmp_path: Path, accepting: list[httpx.Request]) -> None:
    config = make_config(tmp_path, env=isolated_env(tmp_path, BROWSERSTACK_USERNAME="from_env", BROWSERSTACK_ACCESS_KEY=KEY))
    with client_for(config) as client:
        listed = client.get("/api/clouds").json()["clouds"][0]
        saved = client.put("/api/clouds/browserstack", json={"username": "other", "access_key": "other-key"}).json()
        removed = client.delete("/api/clouds/browserstack").json()
    assert listed["connected"] and listed["source"] == "env" and listed["username"] == "from_env"
    assert saved["source"] == "env" and "win over the saved keys" in saved["note"]
    assert "still set" in removed["note"]


def test_a_failed_run_does_not_run_on_a_cloud_and_an_unconnected_cloud_is_named(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        passed = submit(client)
        wait_for(client, passed)
        failed = submit(client, "fake-fail")
        wait_for(client, failed)
        refused = client.post(f"/api/runs/{failed}/cloud-runs", json={"cloud": "browserstack"})
        not_connected = client.post(f"/api/runs/{passed}/cloud-runs", json={"cloud": "browserstack"})
        bad_browser = client.post(f"/api/runs/{passed}/cloud-runs", json={"cloud": "browserstack", "browser": "netscape"})
        listed = client.get(f"/api/runs/{passed}/cloud-runs")
        unknown = client.get("/api/cloud-runs/0123456789ab")
    assert refused.status_code == 409 and "only passed runs" in refused.json()["error"]
    assert not_connected.status_code == 409 and "not connected" in not_connected.json()["error"]
    assert bad_browser.status_code == 422
    assert listed.json() == {"cloud_runs": []} and unknown.status_code == 404


def test_the_cloud_project_downloads_as_a_zip(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        passed = submit(client)
        wait_for(client, passed)
        bundle = client.get(f"/api/runs/{passed}/cloud-bundle", params={"cloud": "testmu", "browser": "chromium", "os": "macos"})
        sauce = client.get(f"/api/runs/{passed}/cloud-bundle", params={"cloud": "saucelabs"})
        bad = client.get(f"/api/runs/{passed}/cloud-bundle", params={"cloud": "nowhere"})
    assert bundle.status_code == 200 and bundle.headers["content-type"] == "application/zip"
    assert 'filename="demo-login-testmu.zip"' in bundle.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        names = set(archive.namelist())
        conftest = archive.read("conftest.py").decode()
    assert {"conftest.py", "hyperexecute.yaml", "tests/test_demo_login.py", "requirements.txt", "README.md"} <= names
    assert '"chromium")' in conftest and '"macos")' in conftest
    with zipfile.ZipFile(io.BytesIO(sauce.content)) as archive:
        assert ".sauce/config.yml" in archive.namelist() and "tests/demo-login.spec.ts" in archive.namelist()
    assert bad.status_code == 422


def test_a_cloud_run_starts_and_is_listed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from testzeus_hercules.clouds import runner as runner_module

    started: dict[str, Any] = {}

    async def fake_run(self: Any, record: Any, bundle: Any, credentials: Any, web: Any, project: Path) -> None:
        started["project"] = project
        record.status, record.message, record.session_url = "passed", "1 passed", "https://automation.lambdatest.com/test?testID=1"
        self._write(record)

    monkeypatch.setattr(runner_module.CloudRuns, "_run", fake_run)
    monkeypatch.setattr(runner_module, "requirements", lambda kind, mode: None)
    config = make_config(tmp_path, env=isolated_env(tmp_path, LT_USERNAME="demo", LT_ACCESS_KEY=KEY))
    with client_for(config) as client:
        passed = submit(client)
        wait_for(client, passed)
        created = client.post(f"/api/runs/{passed}/cloud-runs", json={"cloud": "testmu", "browser": "edge"})
        cloud_run_id = created.json()["id"]
        listed = client.get(f"/api/runs/{passed}/cloud-runs").json()["cloud_runs"]
        one = client.get(f"/api/cloud-runs/{cloud_run_id}").json()
        cancelled = client.post(f"/api/cloud-runs/{cloud_run_id}/cancel").json()
    assert created.status_code == 202 and created.json()["cloud_name"] == "TestMu AI" and created.json()["options"] == {"browser": "edge", "os": "windows"}
    assert [item["id"] for item in listed] == [cloud_run_id]
    assert one["status"] == "passed" and one["session_url"].startswith("https://automation.lambdatest.com/") and "log" in one
    assert cancelled["status"] == "passed", "a finished run stays as it was"
    assert (started["project"] / "conftest.py").is_file()
    assert KEY not in json.dumps([created.json(), listed, one])
