"""Schema checks (``act.api_schema``): JSON Schema and OpenAPI response validation, ported from the v1 contract tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.act.api_schema import SpecError, load_document, match_template, pointer, validate, validate_operation

PET = {"id": 1, "name": "Rex"}

SPEC_20: dict[str, Any] = {
    "swagger": "2.0",
    "info": {"title": "pets", "version": "1.0"},
    "paths": {"/pets": {"get": {"responses": {"200": {"description": "ok", "schema": {"$ref": "#/definitions/Pets"}}, "400": {"description": "bad request"}}}}},
    "definitions": {
        "Pets": {"type": "array", "items": {"$ref": "#/definitions/Pet"}},
        "Pet": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}},
    },
}

SPEC_30: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "pets", "version": "1.0"},
    "servers": [{"url": "https://api.example.test/v1"}],
    "paths": {
        "/pets": {"get": {"responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pets"}}}}}}},
        "/pets/{petId}": {"get": {"responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}}}}}},
        "/pets/mine": {"get": {"responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "string"}}}}}}},
    },
    "components": {
        "schemas": {
            "Pets": {"type": "array", "items": {"$ref": "#/components/schemas/Pet"}},
            "Pet": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}, "name": {"type": "string", "nullable": True}}},
        }
    },
}


# ── OpenAPI operations ───────────────────────────────────────────────────────


def test_swagger_20_conforming_and_violating_responses() -> None:
    assert validate_operation([PET], SPEC_20, "/pets", "get", 200)["assertion"] == "PASSED"
    failed = validate_operation([{"name": "no id"}, {"id": "x"}], SPEC_20, "/pets", "GET", 200)
    assert failed["assertion"] == "FAILED" and failed["violation_count"] == 2
    assert {item["path"] for item in failed["violations"]} == {"$[0]", "$[1].id"}


def test_openapi_30_with_nullable_and_refs() -> None:
    assert validate_operation([{"id": 1, "name": None}], SPEC_30, "/pets", "get", 200)["assertion"] == "PASSED"
    assert validate_operation([{"id": "1"}], SPEC_30, "/pets", "get", 200)["assertion"] == "FAILED"


@pytest.mark.parametrize(
    ("template", "method", "status", "fragment"),
    [("/cats", "get", 200, "declared paths include: /pets"), ("/pets", "post", 200, "declared methods: GET"), ("/pets", "get", 404, "declared: 200, 400"), ("/pets", "get", 400, "no response schema")],
)
def test_unknown_operation_parts_are_errors_naming_what_is_declared(template: str, method: str, status: int, fragment: str) -> None:
    verdict = validate_operation([PET], SPEC_20, template, method, status)
    assert verdict["assertion"] == "ERROR" and fragment in verdict["reason"]
    assert not verdict["reason"].startswith("'")  # not a quoted KeyError text


def test_range_and_default_status_keys() -> None:
    spec = copy.deepcopy(SPEC_20)
    responses = spec["paths"]["/pets"]["get"]["responses"]
    responses["2XX"] = responses.pop("200")
    responses["default"] = {"description": "fallback", "schema": {"type": "string"}}
    assert validate_operation([PET], spec, "/pets", "get", 201)["assertion"] == "PASSED"
    assert validate_operation([{"name": "no id"}], spec, "/pets", "get", "201")["assertion"] == "FAILED"
    assert validate_operation("oops", spec, "/pets", "get", 500)["assertion"] == "PASSED"  # default: a string


def test_exact_status_key_wins_and_yaml_int_keys_match(tmp_path: Path) -> None:
    spec = copy.deepcopy(SPEC_20)
    spec["paths"]["/pets"]["get"]["responses"]["2XX"] = {"description": "any", "schema": {"type": "string"}}
    assert validate_operation([PET], spec, "/pets", "get", 200)["assertion"] == "PASSED"
    yaml_spec = tmp_path / "pets.yaml"
    yaml_spec.write_text("swagger: '2.0'\npaths:\n  /pets:\n    get:\n      responses:\n        200:\n          schema: {type: array}\n")
    loaded = load_document(yaml_spec)
    assert validate_operation([PET], loaded, "/pets", "get", "200")["assertion"] == "PASSED"
    assert "declared: 200" in validate_operation([], loaded, "/pets", "get", 404)["reason"]


def test_unsupported_version_is_an_error() -> None:
    verdict = validate_operation([PET], {"openapi": "4.0.0", "paths": {"/pets": {"get": {"responses": {"200": {"schema": {}}}}}}}, "/pets", "get", 200)
    assert verdict["assertion"] == "ERROR"


def test_response_path_selects_the_instance() -> None:
    assert validate_operation({"data": [PET]}, SPEC_30, "/pets", "get", 200, path="$.data")["assertion"] == "PASSED"
    assert validate_operation({"data": [PET]}, SPEC_30, "/pets", "get", 200, path="$.nope")["assertion"] == "FAILED"


def test_templates_match_concrete_paths_with_or_without_the_server_prefix() -> None:
    assert match_template(SPEC_30, "/v1/pets/7") == "/pets/{petId}"
    assert match_template(SPEC_30, "/pets/7") == "/pets/{petId}"
    assert match_template(SPEC_30, "/v1/pets/mine") == "/pets/mine"  # a literal path wins over a template
    assert match_template(SPEC_30, "/v1/pets") == "/pets"
    assert match_template(SPEC_30, "/v1/owners/7") is None


# ── JSON Schema ──────────────────────────────────────────────────────────────


def test_inline_schema_validates_and_rejects_invalid_schemas() -> None:
    schema = {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}}
    assert validate(PET, schema)["assertion"] == "PASSED"
    failed = validate({"id": "x"}, schema)
    assert failed["assertion"] == "FAILED" and failed["violations"][0]["path"] == "$.id"
    invalid = validate(PET, {"type": "nope"})
    assert invalid["assertion"] == "ERROR" and "Invalid JSON Schema" in invalid["reason"]


def test_schema_inside_a_spec_resolves_local_refs() -> None:
    assert validate([PET], pointer(SPEC_30, "#/components/schemas/Pets"), root=SPEC_30)["assertion"] == "PASSED"
    assert validate({"id": "x"}, pointer(SPEC_30, "/components/schemas/Pet"), root=SPEC_30)["assertion"] == "FAILED"
    with pytest.raises(SpecError, match="does not exist"):
        pointer(SPEC_30, "#/components/schemas/Cat")


@pytest.mark.parametrize("ref", ["http://127.0.0.1:1/x.json", "file:///etc/passwd", "other.json#/X"])
def test_remote_and_file_refs_are_refused_never_fetched(ref: str) -> None:
    verdict = validate({"a": 1}, {"type": "object", "properties": {"a": {"$ref": ref}}})
    assert verdict["assertion"] == "ERROR" and "not allowed" in verdict["reason"]


def test_load_document_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("openapi: [unclosed\n  - x")
    with pytest.raises(SpecError, match="invalid YAML") as caught:
        load_document(bad)
    assert "\n" not in str(caught.value)
    listed = tmp_path / "list.json"
    listed.write_text(json.dumps([1]))
    with pytest.raises(SpecError, match="mapping"):
        load_document(listed)
    with pytest.raises(SpecError, match="not found"):
        load_document(tmp_path / "missing.yaml")
