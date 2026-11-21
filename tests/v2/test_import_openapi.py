"""``hercules import openapi``: one API TestCase per operation, with example data, expected status and schema reference."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from testzeus_hercules.cases.importers.common import ImporterError
from testzeus_hercules.cases.importers.openapi import OpenAPIImportOptions, import_openapi
from testzeus_hercules.cases.models import TestCase
from testzeus_hercules.cli.main import main

SPEC = Path(__file__).parent / "fixtures" / "import" / "petstore.yaml"


def _cases(project: Path) -> dict[str, TestCase]:
    return {path.stem: TestCase.model_validate_json(path.read_text()) for path in sorted((project / ".hercules" / "cases").glob("*.json"))}


def test_one_case_per_operation_with_status_and_schema(tmp_path: Path) -> None:
    report = import_openapi(OpenAPIImportOptions(spec_path=SPEC, project_dir=tmp_path))

    cases = _cases(tmp_path)
    assert sorted(cases) == ["create-pet", "get-health", "list-pets", "show-pet-by-id"]
    show = cases["show-pet-by-id"]
    assert len(show.steps) == 1
    step = show.steps[0]
    assert step.action == 'I send a GET request to "/pets/{{petId}}"'
    assert step.expected == 'the response status should be 200 and the response body should match the schema "petstore.yaml#/components/schemas/Pet"'
    assert step.api is not None and step.api.method == "GET" and step.api.path == "/pets/{{petId}}"
    assert step.api.expect is not None and step.api.expect.status == 200 and step.api.expect.json_schema == "petstore.yaml#/components/schemas/Pet"
    assert show.test_data["petId"] == "42"
    assert "kind:api" in show.tags and "openapi" in show.tags and "pets" in show.tags
    assert show.environment.base_url == "https://petstore.example.test/v1"
    assert len([item for item in report.converted if item.kind == "case"]) == 4
    assert [item.path for item in report.converted if item.kind == "data"] == [".hercules/specs/petstore.yaml"]
    assert (tmp_path / ".hercules" / "specs" / "petstore.yaml").read_bytes() == SPEC.read_bytes()


def test_query_params_and_json_body_use_examples_or_placeholders(tmp_path: Path) -> None:
    import_openapi(OpenAPIImportOptions(spec_path=SPEC, project_dir=tmp_path))

    cases = _cases(tmp_path)
    listing = cases["list-pets"].steps[0]
    assert listing.action == 'I send a GET request to "/pets?limit=10"'
    assert listing.api is not None and listing.api.path == "/pets" and listing.api.query == {"limit": "10"}
    create = cases["create-pet"].steps[0]
    assert create.api is not None and create.api.method == "POST" and create.api.body == {"name": "string", "tag": "dog"}
    assert create.api.expect is not None and create.api.expect.status == 201
    assert create.action == 'I send a POST request to "/pets" with a JSON body'


def test_security_scheme_becomes_a_secret_reference_by_name(tmp_path: Path) -> None:
    report = import_openapi(OpenAPIImportOptions(spec_path=SPEC, project_dir=tmp_path))

    create = _cases(tmp_path)["create-pet"]
    assert create.test_data["api_key"].model_dump() == {"secret": "API_KEY"}
    api = create.steps[0].api
    assert api is not None and api.headers == {"X-API-Key": "{{api_key}}"}
    assert "API_KEY" in report.secret_names


def test_operation_without_2xx_body_has_no_schema_step(tmp_path: Path) -> None:
    import_openapi(OpenAPIImportOptions(spec_path=SPEC, project_dir=tmp_path))

    health = _cases(tmp_path)["get-health"]
    assert [step.expected for step in health.steps if step.expected] == ["the response status should be 204"]
    api = health.steps[0].api
    assert api is not None and api.expect is not None and api.expect.status == 204 and api.expect.json_schema is None


def test_tag_filter_and_base_url_override(tmp_path: Path) -> None:
    import_openapi(OpenAPIImportOptions(spec_path=SPEC, project_dir=tmp_path, tags=("ops",), base_url="http://localhost:8080/api"))

    cases = _cases(tmp_path)
    assert sorted(cases) == ["get-health"]
    assert cases["get-health"].environment.base_url == "http://localhost:8080/api"


def test_json_specs_and_conflicts(tmp_path: Path) -> None:
    import yaml

    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(yaml.safe_load(SPEC.read_text())))
    project = tmp_path / "p"
    import_openapi(OpenAPIImportOptions(spec_path=spec, project_dir=project))
    target = project / ".hercules" / "cases" / "list-pets.json"
    target.write_text("mine")

    report = import_openapi(OpenAPIImportOptions(spec_path=spec, project_dir=project))

    assert target.read_text() == "mine"
    assert any("list-pets.json" in item.source and "--force" in item.reason for item in report.skipped)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("swagger: '2.0'\npaths: {}\n", "OpenAPI 3"),
        ("openapi: 3.0.0\n", "no paths"),
        ("- just a list\n", "mapping"),
        ("openapi: [unclosed\n", "cannot parse"),
    ],
)
def test_unsupported_specs_are_rejected(tmp_path: Path, content: str, message: str) -> None:
    spec = tmp_path / "spec.yaml"
    spec.write_text(content)

    with pytest.raises(ImporterError, match=message):
        import_openapi(OpenAPIImportOptions(spec_path=spec, project_dir=tmp_path))


def test_recursive_and_external_refs_do_not_loop(tmp_path: Path) -> None:
    spec = tmp_path / "loop.yaml"
    spec.write_text("""\
openapi: 3.1.0
info: {title: t, version: "1"}
components:
  schemas:
    Node:
      type: object
      properties:
        child: {$ref: "#/components/schemas/Node"}
        other: {$ref: "other.yaml#/X"}
paths:
  /nodes:
    post:
      requestBody:
        content:
          application/json:
            schema: {$ref: "#/components/schemas/Node"}
      responses:
        "200": {description: ok}
""")

    report = import_openapi(OpenAPIImportOptions(spec_path=spec, project_dir=tmp_path, base_url="http://localhost:1"))

    case = _cases(tmp_path)["post-nodes"]
    assert case.steps[0].api is not None and isinstance(case.steps[0].api.body, dict)
    assert any("external" in note for note in report.notes)


def test_cli_import_openapi(tmp_path: Path) -> None:
    out = io.StringIO()

    code = main(["import", "openapi", str(SPEC), "--project", str(tmp_path), "--tag", "pets", "--base-url", "http://localhost:9000"], env={"HOME": str(tmp_path)}, stdout=out, stderr=out)

    assert code == 0, out.getvalue()
    assert sorted(_cases(tmp_path)) == ["create-pet", "list-pets", "show-pet-by-id"]
    assert "API_KEY" in out.getvalue()


def test_auth_schemes_map_to_headers_query_or_auth(tmp_path: Path) -> None:
    spec = tmp_path / "auth.yaml"
    spec.write_text("""\
openapi: 3.0.0
info: {title: t, version: "1"}
components:
  securitySchemes:
    q: {type: apiKey, in: query, name: key}
    c: {type: apiKey, in: cookie, name: sid}
    b: {type: http, scheme: basic}
    t: {type: http, scheme: bearer}
paths:
  /q: {get: {security: [{q: []}], responses: {"200": {description: ok}}}}
  /c: {get: {security: [{c: []}], responses: {"200": {description: ok}}}}
  /b: {get: {security: [{b: []}], responses: {"200": {description: ok}}}}
  /t: {get: {security: [{t: []}], responses: {"200": {description: ok}}}}
  /e: {post: {requestBody: {content: {application/json: {example: {note: "{{not a placeholder}}"}}}}, responses: {"201": {description: ok}}}}
""")
    import_openapi(OpenAPIImportOptions(spec_path=spec, project_dir=tmp_path, base_url="http://localhost:1"))

    apis = {name: case.steps[0].api for name, case in _cases(tmp_path).items()}
    assert apis["get-q"].query == {"key": "{{q}}"}  # type: ignore[union-attr]
    assert apis["get-c"].headers == {"Cookie": "sid={{c}}"}  # type: ignore[union-attr]
    assert apis["get-b"].auth.model_dump() == {"type": "basic", "value": "{{b}}"}  # type: ignore[union-attr]
    assert apis["get-t"].auth.model_dump() == {"type": "bearer", "value": "{{t}}"}  # type: ignore[union-attr]
    assert apis["post-e"].body == {"note": "{ {not a placeholder} }"}  # type: ignore[union-attr]


def test_malformed_parts_skip_one_operation_with_a_note_not_a_traceback(tmp_path: Path) -> None:
    spec = tmp_path / "bad.yaml"
    spec.write_text("""\
openapi: 3.0.0
info: {title: t, version: "1"}
servers: [{url: "https://{region}.api.test", variables: {region: "eu"}}]
components: [not, a, mapping]
security: [{key: []}]
paths:
  /a: {get: {tags: null, responses: {"200": {description: ok}}}}
  /b/{n}: {get: {parameters: [{name: n, in: path, required: true, schema: {type: integer, minimum: lots}}], responses: {"200": {description: ok}}}}
  /c: {get: {tags: [x, 1, true], responses: {"200": {description: ok}}}}
""")

    report = import_openapi(OpenAPIImportOptions(spec_path=spec, project_dir=tmp_path))

    assert sorted(_cases(tmp_path)) == ["get-a", "get-c"]
    assert any(item.source == "GET /b/{n}" and "could not be read" in item.reason for item in report.skipped)
    assert _cases(tmp_path)["get-a"].environment.base_url == "https://{region}.api.test", "a non-mapping server variable stays as written"


def test_a_security_scheme_named_like_a_path_parameter_does_not_replace_its_value(tmp_path: Path) -> None:
    spec = tmp_path / "clash.yaml"
    spec.write_text("""\
openapi: 3.0.0
info: {title: t, version: "1"}
components:
  securitySchemes:
    apikey: {type: apiKey, in: header, name: X-Key}
paths:
  /keys/{apikey}/{pet-id}/{pet_id}:
    get:
      security: [{apikey: []}]
      parameters:
        - {name: apikey, in: path, required: true, example: k1}
        - {name: pet-id, in: path, required: true, example: p1}
        - {name: pet_id, in: path, required: true, example: p2}
      responses: {"200": {description: ok}}
""")

    import_openapi(OpenAPIImportOptions(spec_path=spec, project_dir=tmp_path, base_url="http://localhost:1"))

    case = next(iter(_cases(tmp_path).values()))
    api = case.steps[0].api
    assert api is not None and api.path == "/keys/{{apikey}}/{{pet_id}}/{{pet_id_2}}"
    assert case.test_data["apikey"] == "k1" and case.test_data["pet_id"] == "p1" and case.test_data["pet_id_2"] == "p2"
    assert api.headers == {"X-Key": "{{apikey_2}}"}
    assert case.test_data["apikey_2"].model_dump() == {"secret": "APIKEY"}


def test_references_are_percent_decoded_and_wide_schemas_stop_at_a_budget(tmp_path: Path) -> None:
    properties = {f"p{index}": {"type": "object", "properties": {f"q{inner}": {"type": "string"} for inner in range(200)}} for index in range(200)}
    spec = tmp_path / "wide.json"
    spec.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "t", "version": "1"},
                "components": {"schemas": {"A B": {"type": "object", "properties": {"name": {"type": "string", "example": "found"}}}, "Wide": {"type": "object", "properties": properties}}},
                "paths": {
                    "/a": {"post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/A%20B"}}}}, "responses": {"200": {"description": "ok"}}}},
                    "/w": {"post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Wide"}}}}, "responses": {"200": {"description": "ok"}}}},
                },
            }
        )
    )

    import_openapi(OpenAPIImportOptions(spec_path=spec, project_dir=tmp_path, base_url="http://localhost:1"))

    cases = _cases(tmp_path)
    assert cases["post-a"].steps[0].api.body == {"name": "found"}  # type: ignore[union-attr]
    wide = cases["post-w"].steps[0].api.body  # type: ignore[union-attr]
    assert isinstance(wide, dict) and sum(len(value) for value in wide.values()) < 2_000
