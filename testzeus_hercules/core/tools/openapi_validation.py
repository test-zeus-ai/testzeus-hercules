"""
Contract regression: validate an API response body against the operation's
declared response schema in an OpenAPI (Swagger) specification.

The validator is selected by the version the spec declares:
Swagger 2.0 -> jsonschema's Draft4Validator; OpenAPI 3.0.x / 3.1.x ->
OAS30Validator / OAS31Validator from openapi_schema_validator. The validator
is built on the WHOLE spec and then re-targeted with ``.evolve(schema=...)``
so local ``$ref``s resolve against the full document.

Every verdict — pass included — is appended to the assertion audit ledger,
tagged with a fingerprint of the examined payload.
"""

import json
import os
from typing import Annotated, Any, Dict, List, Optional

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.api_calls import parse_json_payload
from testzeus_hercules.core.tools.tool_registry import (
    assertion_logger,
    assertion_subject,
    tool,
)
from testzeus_hercules.utils.jsonpath_helper import find_json_path
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.path_guard import resolve_in_roots

OPENAPI_SCHEMA_VALIDATOR_INSTALL_HINT = (
    "openapi_schema_validator is not installed. Install the API extras to " "enable OpenAPI contract validation: pip install 'testzeus-hercules[api]' " "(provides openapi-schema-validator)."
)

_DEFAULT_CONTENT_TYPE = "application/json"


def _minified(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _load_spec(spec_path: str) -> Dict[str, Any]:
    """Load an OpenAPI/Swagger spec from a JSON or YAML file inside the test data / project roots."""
    # Model-supplied path: confined (PathConfinementError is a ValueError).
    spec_path = resolve_in_roots(spec_path)
    if not os.path.isfile(spec_path):
        raise FileNotFoundError(f"OpenAPI spec file not found: {spec_path}")
    with open(spec_path, "r", encoding="utf-8") as spec_file:
        text = spec_file.read()
    try:
        spec = json.loads(text)
    except json.JSONDecodeError:
        import yaml

        try:
            spec = yaml.safe_load(text)
        except yaml.YAMLError as e:
            # Keep the reason single-line: YAML parser errors are multi-line by default.
            raise ValueError(f"{spec_path}: invalid YAML ({' '.join(str(e).split())}).") from e
    if not isinstance(spec, dict):
        raise ValueError(f"OpenAPI spec must be a mapping, got {type(spec).__name__}.")
    return spec


def _validator_for_spec(spec: Dict[str, Any]):
    """Pick the validator class matching the spec's declared version."""
    swagger_version = str(spec.get("swagger") or "")
    openapi_version = str(spec.get("openapi") or "")
    if swagger_version.startswith("2"):
        # Swagger 2.0 response schemas are JSON Schema draft-4 documents.
        try:
            from jsonschema.validators import Draft4Validator
        except ImportError as e:  # pragma: no cover - jsonschema is a main dependency
            raise ImportError(f"jsonschema is not installed ({e}).") from e
        return Draft4Validator
    if not openapi_version:
        raise ValueError("Spec declares neither 'swagger' nor 'openapi'; cannot pick a validator.")
    if openapi_version.startswith("3.0"):
        try:
            from openapi_schema_validator import OAS30Validator
        except ImportError as e:
            raise ImportError(OPENAPI_SCHEMA_VALIDATOR_INSTALL_HINT) from e
        return OAS30Validator
    if openapi_version.startswith("3.1"):
        try:
            from openapi_schema_validator import OAS31Validator
        except ImportError as e:
            raise ImportError(OPENAPI_SCHEMA_VALIDATOR_INSTALL_HINT) from e
        return OAS31Validator
    raise ValueError(f"Unsupported OpenAPI version '{openapi_version}' (supported: 2.0, 3.0, 3.1).")


def _response_schema(spec: Dict[str, Any], path: str, method: str, status_code: int, operation: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the declared response schema for one operation+status."""
    responses = operation.get("responses") or {}
    # YAML parses a bare `200:` response key as an int, so probe both key forms.
    response = responses.get(str(status_code)) or responses.get(status_code) or responses.get("default")
    if response is None:
        declared = ", ".join(sorted(map(str, responses.keys()))) or "none"
        raise KeyError(f"Status {status_code} is not declared for {method.upper()} {path} (declared: {declared}).")
    if str(spec.get("swagger") or "").startswith("2"):
        schema = response.get("schema")
    else:
        content = response.get("content") or {}
        content_type = _DEFAULT_CONTENT_TYPE if _DEFAULT_CONTENT_TYPE in content else (next(iter(content), None))
        schema = content[content_type].get("schema") if content_type else None
    if not schema:
        raise KeyError(f"No response schema is declared for {method.upper()} {path} -> {status_code}.")
    return schema


def _record(response_json: Any, result: Dict[str, Any]) -> None:
    """Append the verdict to the assertion audit ledger."""
    entry = {
        "source": "validate_against_openapi_spec",
        "test_id": get_global_conf().get_default_test_id(),
        "subject": assertion_subject(result.get("subject_payload", "")),
        "path": f"{str(result.get('method', '')).upper()} {result.get('operation_path')}",
        "operator": "openapi_response_schema",
        "expected": f"{result.get('spec')}#{result.get('status_code')}",
        "actual": result.get("actual"),
        "verdict": result.get("assertion"),
    }
    result.pop("subject_payload", None)
    for optional_key in ("note", "reason"):
        if result.get(optional_key):
            entry[optional_key] = result[optional_key]
    try:
        assertion_logger(json.dumps(entry, separators=(",", ":"), default=str))
    except Exception as e:
        # The ledger must never turn a verdict into a crash.
        logger.warning(f"Failed to append to the assertion ledger: {e}")


@tool(
    agent_names=["api_nav_agent"],
    name="validate_against_openapi_spec",
    description=(
        "Validate a JSON response body against the response schema declared in "
        "an OpenAPI/Swagger spec (contract check). Supports Swagger 2.0 and "
        "OpenAPI 3.0/3.1. Never judge the response shape yourself — call this "
        "tool and use its returned 'assertion' value and 'violations' list."
    ),
)
async def validate_against_openapi_spec(
    response_json: Annotated[Any, "The JSON response body (object or JSON string) to validate."],
    spec_path: Annotated[str, "Path to the OpenAPI/Swagger specification file (JSON or YAML)."],
    path: Annotated[str, "The API path exactly as declared in the spec, e.g. /users/{id}."],
    method: Annotated[str, "The HTTP method of the operation. Defaults to get."] = "get",
    status_code: Annotated[int, "The response status code whose declared schema to validate against. Defaults to 200."] = 200,
    response_path: Annotated[Optional[str], "Optional JSONPath selecting the sub-value of the body to validate. Defaults to the whole body."] = None,
) -> Annotated[str, "Minified JSON verdict: {assertion, operation, violations: [{path, message}], ...}."]:
    result: Dict[str, Any] = {
        "spec": spec_path,
        "operation_path": path,
        "method": method,
        "status_code": status_code,
    }
    try:
        result.update(_evaluate(response_json, spec_path, path, method, status_code, response_path))
    except Exception as e:
        logger.exception("validate_against_openapi_spec failed unexpectedly")
        result["assertion"] = "ERROR"
        result["reason"] = f"Validation could not run: {type(e).__name__}: {e}"
        result["subject_payload"] = response_json if isinstance(response_json, str) else ""
    _record(response_json, result)
    return _minified({key: value for key, value in result.items() if key != "subject_payload"})


def _evaluate(response_json: Any, spec_path: str, path: str, method: str, status_code: int, response_path: Optional[str]) -> Dict[str, Any]:
    try:
        spec = _load_spec(spec_path)
    except (FileNotFoundError, ValueError, OSError) as e:
        return {"assertion": "ERROR", "reason": str(e), "subject_payload": ""}

    try:
        validator_cls = _validator_for_spec(spec)
    except (ImportError, ValueError) as e:
        return {"assertion": "ERROR", "reason": str(e), "subject_payload": ""}

    spec_paths = spec.get("paths") or {}
    if path not in spec_paths:
        declared = ", ".join(list(spec_paths.keys())[:20]) or "none"
        return {
            "assertion": "ERROR",
            "reason": f"Path '{path}' is not declared in the spec (declared paths include: {declared}).",
            "subject_payload": "",
        }
    operation = spec_paths[path].get(str(method).lower())
    if not isinstance(operation, dict):
        declared = ", ".join(key.upper() for key in spec_paths[path] if key != "parameters") or "none"
        return {
            "assertion": "ERROR",
            "reason": f"Method {str(method).upper()} is not declared for path '{path}' (declared methods: {declared}).",
            "subject_payload": "",
        }

    try:
        schema = _response_schema(spec, path, method, status_code, operation)
    except KeyError as e:
        # str(KeyError) would wrap the message in quotes; args[0] is the bare message.
        reason = e.args[0] if e.args else str(e)
        return {"assertion": "ERROR", "reason": reason, "subject_payload": ""}

    try:
        document = parse_json_payload(response_json)
    except ValueError as e:
        return {"assertion": "ERROR", "reason": f"Response is not valid JSON: {e}", "subject_payload": ""}

    instance_location = "$"
    if response_path:
        try:
            matches = find_json_path(document, response_path)
        except ImportError as e:
            return {"assertion": "ERROR", "reason": str(e), "subject_payload": ""}
        except Exception as e:
            return {"assertion": "ERROR", "reason": f"Invalid JSONPath '{response_path}': {e}", "subject_payload": ""}
        if not matches:
            return {
                "assertion": "ERROR",
                "reason": f"response_path '{response_path}' matched nothing in the response body.",
                "subject_payload": "",
            }
        document = matches[0]
        instance_location = response_path

    # Build the validator on the WHOLE spec so local $refs resolve, then
    # re-target it at the extracted response schema.
    subject_payload = document if isinstance(document, str) else json.dumps(document, separators=(",", ":"), default=str)
    try:
        base_validator = validator_cls(spec)
        validator = base_validator.evolve(schema=schema)
        errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    except Exception as e:
        return {"assertion": "ERROR", "reason": f"Schema validation could not run: {type(e).__name__}: {e}", "subject_payload": subject_payload}

    violations: List[Dict[str, str]] = []
    for error in errors:
        location = instance_location
        segments = [str(segment) for segment in error.absolute_path]
        if segments:
            location += "".join(f"[{segment}]" if segment.isdigit() else f".{segment}" for segment in segments)
        violations.append({"path": location, "message": error.message})

    return {
        "assertion": "FAILED" if violations else "PASSED",
        "operation": f"{str(method).upper()} {path} -> {status_code}",
        "response_path": response_path,
        "violations": violations,
        "violation_count": len(violations),
        "actual": "violates the declared schema" if violations else "conforms to the declared schema",
        "subject_payload": subject_payload,
    }
