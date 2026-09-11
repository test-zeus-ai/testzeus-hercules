"""Tests for OpenAPI contract validation (core.tools.openapi_validation).

Tiny inline specs are written to tmp_path; no network, no LLM. Swagger 2.0
validation rides on jsonschema's Draft4Validator, so those cases run
everywhere; the OpenAPI 3.x cases need the openapi-schema-validator extra.
"""

import asyncio
import hashlib
import importlib.util
import json

import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.openapi_validation import (
    validate_against_openapi_spec,
)

HAS_OAS_VALIDATOR = importlib.util.find_spec("openapi_schema_validator") is not None

requires_oas_validator = pytest.mark.skipif(not HAS_OAS_VALIDATOR, reason="openapi-schema-validator (api extra) is not installed")

PET_20 = {
    "id": 1,
    "name": "Rex",
}

SPEC_20 = {
    "swagger": "2.0",
    "info": {"title": "pets", "version": "1.0"},
    "paths": {
        "/pets": {
            "get": {
                "responses": {
                    "200": {"description": "ok", "schema": {"$ref": "#/definitions/Pets"}},
                    "400": {"description": "bad request"},
                }
            }
        }
    },
    "definitions": {
        "Pets": {"type": "array", "items": {"$ref": "#/definitions/Pet"}},
        "Pet": {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
        },
    },
}

SPEC_30 = {
    "openapi": "3.0.3",
    "info": {"title": "pets", "version": "1.0"},
    "paths": {
        "/pets": {
            "get": {
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pets"}}},
                    }
                }
            }
        }
    },
    "components": {
        "schemas": {
            "Pets": {"type": "array", "items": {"$ref": "#/components/schemas/Pet"}},
            "Pet": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
            },
        }
    },
}


@pytest.fixture
def proof_env(tmp_path):
    """Point the proof folder at tmp_path; restore the config afterwards."""
    conf = get_global_conf()
    original_config = dict(conf.get_config())
    original_test_id = conf.get_default_test_id()
    conf.get_config()["PROJECT_SOURCE_ROOT"] = str(tmp_path)
    conf.set_default_test_id("openapi-tests")
    yield conf
    conf.get_config().clear()
    conf.get_config().update(original_config)
    conf.set_default_test_id(original_test_id)


@pytest.fixture
def spec_20_file(tmp_path) -> str:
    spec_path = tmp_path / "pets-2.0.json"
    spec_path.write_text(json.dumps(SPEC_20), encoding="utf-8")
    return str(spec_path)


@pytest.fixture
def spec_30_file(tmp_path) -> str:
    spec_path = tmp_path / "pets-3.0.yaml"
    spec_path.write_text(json.dumps(SPEC_30), encoding="utf-8")
    return str(spec_path)


def _validate(body, spec_path, path="/pets", method="get", status_code=200, response_path=None):
    verdict = asyncio.run(validate_against_openapi_spec(body, spec_path, path, method, status_code, response_path))
    return json.loads(verdict)


# ── Swagger 2.0 (jsonschema only) ────────────────────────────────────────────


def test_20_conforming_response_passes(proof_env, spec_20_file) -> None:
    verdict = _validate([PET_20], spec_20_file)
    assert verdict["assertion"] == "PASSED"
    assert verdict["violation_count"] == 0
    assert verdict["operation"] == "GET /pets -> 200"


def test_20_violation_fails_with_paths(proof_env, spec_20_file) -> None:
    verdict = _validate([{"name": "no id"}], spec_20_file)
    assert verdict["assertion"] == "FAILED"
    assert verdict["violation_count"] >= 1
    assert any("id" in violation["message"] for violation in verdict["violations"])
    assert verdict["violations"][0]["path"].startswith("$")


def test_20_type_mismatch_fails(proof_env, spec_20_file) -> None:
    verdict = _validate([{"id": "not-an-int"}], spec_20_file)
    assert verdict["assertion"] == "FAILED"
    assert any("type" in violation["message"].lower() for violation in verdict["violations"])


def test_unknown_path_is_error_naming_declared(proof_env, spec_20_file) -> None:
    verdict = _validate([PET_20], spec_20_file, path="/users")
    assert verdict["assertion"] == "ERROR"
    assert "/pets" in verdict["reason"]


def test_unknown_method_is_error_naming_declared(proof_env, spec_20_file) -> None:
    verdict = _validate(PET_20, spec_20_file, method="post")
    assert verdict["assertion"] == "ERROR"
    assert "GET" in verdict["reason"]


def test_unknown_status_is_error_naming_declared(proof_env, spec_20_file) -> None:
    verdict = _validate(PET_20, spec_20_file, status_code=500)
    assert verdict["assertion"] == "ERROR"
    assert "200" in verdict["reason"] and "400" in verdict["reason"]


def test_status_without_schema_is_error(proof_env, spec_20_file) -> None:
    verdict = _validate({"message": "bad"}, spec_20_file, status_code=400)
    assert verdict["assertion"] == "ERROR"
    assert "No response schema" in verdict["reason"]


@pytest.mark.skipif(not importlib.util.find_spec("jsonpath_ng"), reason="jsonpath_ng (api extra) is not installed")
def test_response_path_selects_the_instance(proof_env, spec_20_file) -> None:
    verdict = _validate({"data": [PET_20]}, spec_20_file, response_path="$.data")
    assert verdict["assertion"] == "PASSED"
    unmatched = _validate({"other": 1}, spec_20_file, response_path="$.data")
    assert unmatched["assertion"] == "ERROR"
    assert "matched nothing" in unmatched["reason"]


def test_missing_spec_file_is_error(proof_env, tmp_path) -> None:
    verdict = _validate([PET_20], str(tmp_path / "absent.json"))
    assert verdict["assertion"] == "ERROR"
    assert "not found" in verdict["reason"]


YAML_SPEC_20_INT_STATUS_KEYS = """\
swagger: '2.0'
info:
  title: pets
  version: '1.0'
paths:
  /pets:
    get:
      responses:
        200:
          description: ok
          schema:
            $ref: '#/definitions/Pets'
definitions:
  Pets:
    type: array
    items:
      $ref: '#/definitions/Pet'
  Pet:
    type: object
    required:
      - id
    properties:
      id:
        type: integer
      name:
        type: string
"""


@pytest.fixture
def yaml_spec_int_status_keys_file(tmp_path) -> str:
    # YAML parses a bare `200:` response key as an int, not the string "200".
    spec_path = tmp_path / "pets-int-keys.yaml"
    spec_path.write_text(YAML_SPEC_20_INT_STATUS_KEYS, encoding="utf-8")
    return str(spec_path)


def test_yaml_integer_status_keys_match(proof_env, yaml_spec_int_status_keys_file) -> None:
    verdict = _validate([PET_20], yaml_spec_int_status_keys_file)
    assert verdict["assertion"] == "PASSED", verdict.get("reason")


def test_yaml_integer_status_keys_unknown_status_lists_declared(proof_env, yaml_spec_int_status_keys_file) -> None:
    # Pre-fix the fallback error path crashed on ", ".join(sorted(int_keys)) and the
    # TypeError surfaced as a useless reason.
    verdict = _validate(PET_20, yaml_spec_int_status_keys_file, status_code=500)
    assert verdict["assertion"] == "ERROR"
    assert "200" in verdict["reason"]
    assert "\n" not in verdict["reason"]


def test_invalid_yaml_spec_is_single_line_error(proof_env, tmp_path) -> None:
    # Pre-fix the yaml ParserError escaped into the generic handler: full traceback
    # in the log and a multi-line reason.
    spec_path = tmp_path / "broken.yaml"
    spec_path.write_text("openapi: [1, 2\n", encoding="utf-8")
    verdict = _validate({}, str(spec_path))
    assert verdict["assertion"] == "ERROR"
    assert "\n" not in verdict["reason"]


def test_keyerror_reason_is_not_double_quoted(proof_env, spec_20_file) -> None:
    # str(KeyError("msg")) renders as "'msg'"; the reason must be the bare message.
    verdict = _validate(PET_20, spec_20_file, status_code=500)
    assert verdict["assertion"] == "ERROR"
    assert not verdict["reason"].startswith("'")
    assert "not declared" in verdict["reason"]


def test_unsupported_version_is_error(proof_env, tmp_path) -> None:
    spec_path = tmp_path / "future.json"
    spec_path.write_text(json.dumps({"openapi": "9.9.9", "paths": {}}), encoding="utf-8")
    verdict = _validate({}, str(spec_path))
    assert verdict["assertion"] == "ERROR"
    assert "9.9.9" in verdict["reason"]


# ── OpenAPI 3.x (needs the extra) ────────────────────────────────────────────


@requires_oas_validator
def test_30_conforming_response_passes(proof_env, spec_30_file) -> None:
    verdict = _validate([PET_20], spec_30_file)
    assert verdict["assertion"] == "PASSED"


@requires_oas_validator
def test_30_violation_fails(proof_env, spec_30_file) -> None:
    verdict = _validate([{"name": "no id"}], spec_30_file)
    assert verdict["assertion"] == "FAILED"
    assert any("id" in violation["message"] for violation in verdict["violations"])


# ── audit ledger ─────────────────────────────────────────────────────────────


def test_verdicts_are_recorded_with_fingerprint(proof_env, spec_20_file) -> None:
    _validate([PET_20], spec_20_file)
    _validate([{"name": "no id"}], spec_20_file)

    log_path = f"{proof_env.get_proof_path()}/assertion_logs.log"
    with open(log_path, "r", encoding="utf-8") as log_file:
        entries = [json.loads(line) for line in log_file if line.strip()]
    assert [entry["verdict"] for entry in entries] == ["PASSED", "FAILED"]
    assert entries[0]["source"] == "validate_against_openapi_spec"
    assert entries[0]["operator"] == "openapi_response_schema"
    assert entries[0]["path"] == "GET /pets"
    expected_subject = hashlib.sha1(json.dumps([PET_20], separators=(",", ":"), default=str).encode()).hexdigest()[:12]
    assert entries[0]["subject"] == expected_subject
