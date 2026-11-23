"""``hercules import v1``: v1 ``opt/`` projects become v2 cases, data files and an environment, never copying secret values."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

from testzeus_hercules.cases.importers.v1 import V1ImportOptions, import_v1
from testzeus_hercules.cases.models import TestCase
from testzeus_hercules.cli.main import main

FIXTURE = Path(__file__).parent / "fixtures" / "import" / "v1_project"
FAKE_SECRET_VALUES = ("fake-value-never-copied-1", "fake-value-never-copied-2", "fake-value-never-copied-3")
ENV_FILE = """\
# v1 engine settings and app variables (all values are fake)
LLM_MODEL_NAME=some-model
LLM_MODEL_API_KEY=fake-value-never-copied-1
HEADLESS=false
export BASE_URL="http://localhost:3000"
SHOP_ADMIN_PASSWORD=fake-value-never-copied-2
REGION=eu
"""


@pytest.fixture()
def v1_project(tmp_path: Path) -> Path:
    source = tmp_path / "legacy"
    shutil.copytree(FIXTURE, source)
    (source / ".env").write_text(ENV_FILE)
    return source


def _cases(project: Path) -> dict[str, TestCase]:
    return {path.stem: TestCase.model_validate_json(path.read_text()) for path in sorted((project / ".hercules" / "cases").glob("*.json"))}


def _all_written_text(project: Path) -> str:
    return "\n".join(path.read_text(errors="replace") for path in (project / ".hercules").rglob("*") if path.is_file())


def test_converts_scenarios_and_expands_outline_rows(v1_project: Path, tmp_path: Path) -> None:
    project = tmp_path / "app"

    report = import_v1(V1ImportOptions(source_dir=v1_project, project_dir=project))

    cases = _cases(project)
    assert sorted(cases) == ["locked-accounts-are-refused-1", "locked-accounts-are-refused-2", "upload-an-avatar", "valid-user-signs-in"]
    valid = cases["valid-user-signs-in"]
    assert valid.steps[0].action == 'I open the page "http://localhost:3000/login"'
    assert valid.steps[-1].expected == "the cart badge should be empty"
    assert "smoke" in valid.tags and "imported:v1" in valid.tags
    row = cases["locked-accounts-are-refused-2"]
    assert row.steps[1].action == 'I sign in as "blocked_user" with "standard"'
    assert row.steps[2].expected == 'I should see the error "Account is blocked"'
    assert "blocked_user" in row.title and "negative" in row.tags
    assert len(report.converted) >= 4


def test_test_data_becomes_entries_and_secret_keys_become_named_references(v1_project: Path, tmp_path: Path) -> None:
    project = tmp_path / "app"

    report = import_v1(V1ImportOptions(source_dir=v1_project, project_dir=project))

    data = _cases(project)["valid-user-signs-in"].test_data
    assert data["username"] == "standard_user"
    assert data["search_term"] == "backpack"
    assert data["password"].model_dump() == {"secret": "PASSWORD"}
    assert "PASSWORD" in report.secret_names


def test_referenced_data_files_are_copied_and_attached(v1_project: Path, tmp_path: Path) -> None:
    project = tmp_path / "app"

    import_v1(V1ImportOptions(source_dir=v1_project, project_dir=project))

    copied = project / ".hercules" / "data" / "avatar.png"
    assert copied.read_bytes() == (v1_project / "opt" / "test_data" / "avatar.png").read_bytes()
    upload = _cases(project)["upload-an-avatar"]
    assert upload.test_data["avatar_file"] == ".hercules/data/avatar.png"
    assert "avatar_file" not in _cases(project)["valid-user-signs-in"].test_data


def test_environment_keeps_secret_names_only_and_drops_v1_engine_settings(v1_project: Path, tmp_path: Path) -> None:
    project = tmp_path / "app"

    report = import_v1(V1ImportOptions(source_dir=v1_project, project_dir=project))

    env = json.loads((project / ".hercules" / "envs" / "default.json").read_text())
    assert env == {"name": "default", "base_url": "http://localhost:3000", "secret_names": ["PASSWORD", "SHOP_ADMIN_PASSWORD"]}
    assert _cases(project)["valid-user-signs-in"].environment.ref == "default"
    text = _all_written_text(project)
    for value in FAKE_SECRET_VALUES:
        assert value not in text
    rendered = report.render()
    for value in FAKE_SECRET_VALUES:
        assert value not in rendered
    reasons = " ".join(item.reason for item in report.skipped)
    assert "LLM_MODEL_API_KEY" in reasons and "REGION" in reasons
    assert "fake-value" not in reasons


def test_unconvertible_scenarios_are_reported_with_a_reason(v1_project: Path, tmp_path: Path) -> None:
    report = import_v1(V1ImportOptions(source_dir=v1_project, project_dir=tmp_path / "app"))

    broken = [item for item in report.skipped if "broken.feature" in item.source]
    assert broken and "no steps" in broken[0].reason


def test_dry_run_writes_nothing_but_reports_the_plan(v1_project: Path, tmp_path: Path) -> None:
    project = tmp_path / "app"

    report = import_v1(V1ImportOptions(source_dir=v1_project, project_dir=project, dry_run=True))

    assert not project.exists()
    assert any(item.path.endswith("valid-user-signs-in.json") for item in report.converted)
    assert "dry run" in report.render().lower()


def test_existing_files_are_never_overwritten_without_force(v1_project: Path, tmp_path: Path) -> None:
    project = tmp_path / "app"
    target = project / ".hercules" / "cases" / "valid-user-signs-in.json"
    target.parent.mkdir(parents=True)
    target.write_text("mine")

    report = import_v1(V1ImportOptions(source_dir=v1_project, project_dir=project))

    assert target.read_text() == "mine"
    assert any("valid-user-signs-in.json" in item.source and "--force" in item.reason for item in report.skipped)

    import_v1(V1ImportOptions(source_dir=v1_project, project_dir=project, force=True))
    assert TestCase.model_validate_json(target.read_text()).id == "valid-user-signs-in"


def test_base_url_option_wins_and_missing_base_url_skips_the_environment(v1_project: Path, tmp_path: Path) -> None:
    (v1_project / ".env").write_text("REGION=eu\n")
    project = tmp_path / "app"
    shutil.rmtree(v1_project / "opt" / "input")
    (v1_project / "opt" / "input").mkdir()
    (v1_project / "opt" / "input" / "a.feature").write_text("Feature: a\n  Scenario: only\n    Given I click the button\n")

    report = import_v1(V1ImportOptions(source_dir=v1_project, project_dir=project))
    assert not (project / ".hercules" / "envs").exists()
    assert any("base URL" in item.reason for item in report.skipped)

    import_v1(V1ImportOptions(source_dir=v1_project, project_dir=project, base_url="https://staging.example.test", force=True))
    env = json.loads((project / ".hercules" / "envs" / "default.json").read_text())
    assert env["base_url"] == "https://staging.example.test"


def test_accepts_the_opt_folder_itself_and_rejects_a_folder_without_features(v1_project: Path, tmp_path: Path) -> None:
    report = import_v1(V1ImportOptions(source_dir=v1_project / "opt", project_dir=tmp_path / "a"))
    assert len([item for item in report.converted if item.kind == "case"]) == 4

    from testzeus_hercules.cases.importers.common import ImporterError

    with pytest.raises(ImporterError, match="no v1 input folder"):
        import_v1(V1ImportOptions(source_dir=tmp_path, project_dir=tmp_path / "b"))


def test_cli_import_v1_prints_the_report(v1_project: Path, tmp_path: Path) -> None:
    out = io.StringIO()
    project = tmp_path / "app"

    code = main(["import", "v1", str(v1_project), "--project", str(project), "--dry-run"], env={"HOME": str(tmp_path)}, stdout=out, stderr=out)

    assert code == 0, out.getvalue()
    text = out.getvalue()
    assert "valid-user-signs-in.json" in text and "Skipped" in text and "PASSWORD" in text
    assert not project.exists()


def test_cli_import_v1_missing_source_is_a_clear_error(tmp_path: Path) -> None:
    out = io.StringIO()

    code = main(["import", "v1", str(tmp_path / "nope"), "--project", str(tmp_path)], env={"HOME": str(tmp_path)}, stdout=out, stderr=out)

    assert code == 2
    assert "not a directory" in out.getvalue()


@pytest.mark.parametrize(
    "key",
    ["userPassword", "adminPassword", "dbpassword", "apiToken", "authToken", "accessToken", "clientSecret", "secretKey", "passphrase", "jwt", "otp", "totp", "API_KEY", "user-pass", "DB_PWD"],
)
def test_credential_keys_in_any_case_style_become_secret_references(key: str) -> None:
    from testzeus_hercules.cases.importers.common import looks_secret

    assert looks_secret(key)


@pytest.mark.parametrize("key", ["passport_number", "footprint", "username", "author", "search_term", "keyboard_layout", "spinner"])
def test_ordinary_keys_are_not_mistaken_for_credentials(key: str) -> None:
    from testzeus_hercules.cases.importers.common import looks_secret

    assert not looks_secret(key)


def test_camel_case_credential_keys_and_url_passwords_are_never_copied(v1_project: Path, tmp_path: Path) -> None:
    data_file = v1_project / "opt" / "test_data" / "test_data.json"
    data = json.loads(data_file.read_text())
    data |= {"userPassword": "fake-camel-pass", "apiToken": "fake-api-token", "otp": "123456", "dbUrl": "postgres://app:fake-url-pass@db.test/app", "footprint": "small"}
    data_file.write_text(json.dumps(data))
    project = tmp_path / "app"

    import_v1(V1ImportOptions(source_dir=v1_project, project_dir=project))

    written = _all_written_text(project)
    for value in ("fake-camel-pass", "fake-api-token", "123456", "fake-url-pass"):
        assert value not in written
    case = _cases(project)["valid-user-signs-in"]
    assert case.test_data["userPassword"].model_dump() == {"secret": "USERPASSWORD"}
    assert case.test_data["footprint"] == "small"


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("server.pem", b"-----BEGIN CERTIFICATE-----\nfake\n"),
        ("deploy_key", b"-----BEGIN OPENSSH PRIVATE KEY-----\nfake-key-material\n"),
        ("gcp.json", b'{"type": "service_account", "private_key": "fake-key-material", "nested": {"a": 1}}'),
        ("users.json", b'{"admin": {"name": "a", "password": "fake-key-material"}}'),
        ("secrets.yaml", b"key: fake-key-material\n"),
    ],
)
def test_credential_files_in_test_data_are_not_copied(v1_project: Path, tmp_path: Path, name: str, content: bytes) -> None:
    (v1_project / "opt" / "test_data" / name).write_bytes(content)
    project = tmp_path / "app"

    report = import_v1(V1ImportOptions(source_dir=v1_project, project_dir=project))

    assert not (project / ".hercules" / "data" / name).exists()
    assert "fake-key-material" not in _all_written_text(project)
    assert any(name in item.source and "credential file" in item.reason for item in report.skipped)
    assert (project / ".hercules" / "data" / "avatar.png").exists(), "ordinary data files are still copied"
