from __future__ import annotations

import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from testzeus_hercules.cases.gherkin import compile_feature
from testzeus_hercules.cases.models import SecretRef, TestCase, placeholder_names, set_environment_names
from testzeus_hercules.daemon.cases import CaseError, apply_environment, environment_names, save_environment
from testzeus_hercules.daemon.models import Environment
from testzeus_hercules.engine.data import CaseData
from testzeus_hercules.engine.run_graph import _resolved_base_url
from testzeus_hercules.kernel.secrets_store import (
    SecretStoreError,
    remove_secret,
    save_secret,
    secret_sources,
    secrets_file,
    with_saved_secrets,
)
from tests.v2.fixtures.daemon_support import client_for, isolated_env, make_config, wait_for

SF_URL = "https://acme.my.salesforce.example/secur/frontdoor.jsp?sid={{sid}}&startURL=%2Fhome"


@pytest.fixture(autouse=True)
def _no_environment_lookup() -> Any:
    set_environment_names(None)
    yield
    set_environment_names(None)


def _envs(tmp_path: Path, *environments: Environment) -> Path:
    envs_dir = tmp_path / "envs"
    for environment in environments:
        save_environment(envs_dir, environment, overwrite=True)
    return envs_dir


def _case(*actions: str, ref: str | None = None, test_data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": "uses-env",
        "title": "Uses the environment",
        "environment": {"ref": ref} if ref else {},
        "test_data": test_data or {},
        "steps": [{"action": action} for action in actions],
    }


def test_environment_variables_merge_into_the_case_and_the_case_wins(tmp_path: Path) -> None:
    envs_dir = _envs(
        tmp_path,
        Environment(name="shop", base_url="http://127.0.0.1:8201", secret_names=["SHOP_PASSWORD"], variables={"user": "ana", "coupon": "PANTRY10", "token": {"secret": "SHOP_TOKEN"}}),
    )
    set_environment_names(environment_names(envs_dir))
    case = TestCase.model_validate(_case('Fill "{{user}}" in "Name"', 'Fill "{{SHOP_PASSWORD}}" in "Password"', 'Fill "{{coupon}}" in "Coupon"', ref="shop", test_data={"coupon": "SAVE10"}))

    applied = apply_environment(case, envs_dir, environment=None, base_url=None)

    assert applied.test_data == {"SHOP_PASSWORD": SecretRef(secret="SHOP_PASSWORD"), "user": "ana", "coupon": "SAVE10", "token": SecretRef(secret="SHOP_TOKEN")}
    assert applied.environment.base_url == "http://127.0.0.1:8201"


def test_a_name_the_environment_defines_is_known_only_with_that_environment(tmp_path: Path) -> None:
    envs_dir = _envs(tmp_path, Environment(name="shop", base_url="http://127.0.0.1:8201", variables={"user": "ana"}))
    with pytest.raises(ValidationError, match="variables of the case's environment"):
        TestCase.model_validate(_case('Fill "{{user}}" in "Name"', ref="shop"))

    set_environment_names(environment_names(envs_dir))
    assert TestCase.model_validate(_case('Fill "{{user}}" in "Name"', ref="shop")).environment.ref == "shop"
    with pytest.raises(ValidationError, match="unknown placeholder"):
        TestCase.model_validate(_case('Fill "{{user}}" in "Name"', ref="other"))
    with placeholder_names({"user"}):
        assert TestCase.model_validate(_case('Fill "{{user}}" in "Name"')).environment.ref is None


def test_feature_files_use_environment_values_and_testzeus_references(tmp_path: Path) -> None:
    envs_dir = _envs(tmp_path, Environment(name="sf", base_url=SF_URL, variables={"sid": {"secret": "SF_SID"}, "user": "admin@acme.example"}))
    set_environment_names(environment_names(envs_dir))
    feature = """@env:sf
Feature: Salesforce
  Scenario: Sign in through the front door
    Given I open "https://acme.my.salesforce.example/secur/frontdoor.jsp?sid=[$.environment.sf.sid]"
    When I fill "[$.testdata.creds.user]" in "Username"
    Then I should see "{{user}}"
"""

    case = apply_environment(compile_feature(feature), envs_dir, environment=None, base_url=None)

    assert [step.action for step in case.steps][:2] == ['I open "https://acme.my.salesforce.example/secur/frontdoor.jsp?sid={{sid}}"', 'I fill "{{user}}" in "Username"']
    assert case.test_data["sid"] == SecretRef(secret="SF_SID") and case.environment.base_url == SF_URL


def test_a_placeholder_in_a_base_url_must_be_defined(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"base_url uses \{\{sid\}\}"):
        Environment(name="sf", base_url=SF_URL)
    envs_dir = _envs(tmp_path, Environment(name="plain", base_url="http://127.0.0.1:8201"))
    case = TestCase.model_validate(_case("Open the home page"))

    with pytest.raises(CaseError, match=r"\{\{sid\}\}"):
        apply_environment(case, envs_dir, environment="plain", base_url=SF_URL)


def test_the_run_opens_the_base_url_with_its_secret_and_masks_it(tmp_path: Path) -> None:
    case = TestCase.model_validate({**_case("Open the home page"), "environment": {"base_url": SF_URL}, "test_data": {"sid": {"secret": "SF_SID"}}})
    data = CaseData.from_case(case, env={"SF_SID": "00Dxx0000001gPL!AQ4AQFfz"})

    resolved = _resolved_base_url(case.environment.base_url, data)

    assert resolved == "https://acme.my.salesforce.example/secur/frontdoor.jsp?sid=00Dxx0000001gPL!AQ4AQFfz&startURL=%2Fhome"
    assert "00Dxx0000001gPL" not in data.redact(f"opened {resolved}")
    assert _resolved_base_url(case.environment.base_url, CaseData.from_case(case, env={})) == SF_URL
    assert _resolved_base_url(None, data) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_saved_secrets_are_private_and_the_environment_wins(tmp_path: Path) -> None:
    home = tmp_path / "home"
    save_secret(home, "SF_SID", "from-the-ui")
    save_secret(home, "SHOP_PASSWORD", "hunter22")

    assert stat.S_IMODE(secrets_file(home).stat().st_mode) == 0o600
    merged = with_saved_secrets({"SHOP_PASSWORD": "from-the-shell"}, home)
    assert merged["SF_SID"] == "from-the-ui" and merged["SHOP_PASSWORD"] == "from-the-shell"
    assert secret_sources(["SF_SID", "SHOP_PASSWORD", "NOPE"], {"SHOP_PASSWORD": "x"}, home) == {"SF_SID": "file", "SHOP_PASSWORD": "env", "NOPE": "none"}
    assert remove_secret(home, "SF_SID") is True and remove_secret(home, "SF_SID") is False
    remove_secret(home, "SHOP_PASSWORD")
    assert not secrets_file(home).exists()


@pytest.mark.parametrize(("name", "value"), [("lower", "s3cret-value"), ("OK", ""), ("OK", "two\nlines"), ("OK", " padded "), ("OK", "x" * 5000)])
def test_bad_secrets_are_refused_without_echoing_the_value(tmp_path: Path, name: str, value: str) -> None:
    with pytest.raises(SecretStoreError) as raised:
        save_secret(tmp_path / "home", name, value)
    assert not value.strip() or value.strip() not in str(raised.value)


def test_secrets_api_saves_values_it_never_returns(tmp_path: Path) -> None:
    config = make_config(tmp_path, env=isolated_env(tmp_path, SHOP_PASSWORD="from-the-shell"))
    with client_for(config) as client:
        saved = client.put("/api/secrets/SF_SID", json={"value": "00Dxx-secret-value"})
        shadowed = client.put("/api/secrets/SHOP_PASSWORD", json={"value": "from-the-ui"})
        listed = client.get("/api/secrets", params={"names": "SF_SID,SHOP_PASSWORD,NOPE"})
        bad_name = client.put("/api/secrets/lower", json={"value": "x"})
        bad_query = client.get("/api/secrets", params={"names": "ok,lower"})
        removed = client.delete("/api/secrets/SF_SID")
    assert saved.status_code == 200 and saved.json() == {"name": "SF_SID", "source": "file", "note": None}
    assert shadowed.json()["source"] == "env" and "wins over the saved value" in shadowed.json()["note"]
    assert listed.json()["secrets"] == [{"name": "NOPE", "source": "none"}, {"name": "SF_SID", "source": "file"}, {"name": "SHOP_PASSWORD", "source": "env"}]
    assert bad_name.status_code == 422 and bad_query.status_code == 422
    assert removed.json() == {"name": "SF_SID", "source": "none", "removed": True}
    for response in (saved, shadowed, listed, removed):
        assert "00Dxx-secret-value" not in response.text and "from-the-ui" not in response.text


def test_a_run_started_with_an_environment_gets_its_values(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        created = client.post("/api/environments", json={"name": "shop", "base_url": "http://127.0.0.1:9", "variables": {"user": "ana", "password": {"secret": "SHOP_PASSWORD"}}})
        run = client.post("/api/runs", json={"test_case": _case('Fill "{{user}}" in "Name"', 'Fill "{{password}}" in "Password"'), "environment": "shop"})
        missing = client.post("/api/runs", json={"test_case": _case('Fill "{{user}}" in "Name"')})
        assert run.status_code == 201, run.text
        run_id = run.json()["run_id"]
        wait_for(client, run_id)
        case = client.get(f"/api/runs/{run_id}/case").json()
    assert created.status_code == 201
    assert missing.status_code == 422 and "unknown placeholder" in missing.text
    assert case["test_case"]["test_data"] == {"user": "ana", "password": {"secret": "SHOP_PASSWORD"}}


SIGN_IN = """Feature: Shop
  Scenario: Sign in
    Given I open "http://127.0.0.1:9/login"
    When I fill "{{user}}" in "Email"
    And I fill "{{password}}" in "Password"
    Then I should see "Welcome"
"""


def test_a_feature_keeps_its_test_data_beside_it(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    data = {"user": "ana@example.com", "password": {"secret": "SHOP_PASSWORD"}}
    with client_for(config) as client:
        unsaved = client.post("/api/cases/validate", json={"gherkin": SIGN_IN})
        checked = client.post("/api/cases/validate", json={"gherkin": SIGN_IN, "test_data": data})
        saved = client.post("/api/cases", json={"gherkin": SIGN_IN, "test_data": data})
        listed = client.get("/api/cases").json()["cases"]
        opened = client.get("/api/cases/sign-in.feature").json()
        stray = client.post("/api/cases", json={"test_case": _case("Open the home page"), "test_data": data})
        cleared = client.post("/api/cases", json={"gherkin": SIGN_IN.replace("{{user}}", "ana").replace("{{password}}", "pw"), "overwrite": True})
    sidecar = config.paths.cases_dir / "sign-in.data.json"
    assert unsaved.json()["valid"] is False and "unknown placeholder" in unsaved.json()["error"]
    assert checked.json()["valid"] is True and checked.json()["test_case"]["test_data"] == data
    assert saved.status_code == 201 and [entry["name"] for entry in listed] == ["sign-in.feature"]
    assert opened["test_case"]["test_data"] == data and "{{user}}" in opened["source"]
    assert stray.status_code == 422 and cleared.status_code == 201 and not sidecar.exists()


def test_a_feature_run_from_the_cli_reads_its_test_data(tmp_path: Path) -> None:
    from testzeus_hercules.cases.gherkin import GherkinError, load_feature

    feature = tmp_path / "sign-in.feature"
    feature.write_text(SIGN_IN, encoding="utf-8")
    with pytest.raises(ValidationError, match="unknown placeholder"):
        load_feature(feature)
    (tmp_path / "sign-in.data.json").write_text('{"test_data": {"user": "ana", "password": {"secret": "SHOP_PASSWORD"}}}', encoding="utf-8")
    assert load_feature(feature).test_data == {"user": "ana", "password": SecretRef(secret="SHOP_PASSWORD")}
    (tmp_path / "sign-in.data.json").write_text('{"test_data": {"run_id": "x"}}', encoding="utf-8")
    with pytest.raises((GherkinError, ValidationError), match="built-in"):
        load_feature(feature)


def test_the_case_list_names_each_case_environment(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        client.post("/api/environments", json={"name": "shop", "base_url": "http://127.0.0.1:9"})
        client.post("/api/cases", json={"gherkin": "@env:shop\n" + SIGN_IN.replace("{{user}}", "ana").replace("{{password}}", "pw")})
        client.post("/api/cases", json={"test_case": _case("Open the home page")})
        listed = {entry["name"]: entry["environment"] for entry in client.get("/api/cases").json()["cases"]}
    assert listed == {"sign-in.feature": "shop", "uses-env.json": None}
