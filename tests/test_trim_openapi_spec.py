"""Tests for scripts/trim_openapi_spec.py (offline spec trimming helper)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "trim_openapi_spec.py"
_spec_module = importlib.util.spec_from_file_location("trim_openapi_spec", _SCRIPT)
trim_openapi_spec = importlib.util.module_from_spec(_spec_module)
sys.modules.setdefault("trim_openapi_spec", trim_openapi_spec)
_spec_module.loader.exec_module(trim_openapi_spec)

SPEC_20 = {
    "swagger": "2.0",
    "info": {"title": "clinic", "version": "1.0"},
    "host": "api.example.com",
    "paths": {
        "/pets": {
            "get": {"responses": {"200": {"description": "ok", "schema": {"$ref": "#/definitions/Pets"}}}},
        },
        "/owners": {
            "get": {"responses": {"200": {"description": "ok", "schema": {"$ref": "#/definitions/Owners"}}}},
        },
    },
    "definitions": {
        "Pets": {"type": "array", "items": {"$ref": "#/definitions/Pet"}},
        "Pet": {"type": "object", "properties": {"name": {"type": "string"}}},
        "Owners": {"type": "array", "items": {"type": "object"}},
        "Unused": {"type": "string"},
    },
}

SPEC_30 = {
    "openapi": "3.0.3",
    "info": {"title": "clinic", "version": "1.0"},
    "paths": {
        "/v2/pets": {
            "get": {
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}},
                    }
                }
            }
        },
        "/v2/owners": {"get": {"responses": {"200": {"description": "ok"}}}},
    },
    "components": {
        "schemas": {
            "Pet": {"type": "object", "properties": {"tag": {"$ref": "#/components/schemas/Tag"}}},
            "Tag": {"type": "string"},
            "Orphan": {"type": "number"},
        }
    },
}


def test_trim_20_keeps_exact_paths_and_referenced_definitions() -> None:
    trimmed, kept = trim_openapi_spec.trim_spec(SPEC_20, ["/pets"], [])
    assert list(trimmed["paths"]) == ["/pets"]
    assert set(trimmed["definitions"]) == {"Pets", "Pet"}  # Unused is dropped
    assert kept["definitions"] == 2
    # Non-path sections pass through untouched.
    assert trimmed["host"] == "api.example.com"
    assert trimmed["info"] == SPEC_20["info"]


def test_trim_20_transitively_keeps_nested_refs() -> None:
    trimmed, _ = trim_openapi_spec.trim_spec(SPEC_20, [], ["/pets"])
    # Pets -> Pet stays even though only Pets is referenced directly.
    assert set(trimmed["definitions"]) == {"Pets", "Pet"}


def test_trim_30_drops_unreferenced_component_schemas() -> None:
    trimmed, kept = trim_openapi_spec.trim_spec(SPEC_30, ["/v2/pets"], [])
    assert list(trimmed["paths"]) == ["/v2/pets"]
    assert set(trimmed["components"]["schemas"]) == {"Pet", "Tag"}  # transitive; Orphan dropped
    assert kept["components.schemas"] == 2


def test_trim_30_prefix_keeps_multiple_paths() -> None:
    trimmed, _ = trim_openapi_spec.trim_spec(SPEC_30, [], ["/v2/"])
    assert set(trimmed["paths"]) == {"/v2/pets", "/v2/owners"}


def test_trim_30_drops_empty_components_section() -> None:
    spec = {
        "openapi": "3.0.3",
        "paths": {"/ping": {"get": {"responses": {"200": {"description": "ok"}}}}},
        "components": {"schemas": {"Unused": {"type": "string"}}},
    }
    trimmed, kept = trim_openapi_spec.trim_spec(spec, ["/ping"], [])
    assert "components" not in trimmed
    assert kept["components.schemas"] == 0


def test_no_matching_path_is_an_error() -> None:
    with pytest.raises(ValueError):
        trim_openapi_spec.trim_spec(SPEC_20, ["/nope"], [])


def test_empty_filters_are_an_error() -> None:
    with pytest.raises(ValueError):
        trim_openapi_spec.trim_spec(SPEC_20, [], [])


def test_refs_outside_schema_sections_survive(tmp_path: Path) -> None:
    spec = {
        "swagger": "2.0",
        "paths": {
            "/ping": {
                "get": {
                    "responses": {"200": {"description": "ok", "schema": {"$ref": "#/definitions/Ok"}}},
                    "parameters": [{"$ref": "#/parameters/Trace"}],
                }
            }
        },
        "definitions": {"Ok": {"type": "object"}},
        "parameters": {"Trace": {"name": "trace", "in": "header", "type": "string"}},
    }
    trimmed, _ = trim_openapi_spec.trim_spec(spec, ["/ping"], [])
    assert trimmed["definitions"] == {"Ok": spec["definitions"]["Ok"]}
    assert trimmed["parameters"] == spec["parameters"]  # untouched section stays valid


def test_schemas_via_kept_non_schema_sections_survive_30() -> None:
    # components.responses is kept verbatim, so the schema it references must be
    # kept too -- pre-fix the transitive collection ignored non-schema sections
    # and emitted a dangling $ref.
    spec = {
        "openapi": "3.0.3",
        "paths": {"/ping": {"get": {"responses": {"404": {"$ref": "#/components/responses/NotFound"}}}}},
        "components": {
            "responses": {
                "NotFound": {
                    "description": "missing",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
                }
            },
            "schemas": {"Error": {"type": "object"}, "Orphan": {"type": "string"}},
        },
    }
    trimmed, kept = trim_openapi_spec.trim_spec(spec, ["/ping"], [])
    assert set(trimmed["components"]["schemas"]) == {"Error"}
    assert kept["components.schemas"] == 1


def test_schemas_via_kept_non_schema_sections_survive_20() -> None:
    spec = {
        "swagger": "2.0",
        "paths": {
            "/ping": {
                "get": {
                    "parameters": [{"$ref": "#/parameters/Limit"}],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
        "parameters": {"Limit": {"name": "limit", "in": "body", "schema": {"$ref": "#/definitions/OnlyViaParam"}}},
        "definitions": {"OnlyViaParam": {"type": "integer"}, "Unused": {"type": "string"}},
    }
    trimmed, _ = trim_openapi_spec.trim_spec(spec, ["/ping"], [])
    assert set(trimmed["definitions"]) == {"OnlyViaParam"}


def test_escaped_pointer_ref_keeps_tilde_named_schema() -> None:
    # "#/definitions/a~1b" unescapes to segments ("definitions", "a/b"); the pruner
    # must compare against the unescaped form of the raw name "a~1b" too, or the
    # schema is silently dropped and the kept $ref dangles.
    spec = {
        "swagger": "2.0",
        "paths": {"/x": {"get": {"responses": {"200": {"description": "ok", "schema": {"$ref": "#/definitions/a~1b"}}}}}},
        "definitions": {"a~1b": {"type": "string"}, "Other": {"type": "string"}},
    }
    trimmed, _ = trim_openapi_spec.trim_spec(spec, ["/x"], [])
    assert "a~1b" in trimmed["definitions"]
    assert "Other" not in trimmed["definitions"]


def test_real_slash_named_schema_kept_via_escaped_ref() -> None:
    spec = {
        "swagger": "2.0",
        "paths": {"/x": {"get": {"responses": {"200": {"description": "ok", "schema": {"$ref": "#/definitions/a~1b"}}}}}},
        "definitions": {"a/b": {"type": "string"}},
    }
    trimmed, _ = trim_openapi_spec.trim_spec(spec, ["/x"], [])
    assert "a/b" in trimmed["definitions"]


def test_cli_writes_json_and_yaml_outputs(tmp_path: Path, capsys) -> None:
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(SPEC_20), encoding="utf-8")

    out_json = tmp_path / "trimmed.json"
    assert trim_openapi_spec.main([str(spec_file), "-o", str(out_json), "--paths", "/pets"]) == 0
    trimmed = json.loads(out_json.read_text(encoding="utf-8"))
    assert list(trimmed["paths"]) == ["/pets"]

    out_yaml = tmp_path / "trimmed.yaml"
    assert trim_openapi_spec.main([str(spec_file), "-o", str(out_yaml), "--paths", "/owners"]) == 0
    yaml_text = out_yaml.read_text(encoding="utf-8")
    assert "owners" in yaml_text

    # stdout output is JSON.
    assert trim_openapi_spec.main([str(spec_file), "--path-prefix", "/pets"]) == 0
    stdout_text = capsys.readouterr().out
    assert json.loads(stdout_text)["paths"].keys() == {"/pets"}


def test_cli_bad_input_exits_2(tmp_path: Path) -> None:
    assert trim_openapi_spec.main([str(tmp_path / "absent.json"), "--paths", "/pets"]) == 2
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(SPEC_20), encoding="utf-8")
    assert trim_openapi_spec.main([str(spec_file), "--paths", "/nope"]) == 2


def test_cli_invalid_yaml_spec_exits_2_with_message(tmp_path: Path, capsys) -> None:
    # A YAML ParserError is neither OSError nor ValueError: pre-fix it escaped as a
    # traceback with exit 1.
    bad = tmp_path / "bad.yaml"
    bad.write_text("openapi: [1, 2\n", encoding="utf-8")
    assert trim_openapi_spec.main([str(bad), "--paths", "/x"]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_unwritable_output_exits_2(tmp_path: Path, capsys) -> None:
    # dump_spec ran outside the guarded block: pre-fix an unwritable -o path gave a
    # traceback and exit 1.
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(SPEC_20), encoding="utf-8")
    out = tmp_path / "missing-dir" / "out.json"
    assert trim_openapi_spec.main([str(spec_file), "-o", str(out), "--paths", "/pets"]) == 2
    assert "error:" in capsys.readouterr().err
    assert not out.exists()
