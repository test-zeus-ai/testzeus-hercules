"""Offline tests for the OpenAPI -> Gherkin importer (no LLM calls)."""

import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest
import yaml
from testzeus_hercules.intelligence import openapi_importer
from testzeus_hercules.intelligence.openapi_importer import (
    build_prompt,
    generate_api_tests,
    generate_feature_text,
    main,
    read_spec,
    summarize_spec,
)
from testzeus_hercules.utils.gherkin_helper import split_feature_file

SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Pet Store", "version": "2.1.0"},
    "servers": [{"url": "https://api.example.com/v1"}],
    "paths": {
        "/users": {
            "parameters": [{"name": "trace_id", "in": "header", "schema": {"type": "string"}}],
            "get": {
                "operationId": "list_users",
                "summary": "List users",
                "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
            },
            "post": {
                "operationId": "create_user",
                "summary": "Create a user",
                "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
            },
        },
        "/users/{user_id}": {"get": {"operationId": "get_user", "summary": "Get one user"}},
    },
}

FAKE_GHERKIN = """Feature: User management
  Scenario: Create user successfully
    Given the service is available
    When I send a POST request to "/users" with body {"name": "<name>"}
    Then the response status is 201
    And the response body contains a "user_id"

  Scenario: Create user with invalid body
    Given the service is available
    When I send a POST request to "/users" with body {"name": ""}
    Then the response status is 400
"""


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> SimpleNamespace:
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.content)


def test_read_spec_yaml_and_json(tmp_path) -> None:
    yaml_file = tmp_path / "spec.yaml"
    yaml_file.write_text(yaml.safe_dump(SPEC), encoding="utf-8")
    assert read_spec(str(yaml_file)) == SPEC

    json_file = tmp_path / "spec.json"
    json_file.write_text(json.dumps(SPEC), encoding="utf-8")
    assert read_spec(str(json_file)) == SPEC


def test_read_spec_invalid_raises_value_error(tmp_path) -> None:
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("key: [unclosed", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.yaml"):
        read_spec(str(bad_yaml))

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.json"):
        read_spec(str(bad_json))


def test_read_spec_handles_utf8_bom(tmp_path) -> None:
    json_file = tmp_path / "bom.json"
    json_file.write_text(json.dumps(SPEC), encoding="utf-8-sig")
    assert read_spec(str(json_file)) == SPEC

    yaml_file = tmp_path / "bom.yaml"
    yaml_file.write_text(yaml.safe_dump(SPEC), encoding="utf-8-sig")
    assert read_spec(str(yaml_file)) == SPEC


def test_read_spec_requires_openapi_or_swagger_key(tmp_path) -> None:
    plain_yaml = tmp_path / "plain.yaml"
    plain_yaml.write_text("title: just some yaml\n", encoding="utf-8")
    with pytest.raises(ValueError, match="'openapi' or 'swagger'"):
        read_spec(str(plain_yaml))

    plain_json = tmp_path / "plain.json"
    plain_json.write_text(json.dumps({"info": {"title": "not a spec"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="'openapi' or 'swagger'"):
        read_spec(str(plain_json))


def test_read_spec_deeply_nested_document_raises_value_error(tmp_path) -> None:
    """Absurdly nested YAML/JSON documents raise the documented ValueError, not RecursionError (fuzz finding 7)."""
    deep_yaml = tmp_path / "deep.yaml"
    deep_yaml.write_text("openapi: 3.0.0\nnested: " + "[" * 500 + "]" * 500 + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="deep.yaml"):
        read_spec(str(deep_yaml))

    deep_json = tmp_path / "deep.json"
    deep_json.write_text('{"openapi": "3.0.0", "nested": ' + "[" * 100000 + "]" * 100000 + "}", encoding="utf-8")
    with pytest.raises(ValueError, match="deep.json"):
        read_spec(str(deep_json))


def test_summarize_spec_openapi3() -> None:
    summary = summarize_spec(SPEC)

    assert summary["title"] == "Pet Store"
    assert summary["version"] == "2.1.0"
    assert summary["base_url"] == "https://api.example.com/v1"
    assert len(summary["endpoints"]) == 3

    by_id = {endpoint["operation_id"]: endpoint for endpoint in summary["endpoints"]}
    # Non-HTTP path-item keys (e.g. path-level "parameters") must be skipped.
    assert {endpoint["method"] for endpoint in summary["endpoints"]} == {"GET", "POST"}

    create = by_id["create_user"]
    assert create["method"] == "POST"
    assert create["path"] == "/users"
    assert create["request_body_schema"] == {"application/json": {"schema": {"type": "object"}}}

    list_users = by_id["list_users"]
    assert list_users["parameters"] == [{"name": "limit", "in": "query", "schema": {"type": "integer"}}]
    assert list_users["request_body_schema"] == {}


def test_summarize_spec_swagger2_host_fallback() -> None:
    swagger2 = {
        "swagger": "2.0",
        "info": {"title": "Legacy API", "version": "1.0.0"},
        "host": "legacy.example.com",
        "basePath": "/v2",
        "schemes": ["https"],
        "paths": {"/pets": {"get": {"operationId": "list_pets"}}},
    }

    summary = summarize_spec(swagger2)

    assert summary["base_url"] == "https://legacy.example.com/v2"
    assert [endpoint["path"] for endpoint in summary["endpoints"]] == ["/pets"]


def test_summarize_spec_resolves_local_refs() -> None:
    """Path-item $ref endpoints are imported and schema $refs reach the prompt resolved (F10)."""
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Ref API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/pets": {"$ref": "#/components/pathItems/petItem"},
            "/owners": {
                "post": {
                    "operationId": "create_owner",
                    "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Owner"}}}},
                }
            },
        },
        "components": {
            "pathItems": {"petItem": {"get": {"operationId": "list_pets", "summary": "List pets"}}},
            "schemas": {
                "Owner": {"type": "object", "properties": {"pet": {"$ref": "#/components/schemas/Pet"}}},
                "Pet": {"type": "object", "properties": {"self": {"$ref": "#/components/schemas/Pet"}, "name": {"type": "string"}}},
            },
        },
    }

    summary = summarize_spec(spec)

    assert {(endpoint["method"], endpoint["path"]) for endpoint in summary["endpoints"]} == {("GET", "/pets"), ("POST", "/owners")}

    create = next(endpoint for endpoint in summary["endpoints"] if endpoint["operation_id"] == "create_owner")
    schema = create["request_body_schema"]["application/json"]["schema"]
    # the Owner $ref was resolved to the Pet schema (with its cyclic self-ref kept as a $ref node)
    assert schema["type"] == "object"
    assert schema["properties"]["pet"]["properties"]["name"] == {"type": "string"}
    assert schema["properties"]["pet"]["properties"]["self"] == {"$ref": "#/components/schemas/Pet"}
    # the input spec is not mutated
    assert spec["components"]["schemas"]["Owner"]["properties"]["pet"] == {"$ref": "#/components/schemas/Pet"}


def test_summarize_spec_leaves_external_and_unresolvable_refs_as_is() -> None:
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/x": {"get": {"parameters": [{"schema": {"$ref": "https://elsewhere/spec.yaml#/A"}}]}},
            "/gone": {"$ref": "#/components/missing"},
        },
    }

    summary = summarize_spec(spec)

    by_path = {endpoint["path"]: endpoint for endpoint in summary["endpoints"]}
    assert by_path["/x"]["parameters"][0]["schema"] == {"$ref": "https://elsewhere/spec.yaml#/A"}
    assert "/gone" not in by_path  # unresolvable path-item ref simply yields no endpoints


def test_summarize_spec_shared_aliased_schema_resolves_fast() -> None:
    """A shared/aliased node DAG (9-fanout x 7-depth) resolves quickly via memoization (fuzz finding 5)."""
    layer = [{"type": "object", "properties": {"leaf": {"type": "string"}}} for _ in range(9)]
    for _ in range(7):
        layer = [{"type": "object", "properties": {f"p{index}": child for index, child in enumerate(layer)}} for _ in range(9)]
    schema = layer[0]
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "DAG", "version": "1.0.0"},
        "paths": {
            "/thing": {
                "post": {
                    "operationId": "create_thing",
                    "requestBody": {"content": {"application/json": {"schema": schema}}},
                }
            }
        },
    }

    start = time.perf_counter()
    summary = summarize_spec(spec)
    elapsed = time.perf_counter() - start

    resolved = summary["endpoints"][0]["request_body_schema"]["application/json"]["schema"]
    assert resolved["type"] == "object"
    # the deepest level (the shared leaf nodes) survives resolution intact
    node = resolved
    for _ in range(7):
        node = node["properties"]["p0"]
    assert node["properties"]["leaf"] == {"type": "string"}
    # the input spec is not mutated (the aliased subtrees keep their shared identity)
    assert spec["paths"]["/thing"]["post"]["requestBody"]["content"]["application/json"]["schema"] is schema
    original = spec["paths"]["/thing"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    for _ in range(7):
        original = original["properties"]["p0"]
    assert original["properties"]["leaf"] == {"type": "string"}
    assert elapsed < 0.5


def test_summarize_spec_resolves_repeated_refs_to_one_target() -> None:
    """Many $ref nodes pointing at one schema all resolve while the target is walked only once."""
    target = {"type": "object", "properties": {"name": {"type": "string"}}}
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/x": {
                "post": {
                    "operationId": "create_x",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {f"field{index}": {"$ref": "#/components/schemas/T"} for index in range(50)}}}}},
                }
            }
        },
        "components": {"schemas": {"T": target}},
    }

    summary = summarize_spec(spec)

    schema = summary["endpoints"][0]["request_body_schema"]["application/json"]["schema"]
    assert schema["properties"]["field0"]["type"] == "object"
    assert schema["properties"]["field49"]["properties"]["name"] == {"type": "string"}
    # the input spec is not mutated
    assert spec["components"]["schemas"]["T"] is target
    assert spec["components"]["schemas"]["T"]["type"] == "object"


def test_summarize_spec_skips_non_string_paths_and_methods() -> None:
    """Non-string path keys and non-string method keys are skipped defensively (fuzz finding 6)."""
    spec = {
        "openapi": "3.0.0",
        "paths": {
            7: {"get": {"operationId": "int_path"}},
            None: {"get": {"operationId": "none_path"}},
            "/good": {"get": {"operationId": "ok"}, None: {"operationId": "none_method"}, 5: {"operationId": "int_method"}},
        },
    }

    summary = summarize_spec(spec)

    assert [(endpoint["path"], endpoint["method"], endpoint["operation_id"]) for endpoint in summary["endpoints"]] == [("/good", "GET", "ok")]


def test_summarize_spec_merges_path_item_parameters_into_summary() -> None:
    """Path-item level parameters (e.g. required path params) reach the endpoint summary text (backlog P3)."""
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Params", "version": "1.0"},
        "paths": {
            "/users/{user_id}": {
                "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "get": {
                    "operationId": "get_user",
                    "summary": "Get one user",
                    "parameters": [{"name": "verbose", "in": "query", "schema": {"type": "boolean"}}],
                },
                "delete": {"operationId": "delete_user"},  # no operation-level parameters at all
            }
        },
    }

    summary = summarize_spec(spec)
    by_id = {endpoint["operation_id"]: endpoint for endpoint in summary["endpoints"]}

    get_summary = by_id["get_user"]["summary"]
    assert "Get one user" in get_summary
    assert "user_id" in get_summary  # path-item param surfaced
    assert "verbose" in get_summary  # operation param still there
    assert "user_id" in by_id["delete_user"]["summary"]  # inherited by every operation on the path
    # The endpoint's `parameters` key stays operation-level (path-item params are not duplicated there).
    assert by_id["get_user"]["parameters"] == [{"name": "verbose", "in": "query", "schema": {"type": "boolean"}}]
    assert by_id["delete_user"]["parameters"] == []


def test_summarize_spec_operation_parameters_win_collision_in_summary() -> None:
    """On a (name, in) collision the operation-level parameter wins in the merged summary text."""
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/items": {
                "parameters": [{"name": "limit", "in": "query", "required": True, "schema": {"type": "integer"}}],
                "get": {
                    "operationId": "list_items",
                    "parameters": [{"name": "limit", "in": "query", "required": False, "schema": {"type": "integer"}}],
                },
            }
        },
    }

    summary = summarize_spec(spec)
    endpoint = summary["endpoints"][0]

    assert endpoint["summary"].count("limit") == 1  # collision collapsed to one mention
    assert "(query)" in endpoint["summary"]  # operation-level wins: no longer required
    assert "required" not in endpoint["summary"]


def test_build_prompt_caps_oversized_request_body_schema() -> None:
    """A request-body schema with a 50KB property is truncated to the per-endpoint char cap (backlog P3)."""
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Big", "version": "1.0"},
        "paths": {
            "/big": {
                "post": {
                    "operationId": "create_big",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"blob": {"type": "string", "description": "x" * 50_000}}}}}},
                }
            }
        },
    }

    summary = summarize_spec(spec)
    # summarize_spec keeps the schema structured (resolvers/callers rely on that)
    assert summary["endpoints"][0]["request_body_schema"]["application/json"]["schema"]["type"] == "object"

    prompt = build_prompt(summary, 5)
    marker_bound = openapi_importer._MAX_SCHEMA_CHARS + len(openapi_importer._SCHEMA_TRUNCATION_MARKER)
    assert openapi_importer._SCHEMA_TRUNCATION_MARKER in prompt
    assert "blob" in prompt  # the useful head of the schema is preserved
    assert len(prompt) < marker_bound + 4000  # the total prompt stays bounded


def test_build_prompt_contains_endpoints_and_target() -> None:
    prompt = build_prompt(summarize_spec(SPEC), 42)

    assert "/users/{user_id}" in prompt
    assert "https://api.example.com/v1" in prompt
    assert "42" in prompt
    assert "Gherkin" in prompt


def test_generate_api_tests_writes_split_scenario_files(tmp_path) -> None:
    spec_file = tmp_path / "api_spec.yml"
    spec_file.write_text(yaml.safe_dump(SPEC), encoding="utf-8")
    out_dir = tmp_path / "out"
    llm = FakeLLM(FAKE_GHERKIN)

    written = asyncio.run(generate_api_tests([str(spec_file)], str(out_dir), number_of_testcase=17, llm=llm))

    assert len(written) == 2
    # The intermediate combined file must not linger in the output subfolder.
    subfolder = out_dir / "api_spec"
    assert sorted(os.listdir(subfolder)) == sorted(os.path.basename(path) for path in written)
    # The LLM prompt was built from the spec summary.
    assert len(llm.prompts) == 1
    assert "/users/{user_id}" in llm.prompts[0]
    assert "17" in llm.prompts[0]

    total_scenarios = 0
    for path in written:
        content = open(path, encoding="utf-8").read()
        assert "Feature:" in content
        assert "Scenario:" in content
        scenarios = asyncio.run(split_feature_file(path, str(tmp_path / "split")))
        total_scenarios += len(scenarios)
    assert total_scenarios == 2


def test_generate_feature_text_accepts_plain_string_response(tmp_path) -> None:
    """LLM doubles returning a plain string (no .content) must work (F11)."""
    spec_file = tmp_path / "spec.yml"
    spec_file.write_text(yaml.safe_dump(SPEC), encoding="utf-8")

    class PlainStringLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def ainvoke(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return FAKE_GHERKIN

    llm = PlainStringLLM()

    text = asyncio.run(generate_feature_text(str(spec_file), number_of_testcase=5, llm=llm))

    assert text.startswith("Feature: User management")
    assert "Scenario: Create user successfully" in text
    assert len(llm.prompts) == 1


def test_main_parses_args_and_prints_written_files(monkeypatch, tmp_path, capsys) -> None:
    captured: dict = {}

    async def fake_generate_api_tests(input_files, output_dir, number_of_testcase=20, llm=None):
        captured["input_files"] = input_files
        captured["output_dir"] = output_dir
        captured["number_of_testcase"] = number_of_testcase
        os.makedirs(os.path.join(output_dir, "spec"), exist_ok=True)
        written_path = os.path.join(output_dir, "spec", "a.feature")
        with open(written_path, "w", encoding="utf-8") as f:
            f.write("Feature: x\n  Scenario: y\n")
        return [written_path]

    monkeypatch.setattr(openapi_importer, "generate_api_tests", fake_generate_api_tests)

    out_dir = tmp_path / "out"
    exit_code = main([str(tmp_path / "spec.yml"), "--output", str(out_dir), "--number_of_testcase", "7"])

    assert exit_code == 0
    assert captured == {
        "input_files": [str(tmp_path / "spec.yml")],
        "output_dir": str(out_dir),
        "number_of_testcase": 7,
    }
    assert "a.feature" in capsys.readouterr().out


def test_main_returns_one_on_failure(tmp_path, capsys) -> None:
    # Missing spec file: read_spec fails before any LLM setup.
    exit_code = main([str(tmp_path / "missing.yml"), "--output", str(tmp_path / "out")])

    assert exit_code == 1
    assert "Error" in capsys.readouterr().out
