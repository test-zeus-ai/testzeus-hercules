"""The example cases and environments shipped with Hercules (``testzeus_hercules/seeds``): each one valid and runnable as
shipped, written into a project once, and never brought back or written over once the user has it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from testzeus_hercules.cases.gherkin import load_feature
from testzeus_hercules.cases.models import set_environment_names
from testzeus_hercules.daemon import cases as case_files
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.seeds import MARKER, SEED_ENV, seed_examples, shipped_examples
from tests.v2.fixtures.daemon_support import client_for, make_config

CASES = {
    "cases/Pre-Seeded/fill-all-fields-in-roboform-test-form.feature",
    "cases/Pre-Seeded/fill-minimum-fields-in-roboform-test-form.feature",
    "cases/Pre-Seeded/generate-an-authentication-token-using-valid-credentials.feature",
    "cases/Pre-Seeded/create-a-booking-and-verify-the-booking-details.feature",
}
ENVS = {"envs/pre-seeded-roboform.json", "envs/pre-seeded-restful-booker.json"}


@pytest.fixture()
def paths(tmp_path: Path) -> HerculesPaths:
    return make_config(tmp_path).paths


def test_the_shipped_examples_are_four_cases_and_their_two_environments() -> None:
    assert set(shipped_examples()) == CASES | ENVS


def test_every_example_case_parses_and_names_a_shipped_environment(paths: HerculesPaths) -> None:
    seed_examples(paths, env={})
    environments = {env.name: env for env in case_files.list_environments(paths.envs_dir)}
    assert {"pre-seeded-roboform", "pre-seeded-restful-booker"} <= set(environments)
    set_environment_names(case_files.environment_names(paths.envs_dir))  # as the daemon does: {{email}} is a variable of the environment
    try:
        parsed_cases = [(name, load_feature(paths.cases_dir / name.removeprefix("cases/"))) for name in sorted(CASES)]
    finally:
        set_environment_names(None)
    for name, parsed in parsed_cases:
        case = parsed[0] if isinstance(parsed, list) else parsed
        assert case.steps, name
        assert case.environment is not None and case.environment.ref in environments, f"{name} names an environment that is shipped"
    assert environments["pre-seeded-roboform"].base_url == "https://www.roboform.com"
    assert environments["pre-seeded-roboform"].variables == {"email": "alex.morgan@example.com"}


def test_the_examples_hold_sample_data_only() -> None:
    text = b"\n".join(shipped_examples().values()).decode().lower()
    for personal in ("shahal", "thayyil", "testzeus", "kochi", "kerala", "0484"):
        assert personal not in text, personal
    assert "@example.com" in text and "alex" in text


def test_examples_are_written_once(paths: HerculesPaths) -> None:
    assert set(seed_examples(paths, env={})) == CASES | ENVS
    assert json.loads((paths.project_hercules_dir / MARKER).read_text())["offered"] == sorted(CASES | ENVS)
    assert seed_examples(paths, env={}) == []


def test_an_edited_example_keeps_its_edits_and_a_deleted_one_stays_deleted(paths: HerculesPaths) -> None:
    seed_examples(paths, env={})
    edited = paths.cases_dir / "Pre-Seeded" / "fill-minimum-fields-in-roboform-test-form.feature"
    edited.write_text("mine", encoding="utf-8")
    (paths.envs_dir / "pre-seeded-restful-booker.json").unlink()
    assert seed_examples(paths, env={}) == []
    assert edited.read_text() == "mine"
    assert not (paths.envs_dir / "pre-seeded-restful-booker.json").exists()


def test_a_file_the_project_already_has_is_left_alone(paths: HerculesPaths) -> None:
    own = paths.envs_dir / "pre-seeded-roboform.json"
    own.write_text('{"name": "pre-seeded-roboform", "base_url": "https://my.example.com"}', encoding="utf-8")
    written = seed_examples(paths, env={})
    assert "envs/pre-seeded-roboform.json" not in written and len(written) == len(CASES | ENVS) - 1
    assert "my.example.com" in own.read_text()


def test_an_example_added_in_a_later_version_is_written(paths: HerculesPaths) -> None:
    seed_examples(paths, env={})
    marker = paths.project_hercules_dir / MARKER
    later = sorted((CASES | ENVS) - {"cases/Pre-Seeded/create-a-booking-and-verify-the-booking-details.feature"})
    marker.write_text(json.dumps({"offered": later}), encoding="utf-8")
    (paths.cases_dir / "Pre-Seeded" / "create-a-booking-and-verify-the-booking-details.feature").unlink()
    assert seed_examples(paths, env={}) == ["cases/Pre-Seeded/create-a-booking-and-verify-the-booking-details.feature"]


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_seeding_can_be_turned_off(paths: HerculesPaths, value: str) -> None:
    assert seed_examples(paths, env={SEED_ENV: value}) == []
    assert not (paths.project_hercules_dir / MARKER).exists()


def test_a_broken_marker_is_read_as_nothing_offered(paths: HerculesPaths) -> None:
    (paths.project_hercules_dir / MARKER).write_text("not json", encoding="utf-8")
    assert set(seed_examples(paths, env={})) == CASES | ENVS


def test_the_ui_lists_the_examples(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    seed_examples(config.paths, env={})
    with client_for(config) as client:
        cases = client.get("/api/cases").json()["cases"]
        environments = client.get("/api/environments").json()["environments"]
    seeded = [entry for entry in cases if entry["path"].startswith(".hercules/cases/Pre-Seeded/")]
    assert len(seeded) == len(CASES) and all(entry["valid"] for entry in seeded), seeded
    assert {"pre-seeded-roboform", "pre-seeded-restful-booker"} <= {env["name"] for env in environments}
