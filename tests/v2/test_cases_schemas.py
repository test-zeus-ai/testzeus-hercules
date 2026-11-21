"""The published JSON Schemas match the models and validate the documented examples."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from testzeus_hercules.cases import schemas
from testzeus_hercules.cases.models import RunResult
from testzeus_hercules.cases.models import TestCase as Case

from tests.v2.test_cases_models import SPEC_CASE, SPEC_RUN

SCHEMA_NAMES = ("test_case.schema.json", "run_result.schema.json", "plan.schema.json")


def _on_disk(name: str) -> dict[str, object]:
    return json.loads((schemas.SCHEMA_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_committed_schema_matches_models(name: str) -> None:
    expected = schemas.render_schema(schemas.build_json_schemas()[name])
    actual = (schemas.SCHEMA_DIR / name).read_text(encoding="utf-8")
    assert actual == expected, "schema drifted: run `python -m testzeus_hercules.cases.schemas` to regenerate"


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_schemas_are_valid_draft_2020_12(name: str) -> None:
    schema = _on_disk(name)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_export_writes_both_files(tmp_path: Path) -> None:
    written = schemas.export_json_schemas(tmp_path)
    assert sorted(path.name for path in written) == sorted(SCHEMA_NAMES)
    for path in written:
        assert path.read_text(encoding="utf-8") == (schemas.SCHEMA_DIR / path.name).read_text(encoding="utf-8")


def test_export_is_stable(tmp_path: Path) -> None:
    first = [path.read_bytes() for path in schemas.export_json_schemas(tmp_path / "a")]
    second = [path.read_bytes() for path in schemas.export_json_schemas(tmp_path / "b")]
    assert first == second


def test_spec_case_example_validates_against_schema() -> None:
    Draft202012Validator(_on_disk("test_case.schema.json")).validate(SPEC_CASE)
    Case.model_validate(SPEC_CASE)


def test_spec_run_example_validates_against_schema() -> None:
    Draft202012Validator(_on_disk("run_result.schema.json")).validate(SPEC_RUN)
    RunResult.model_validate(SPEC_RUN)


@pytest.mark.parametrize(
    "mutation",
    [
        {"id": "Not A Slug"},
        {"priority": "urgent"},
        {"steps": []},
        {"timeout_s": 5},
        {"test_data": {"password": {"secret": "lower"}}},
        {"test_data": {"bad key": "x"}},
        {"test_data": {"password": {"secret": "PW", "value": "hunter2"}}},
        {"environment": {"base_url": "ftp://x"}},
        {"environment": {"viewport": "big"}},
    ],
)
def test_schema_rejects_invalid_cases(mutation: dict[str, object]) -> None:
    body = copy.deepcopy(SPEC_CASE)
    body.update(mutation)
    assert list(Draft202012Validator(_on_disk("test_case.schema.json")).iter_errors(body))


def test_load_schema_helper() -> None:
    assert schemas.load_json_schema("test_case.schema.json") == _on_disk("test_case.schema.json")
    with pytest.raises(KeyError):
        schemas.load_json_schema("nope.json")
