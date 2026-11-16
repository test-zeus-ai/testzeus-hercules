"""Device and browser clouds: credentials, the connection check, the project written for each cloud, the runner."""

from __future__ import annotations

import asyncio
import io
import json
import stat
import sys
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.v2.test_export_render import result as web_result

from testzeus_hercules.clouds import runner as runner_module
from testzeus_hercules.clouds.bundle import (
    SESSION_MARKER,
    WebOptions,
    connect_conftest,
    web_bundle,
)
from testzeus_hercules.clouds.check import check_credentials
from testzeus_hercules.clouds.credentials import (
    CredentialError,
    clouds_file,
    find_credentials,
    remove_credentials,
    save_credentials,
    with_cloud_credentials,
)
from testzeus_hercules.clouds.runner import (
    CloudRunError,
    CloudRuns,
    MobileOptions,
    _session_url,
    redact,
)
from testzeus_hercules.export.common import ExportError
from testzeus_hercules.kernel.settings import Settings

KEY = "s3cr3t/Key+with=chars"
USER = "demo_user"


def _settings(tmp_path: Path, **env: str) -> Settings:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home"), **env})


# --------------------------------------------------------------------------- credentials


def test_environment_wins_over_the_saved_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    save_credentials("browserstack", settings, "saved_user", "saved-key")

    from_env = find_credentials("browserstack", settings, {"BROWSERSTACK_USERNAME": USER, "BROWSERSTACK_ACCESS_KEY": KEY})
    from_file = find_credentials("browserstack", settings, {})

    assert (from_env.source, from_env.username, from_env.access_key) == ("env", USER, KEY)
    assert (from_file.source, from_file.username, from_file.access_key) == ("file", "saved_user", "saved-key")
    assert KEY not in repr(from_env) and "saved-key" not in repr(from_file)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_saved_file_is_private_and_keeps_other_clouds(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    save_credentials("browserstack", settings, "bs_user", "bs-key")
    save_credentials("testmu", settings, "lt_user", "lt-key")

    path = clouds_file(settings.home_dir)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text(encoding="utf-8")
    assert "BROWSERSTACK_USERNAME=bs_user" in text and "LT_ACCESS_KEY=lt-key" in text

    assert remove_credentials("browserstack", settings) is True
    assert find_credentials("browserstack", settings, {}).source == "none"
    assert find_credentials("testmu", settings, {}).username == "lt_user"
    assert remove_credentials("browserstack", settings) is False
    remove_credentials("testmu", settings)
    assert not path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_a_file_others_can_read_is_ignored(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    save_credentials("saucelabs", settings, USER, KEY)
    clouds_file(settings.home_dir).chmod(0o644)

    found = find_credentials("saucelabs", settings, {})

    assert found.source == "none" and found.problem is not None and "chmod 600" in found.problem and KEY not in found.problem


@pytest.mark.parametrize("value", ["", "two\nlines", "'quoted'", "x" * 600])
def test_bad_values_are_refused_without_echoing_them(tmp_path: Path, value: str) -> None:
    with pytest.raises(CredentialError) as raised:
        save_credentials("browserstack", _settings(tmp_path), USER, value)
    assert value.strip() == "" or value not in str(raised.value)


def test_saved_keys_are_added_to_a_copy_of_the_environment(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    save_credentials("testmu", settings, USER, KEY)
    env = {"PATH": "/bin"}

    merged = with_cloud_credentials(env, settings)

    assert merged["LT_USERNAME"] == USER and merged["LT_ACCESS_KEY"] == KEY and "LT_USERNAME" not in env


def test_custom_variable_names_are_used(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".hercules").mkdir(parents=True)
    (project / ".hercules" / "config.yaml").write_text("mobile:\n  browserstack:\n    username_env: CI_BS_USER\n    access_key_env: CI_BS_KEY\n", encoding="utf-8")
    settings = Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home")})

    found = find_credentials("browserstack", settings, {"CI_BS_USER": USER, "CI_BS_KEY": KEY})

    assert found.found and (found.username_env, found.access_key_env) == ("CI_BS_USER", "CI_BS_KEY")


# --------------------------------------------------------------------------- connection check


def _factory(status: int, seen: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={})

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    return factory


@pytest.mark.parametrize(
    ("cloud", "url"),
    [
        ("browserstack", "https://api.browserstack.com/automate/plan.json"),
        ("saucelabs", f"https://api.us-west-1.saucelabs.com/rest/v1/users/{USER}"),
        ("testmu", "https://api.lambdatest.com/automation/api/v1/builds?limit=1"),
    ],
)
def test_check_calls_each_clouds_api_with_basic_auth(tmp_path: Path, cloud: str, url: str) -> None:
    settings = _settings(tmp_path)
    save_credentials(cloud, settings, USER, KEY)  # type: ignore[arg-type]
    seen: list[httpx.Request] = []

    result = asyncio.run(check_credentials(cloud, find_credentials(cloud, settings, {}), settings, _factory(200, seen)))  # type: ignore[arg-type]

    assert result.ok and USER in result.message and KEY not in result.message
    assert str(seen[0].url) == url and seen[0].headers["authorization"].startswith("Basic ")


def test_check_reports_refused_and_missing_credentials(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    missing = asyncio.run(check_credentials("testmu", find_credentials("testmu", settings, {}), settings, _factory(200, [])))
    save_credentials("testmu", settings, USER, KEY)
    refused = asyncio.run(check_credentials("testmu", find_credentials("testmu", settings, {}), settings, _factory(401, [])))

    assert not missing.ok and "LT_USERNAME" in missing.message
    assert not refused.ok and "refused" in refused.message and KEY not in refused.message


def test_check_reports_an_unreachable_cloud(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    save_credentials("browserstack", settings, USER, KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    result = asyncio.run(check_credentials("browserstack", find_credentials("browserstack", settings, {}), settings, factory))

    assert not result.ok and "could not be reached" in result.message


# --------------------------------------------------------------------------- the project for each cloud


@pytest.mark.parametrize("cloud", ["browserstack", "testmu"])
def test_connect_project_runs_the_python_export_in_the_clouds_browser(cloud: str) -> None:
    bundle = web_bundle(web_result(), cloud, WebOptions("edge", "macos"))  # type: ignore[arg-type]

    assert bundle.mode == "connect" and bundle.test_path.startswith("tests/") and bundle.test_path.endswith(".py")
    expected = {bundle.test_path, "conftest.py", "requirements.txt", "README.md", "browserstack.yml" if cloud == "browserstack" else "hyperexecute.yaml"}
    assert set(bundle.files) == expected
    for path, text in bundle.files.items():
        if path.endswith(".py"):
            compile(text, path, "exec")
    conftest = bundle.files["conftest.py"]
    assert ("wss://cdp.browserstack.com/playwright" if cloud == "browserstack" else "wss://cdp.lambdatest.com/playwright") in conftest
    assert '"edge")' in conftest and '"macos")' in conftest
    assert "playwright==" in bundle.files["requirements.txt"]
    with zipfile.ZipFile(io.BytesIO(bundle.zip_bytes())) as archive:
        assert set(archive.namelist()) == expected


def test_sauce_project_is_a_playwright_test_project_for_saucectl() -> None:
    bundle = web_bundle(web_result(), "saucelabs", WebOptions("chrome", "windows"))

    assert bundle.mode == "saucectl" and bundle.test_path.endswith(".spec.ts")
    config = bundle.files[".sauce/config.yml"]
    assert "kind: playwright" in config and "apiVersion: v1alpha" in config and "platformName: Windows 11" in config
    assert json.loads(bundle.files["package.json"])["devDependencies"]["@playwright/test"]
    for item in bundle.env:
        if item.required:
            assert f"{item.name}: ${item.name}" in config, "the cloud gets the test's variables from saucectl's environment"
        else:
            assert f"  {item.name}: $" not in config, "an unset optional variable would arrive empty and override the test's default"


def test_sauce_refuses_edge_and_web_projects_refuse_native_runs() -> None:
    with pytest.raises(ExportError, match="not Edge"):
        web_bundle(web_result(), "saucelabs", WebOptions("edge", "windows"))
    native = web_result()
    assert native.automation is not None
    native = native.model_copy(update={"automation": native.automation.model_copy(update={"app": {"platform": "android", "app_id": "com.example"}})})
    with pytest.raises(ExportError, match="native app"):
        web_bundle(native, "browserstack", WebOptions())


@pytest.mark.parametrize("cloud", ["browserstack", "saucelabs", "testmu"])
def test_no_project_file_holds_credentials(cloud: str) -> None:
    bundle = web_bundle(web_result(), cloud, WebOptions())  # type: ignore[arg-type]
    names = {"browserstack": "BROWSERSTACK_ACCESS_KEY", "saucelabs": "SAUCE_ACCESS_KEY", "testmu": "LT_ACCESS_KEY"}[cloud]

    text = "\n".join(bundle.files.values())

    assert names in text, "the files read the key from its variable"
    assert KEY not in text


def test_conftest_marks_the_session_and_prints_its_link() -> None:
    for cloud, action in (("browserstack", "setSessionStatus"), ("testmu", "setTestStatus")):
        code = connect_conftest(cloud, WebOptions(), ("U", "K"), "build")  # type: ignore[arg-type]
        assert action in code and SESSION_MARKER in code and "HERCULES_CLOUD_CONNECT" in code


# --------------------------------------------------------------------------- the runner


def test_redact_masks_plain_and_url_encoded_forms() -> None:
    masks = runner_module._masks(find_credentials_stub())
    text = f"wss://x?caps=%7B%22key%22%3A%22{'s3cr3t%2FKey%2Bwith%3Dchars'}%22%7D and {KEY} and s3cr3t%2FKey%2Bwith%3Dchars"

    cleaned = redact(text, masks)

    assert KEY not in cleaned and "s3cr3t%2FKey%2Bwith%3Dchars" not in cleaned


def find_credentials_stub() -> Any:
    from testzeus_hercules.clouds.credentials import CloudCredentials

    return CloudCredentials("browserstack", "BROWSERSTACK_USERNAME", "BROWSERSTACK_ACCESS_KEY", "env", USER, KEY)


def test_session_links_are_found_in_the_output() -> None:
    assert _session_url(f"x\n{SESSION_MARKER}https://automate.browserstack.com/builds/b/sessions/s\n") == "https://automate.browserstack.com/builds/b/sessions/s"
    assert _session_url("Job https://app.saucelabs.com/tests/0123456789abcdef0123 done") == "https://app.saucelabs.com/tests/0123456789abcdef0123"
    assert _session_url("nothing here") is None


def _runs(tmp_path: Path, env: dict[str, str], **settings_env: str) -> CloudRuns:
    settings = _settings(tmp_path, **settings_env)
    return CloudRuns(tmp_path / "cloud-runs", settings, env, settings.project_dir)


def _start(runs: CloudRuns, cloud: str = "browserstack") -> Any:
    async def go() -> Any:
        return runs.start(web_result(), cloud, WebOptions(), MobileOptions())  # type: ignore[arg-type]

    return asyncio.run(go())


def test_start_refuses_when_egress_is_local(tmp_path: Path) -> None:
    runs = _runs(tmp_path, {"BROWSERSTACK_USERNAME": USER, "BROWSERSTACK_ACCESS_KEY": KEY}, HERCULES_EGRESS="local")
    with pytest.raises(CloudRunError, match="egress policy 'local'"):
        _start(runs)


def test_start_refuses_a_cloud_that_is_not_connected(tmp_path: Path) -> None:
    with pytest.raises(CloudRunError, match="not connected"):
        _start(_runs(tmp_path, {}))


def test_start_refuses_when_the_runner_tool_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module.shutil, "which", lambda name: None)
    runs = _runs(tmp_path, {"SAUCE_USERNAME": USER, "SAUCE_ACCESS_KEY": KEY})
    with pytest.raises(CloudRunError, match="saucectl"):
        _start(runs, "saucelabs")


def test_start_names_the_variables_the_test_needs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module, "requirements", lambda kind, mode: None)
    runs = _runs(tmp_path, {"BROWSERSTACK_USERNAME": USER, "BROWSERSTACK_ACCESS_KEY": KEY})
    with pytest.raises(CloudRunError, match="environment variables") as raised:
        _start(runs)
    assert KEY not in str(raised.value)


FAKE_PYTEST = """#!{python}
import os, sys
print("collected 1 item")
print("{marker}https://automate.browserstack.com/builds/b/sessions/abc")
print("connecting with key " + os.environ["BROWSERSTACK_ACCESS_KEY"])
print("browser=" + os.environ.get("HERCULES_CLOUD_BROWSER", "") + " os=" + os.environ.get("HERCULES_CLOUD_OS", ""))
print("leaked=" + os.environ.get("OPENROUTER_API_KEY", "none"))
print("1 passed in 0.10s" if os.environ.get("FAKE_EXIT", "0") == "0" else "E   AssertionError: expected text not found")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX script stands in for Python")
@pytest.mark.parametrize("exit_code", [0, 1])
def test_a_cloud_run_records_its_status_link_and_masked_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int) -> None:
    fake = tmp_path / "python"
    fake.write_text(FAKE_PYTEST.format(python=sys.executable, marker=SESSION_MARKER), encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(runner_module.sys, "executable", str(fake))
    monkeypatch.setattr(runner_module, "requirements", lambda kind, mode: None)
    env = {"PATH": "/usr/bin:/bin", "BROWSERSTACK_USERNAME": USER, "BROWSERSTACK_ACCESS_KEY": KEY, "OPENROUTER_API_KEY": "model-key", "FAKE_EXIT": str(exit_code)}
    exported = web_bundle(web_result(), "browserstack", WebOptions()).env
    env.update({item.name: "value" for item in exported})
    runs = _runs(tmp_path, env)
    monkeypatch.setattr(runner_module, "child_environment", lambda environ, keep=(), prefixes=(): {name: value for name, value in environ.items() if name in (*keep, "PATH", "FAKE_EXIT")})

    async def go() -> Any:
        record = runs.start(web_result(), "browserstack", WebOptions("edge", "macos"), MobileOptions())
        for _ in range(200):
            await asyncio.sleep(0.05)
            if record.id not in runs._tasks:
                break
        return runs.get(record.id)

    record = asyncio.run(go())

    assert record.status == ("passed" if exit_code == 0 else "failed")
    assert record.session_url == "https://automate.browserstack.com/builds/b/sessions/abc"
    assert record.exit_code == exit_code and record.message
    log = runs.log_tail(record.id)
    assert KEY not in log and "******" in log
    assert "browser=edge os=macos" in log
    assert "leaked=none" in log, "the daemon's other keys never reach the test"
    project = tmp_path / "cloud-runs" / record.id / "project"
    assert (project / "conftest.py").is_file()
    assert all(KEY not in path.read_text(encoding="utf-8") for path in project.rglob("*") if path.is_file())
    assert [item.id for item in runs.records(record.run_id)] == [record.id]


def test_unfinished_runs_are_marked_after_a_restart(tmp_path: Path) -> None:
    runs = _runs(tmp_path, {})
    from testzeus_hercules.clouds.runner import CloudRunRecord

    runs._write(CloudRunRecord("0123456789ab", "r_1", "c", "testmu", "web", "connect", {}, "running", "2026-01-01T00:00:00+00:00", "https://x"))
    runs.recover()

    record = runs.get("0123456789ab")
    assert record is not None and record.status == "error" and "stopped" in (record.message or "")
    assert runs.get("../../etc") is None


# --------------------------------------------------------------------------- review fixes


def _with_secret(name: str) -> Any:
    run = web_result()
    assert run.automation is not None
    data = dict(run.automation.data)
    first = next(key for key, entry in data.items() if entry.kind == "secret")
    data[first] = data[first].model_copy(update={"env": name})
    return run.model_copy(update={"automation": run.automation.model_copy(update={"data": data})})


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "OPENROUTER_API_KEY", "HERCULES_DAEMON_TOKEN", "AWS_SECRET_ACCESS_KEY", "CEREBRAS_API_KEY"])
def test_a_case_cannot_forward_a_model_or_hercules_key_to_a_cloud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setattr(runner_module, "requirements", lambda kind, mode: None)
    runs = _runs(tmp_path, {"BROWSERSTACK_USERNAME": USER, "BROWSERSTACK_ACCESS_KEY": KEY, name: "model-key"})

    with pytest.raises(CloudRunError, match="never sent to a test on a cloud") as raised:
        runs.prepare(_with_secret(name), "browserstack", WebOptions(), MobileOptions())

    assert "model-key" not in str(raised.value)
    assert not (tmp_path / "cloud-runs").exists() or not any((tmp_path / "cloud-runs").iterdir()), "nothing is written for a refused run"


def test_the_sauce_project_uses_the_configured_region(tmp_path: Path) -> None:
    runs = _runs(tmp_path, {}, HERCULES_HOME=str(tmp_path / "home"))
    project = tmp_path / "project" / ".hercules"
    project.mkdir(parents=True, exist_ok=True)
    (project / "config.yaml").write_text("mobile:\n  saucelabs:\n    region: eu-central-1\n", encoding="utf-8")
    runs.settings = Settings.load(tmp_path / "project", env={"HERCULES_HOME": str(tmp_path / "home")})

    bundle = runs.bundle(web_result(), "saucelabs", WebOptions(), MobileOptions())

    assert "region: eu-central-1" in bundle.files[".sauce/config.yml"]


def test_ci_snippets_use_the_configured_variable_names() -> None:
    bundle = web_bundle(web_result(), "testmu", WebOptions(), credentials=("CI_LT_USER", "CI_LT_KEY"))

    assert "$CI_LT_USER" in bundle.files["hyperexecute.yaml"] and "LT_USERNAME" not in bundle.files["hyperexecute.yaml"]
    assert '"$CI_LT_USER"' in bundle.files["README.md"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_a_cancelled_cloud_run_stops_its_process_and_children(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / "child.pid"
    script = tmp_path / "python"
    script.write_text(
        f"#!/bin/sh\n/bin/sleep 60 &\necho $! > {marker}\necho 'a line of output'\n" + "head -c 200000 /dev/zero | tr '\\\\0' 'x'\necho\nwait\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setattr(runner_module.sys, "executable", str(script))
    monkeypatch.setattr(runner_module, "requirements", lambda kind, mode: None)
    env = {"PATH": "/usr/bin:/bin", "BROWSERSTACK_USERNAME": USER, "BROWSERSTACK_ACCESS_KEY": KEY}
    env.update({item.name: "value" for item in web_bundle(web_result(), "browserstack", WebOptions()).env})
    runs = _runs(tmp_path, env)

    async def go() -> Any:
        record = runs.start(web_result(), "browserstack", WebOptions(), MobileOptions())
        for _ in range(100):
            await asyncio.sleep(0.05)
            log_file = tmp_path / "cloud-runs" / record.id / "output.log"
            if marker.exists() and log_file.exists() and log_file.stat().st_size > 200_000:
                break
        await runs.close()
        return record

    record = asyncio.run(go())

    child = int(marker.read_text().strip())
    for _ in range(50):
        try:
            import os

            os.kill(child, 0)
        except ProcessLookupError:
            break
        asyncio.run(asyncio.sleep(0.05))
    else:
        pytest.fail("the test's child process outlived the cancelled cloud run")
    final = runs.get(record.id)
    assert final is not None and final.status == "cancelled" and not runs.is_active(record.id)
    log = (tmp_path / "cloud-runs" / record.id / "output.log").read_text(encoding="utf-8")
    assert "a line of output" in log and len(log) > 200_000, "a 200 kB line does not stop the output being read"
