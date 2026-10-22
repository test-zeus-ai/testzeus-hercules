"""Remote ``$ref`` confinement for JSON-schema validation (SSRF / local file read).

jsonschema's default registry fetches unknown ``$ref`` URIs via urllib. Both
schema-validation tools must instead refuse everything that is not an
in-document ``#/...`` reference. Nothing here touches the network: the socket
and urllib entry points are patched to fail the test if anything tries.
"""

import asyncio
import importlib.util
import json
import socket
import urllib.request

import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools import api_calls
from testzeus_hercules.core.tools.openapi_validation import (
    validate_against_openapi_spec,
)

HAS_OAS_VALIDATOR = importlib.util.find_spec("openapi_schema_validator") is not None
requires_oas_validator = pytest.mark.skipif(not HAS_OAS_VALIDATOR, reason="openapi-schema-validator (api extra) is not installed")


@pytest.fixture
def no_network(monkeypatch):
    """Fail loudly if anything tries to open a socket or call urllib."""
    attempts = []

    def _forbidden(*args, **kwargs):
        attempts.append(args)
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(socket.socket, "connect", _forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)
    return attempts


@pytest.fixture
def proof_env(tmp_path):
    conf = get_global_conf()
    original_config = dict(conf.get_config())
    original_test_id = conf.get_default_test_id()
    conf.get_config()["PROJECT_SOURCE_ROOT"] = str(tmp_path)
    conf.set_default_test_id("ref-confinement-tests")
    yield conf
    conf.get_config().clear()
    conf.get_config().update(original_config)
    conf.set_default_test_id(original_test_id)


@pytest.fixture
def secret_file_uri(tmp_path) -> str:
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"type": "string"}), encoding="utf-8")
    return secret.as_uri()


def _schema_verdict(body, schema):
    return json.loads(asyncio.run(api_calls.validate_response_schema(body, schema)))


def _spec(version_key: str, version: str, schema) -> dict:
    spec = {"info": {"title": "t", "version": "1"}, version_key: version}
    if version_key == "swagger":
        spec["paths"] = {"/x": {"get": {"responses": {"200": {"description": "ok", "schema": schema}}}}}
    else:
        spec["paths"] = {"/x": {"get": {"responses": {"200": {"description": "ok", "content": {"application/json": {"schema": schema}}}}}}}
    return spec


def _contract_verdict(tmp_path, spec, body):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return json.loads(asyncio.run(validate_against_openapi_spec(body, str(spec_path), "/x", "get", 200)))


# ── validate_response_schema ─────────────────────────────────────────────────


@pytest.mark.parametrize("uri", ["http://127.0.0.1:9/x", "https://example.invalid/schema.json", "relative/other.json"])
def test_response_schema_remote_ref_is_rejected_without_network(no_network, uri) -> None:
    verdict = _schema_verdict({"a": 1}, {"type": "object", "properties": {"a": {"$ref": uri}}})
    assert verdict["assertion"] == "ERROR"
    assert "not allowed" in verdict["reason"]
    assert no_network == []


def test_response_schema_file_ref_is_rejected_without_reading_the_file(no_network, secret_file_uri) -> None:
    verdict = _schema_verdict({"a": "text"}, {"type": "object", "properties": {"a": {"$ref": secret_file_uri}}})
    assert verdict["assertion"] == "ERROR"
    assert "not allowed" in verdict["reason"]
    assert no_network == []


def test_response_schema_local_refs_still_resolve(no_network) -> None:
    schema = {"type": "object", "properties": {"a": {"$ref": "#/$defs/n"}}, "$defs": {"n": {"type": "integer"}}}
    assert _schema_verdict({"a": 1}, schema)["assertion"] == "PASSED"
    assert _schema_verdict({"a": "x"}, schema)["assertion"] == "FAILED"


def test_registry_retrieve_hook_raises_for_any_uri() -> None:
    registry = api_calls.local_only_schema_registry()
    with pytest.raises(Exception) as excinfo:
        registry.get_or_retrieve("http://127.0.0.1:1/x")
    assert "not allowed" in str(excinfo.value.__cause__ or excinfo.value)


# ── validate_against_openapi_spec ────────────────────────────────────────────


def test_swagger2_remote_ref_is_rejected_without_network(no_network, proof_env, tmp_path) -> None:
    spec = _spec("swagger", "2.0", {"type": "object", "properties": {"a": {"$ref": "http://127.0.0.1:9/x"}}})
    verdict = _contract_verdict(tmp_path, spec, {"a": 1})
    assert verdict["assertion"] == "ERROR"
    assert "not allowed" in verdict["reason"]
    assert no_network == []


def test_swagger2_file_ref_is_rejected_without_network(no_network, proof_env, tmp_path, secret_file_uri) -> None:
    spec = _spec("swagger", "2.0", {"type": "object", "properties": {"a": {"$ref": secret_file_uri}}})
    verdict = _contract_verdict(tmp_path, spec, {"a": "text"})
    assert verdict["assertion"] == "ERROR"
    assert "not allowed" in verdict["reason"]
    assert no_network == []


@requires_oas_validator
@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
def test_oas3_remote_ref_is_rejected_without_network(no_network, proof_env, tmp_path, version) -> None:
    spec = _spec("openapi", version, {"type": "object", "properties": {"a": {"$ref": "http://127.0.0.1:9/x"}}})
    verdict = _contract_verdict(tmp_path, spec, {"a": 1})
    assert verdict["assertion"] == "ERROR"
    assert "not allowed" in verdict["reason"]
    assert no_network == []


@requires_oas_validator
@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
def test_oas3_file_ref_is_rejected_without_network(no_network, proof_env, tmp_path, secret_file_uri, version) -> None:
    spec = _spec("openapi", version, {"type": "object", "properties": {"a": {"$ref": secret_file_uri}}})
    verdict = _contract_verdict(tmp_path, spec, {"a": "text"})
    assert verdict["assertion"] == "ERROR"
    assert "not allowed" in verdict["reason"]
    assert no_network == []
