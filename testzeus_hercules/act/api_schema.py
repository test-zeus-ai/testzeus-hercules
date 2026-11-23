"""Schema checks for API responses: a JSON Schema (or a schema inside a spec file), and an OpenAPI operation's declared response.

Spec files are JSON or YAML. The validator follows the version the document
declares: Swagger 2.0 uses JSON Schema draft 4, OpenAPI 3.0/3.1 the OAS validators,
anything else the draft its ``$schema`` names. The validator is built on the whole
document and re-targeted at the sub-schema, so local ``#/...`` references resolve;
remote and file references are refused, never fetched. Every check returns a
verdict dict like :func:`testzeus_hercules.act.api_assert.evaluate` does.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from testzeus_hercules.act import jsonpath
from testzeus_hercules.act.api_assert import Verdict

OAS_INSTALL_HINT = "openapi_schema_validator is not installed; it is a Hercules dependency, so reinstall Hercules: pip install --force-reinstall testzeus-hercules"
MAX_SPEC_BYTES = 10 * 1024 * 1024
MAX_VIOLATIONS = 20
_DEFAULT_CONTENT_TYPE = "application/json"
_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_TEMPLATE_PARAMETER = re.compile(r"\\\{[^{}/]+\\\}")


class SpecError(ValueError):
    """A spec or schema file cannot be used; the message is safe to show."""


class RemoteRefError(ValueError):
    """A ``$ref`` pointed outside the document being validated."""


def _refuse(uri: str) -> Any:
    raise RemoteRefError(f"remote or external $ref '{uri}' is not allowed; only local '#/...' references are resolved")


def local_registry() -> Any:
    """A registry with the bundled metaschemas that refuses every other URI (no SSRF, no file reads)."""
    from jsonschema_specifications import REGISTRY
    from referencing import Registry

    return REGISTRY.combine(Registry(retrieve=_refuse))  # type: ignore[call-arg]


def _remote_ref(exc: BaseException) -> RemoteRefError | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, RemoteRefError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def load_document(path: Path) -> dict[str, Any]:
    """A JSON or YAML mapping from ``path``; :class:`SpecError` when it cannot be read or is not a mapping."""
    if not path.is_file():
        raise SpecError(f"spec file not found: {path.name}")
    if path.stat().st_size > MAX_SPEC_BYTES:
        raise SpecError(f"{path.name} is larger than {MAX_SPEC_BYTES // (1024 * 1024)} MB")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SpecError(f"cannot read {path.name} ({type(exc).__name__})") from None
    try:
        document = json.loads(text)
    except ValueError:
        try:
            document = yaml.safe_load(text)
        except (yaml.YAMLError, RecursionError) as exc:
            raise SpecError(f"{path.name}: invalid YAML ({' '.join(str(exc).split())[:200]})") from None
    if not isinstance(document, dict):
        raise SpecError(f"{path.name}: expected a mapping at the top level, got {type(document).__name__}")
    return document


def pointer(document: Any, ref: str) -> Any:
    """The node a local JSON pointer (``#/a/b`` or ``/a/b``) names; :class:`SpecError` when it does not exist."""
    node = document
    text = ref[1:] if ref.startswith("#") else ref
    for part in [segment for segment in text.split("/") if segment != ""]:
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, dict) and key.isdigit() and int(key) in node:
            node = node[int(key)]  # YAML reads a bare 200: key as an int
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            raise SpecError(f"'{ref}' does not exist in the document")
    return node


def validator_class(document: dict[str, Any], schema: Any) -> Any:
    """The validator for ``document``'s declared version (raises :class:`SpecError` or ImportError)."""
    swagger, openapi = str(document.get("swagger") or ""), str(document.get("openapi") or "")
    if swagger.startswith("2"):
        from jsonschema.validators import Draft4Validator

        return Draft4Validator
    if openapi.startswith(("3.0", "3.1")):
        try:
            from openapi_schema_validator import OAS30Validator, OAS31Validator
        except ImportError as exc:
            raise ImportError(OAS_INSTALL_HINT) from exc
        return OAS30Validator if openapi.startswith("3.0") else OAS31Validator
    if openapi or swagger:
        raise SpecError(f"unsupported spec version '{openapi or swagger}' (supported: Swagger 2.0, OpenAPI 3.0 and 3.1)")
    from jsonschema import validators

    return validators.validator_for(schema if isinstance(schema, dict) else document)


def _violations(errors: list[Any], location: str) -> list[dict[str, str]]:
    found = []
    for error in errors:
        segments = [str(segment) for segment in error.absolute_path]
        where = location + "".join(f"[{segment}]" if segment.isdigit() else f".{segment}" for segment in segments)
        found.append({"path": where, "message": error.message})
    return found


def _select(document: Any, path: str | None) -> tuple[Any, str, Verdict | None]:
    if not path:
        return document, "$", None
    try:
        matches = jsonpath.find(document, path)
    except ImportError as exc:
        return None, path, {"assertion": "ERROR", "reason": str(exc)}
    except Exception as exc:
        return None, path, {"assertion": "ERROR", "reason": f"Invalid JSONPath '{path}': {exc}"}
    if not matches:
        return None, path, {"assertion": "FAILED", "path": path, "reason": "Path matched nothing in the response body."}
    return matches[0], path, None


def validate(instance: Any, schema: Any, *, root: dict[str, Any] | None = None, path: str | None = None) -> Verdict:
    """Validate ``instance`` (or the value ``path`` selects in it) against ``schema``.

    ``root`` is the document the schema lives in (a spec or schema file), so its local
    references resolve; without it the schema is its own root and is checked first.
    """
    try:
        from jsonschema import SchemaError
    except ImportError as exc:  # pragma: no cover - jsonschema is a main dependency
        return {"assertion": "ERROR", "reason": f"jsonschema is not installed ({exc})"}
    selected, location, problem = _select(instance, path)
    if problem is not None:
        return problem
    base = root if root is not None else schema
    try:
        cls = validator_class(base if isinstance(base, dict) else {}, schema)
        if root is None:
            cls.check_schema(schema)
        validator = cls(base, registry=local_registry()).evolve(schema=schema)
        errors = sorted(validator.iter_errors(selected), key=lambda error: [str(part) for part in error.absolute_path])
    except SchemaError as exc:
        return {"assertion": "ERROR", "reason": f"Invalid JSON Schema: {exc.message}"}
    except (SpecError, ImportError) as exc:
        return {"assertion": "ERROR", "reason": str(exc)}
    except Exception as exc:
        refused = _remote_ref(exc)
        return {"assertion": "ERROR", "reason": f"Schema validation could not run: {refused or f'{type(exc).__name__}: {exc}'}"}
    violations = _violations(errors, location)
    return {
        "assertion": "FAILED" if violations else "PASSED",
        "schema_path": location,
        "violations": violations[:MAX_VIOLATIONS],
        "violation_count": len(violations),
    }


# --------------------------------------------------------------------------- OpenAPI operations


def declared_response(responses: dict[Any, Any], status: int | str) -> Any:
    """The response object for ``status``: the exact code (text or int key), then ``2XX``/``2xx``, then ``default``."""
    text = str(status).strip()
    number: int | str = int(text) if text.isdigit() else status
    digit = text[:1]
    for key in (text, number, f"{digit}XX", f"{digit}xx", "default"):
        if key in responses and responses[key] is not None:
            return responses[key]
    return None


def response_schema(spec: dict[str, Any], template: str, method: str, status: int | str) -> Any:
    """The declared response schema of one operation and status; :class:`SpecError` (naming what is declared) when there is none."""
    paths = spec.get("paths") or {}
    if template not in paths:
        declared = ", ".join(list(map(str, paths))[:20]) or "none"
        raise SpecError(f"path '{template}' is not declared in the spec (declared paths include: {declared})")
    operation = (paths[template] or {}).get(method.lower())
    if not isinstance(operation, dict):
        declared = ", ".join(key.upper() for key in paths[template] if key in _METHODS) or "none"
        raise SpecError(f"method {method.upper()} is not declared for '{template}' (declared methods: {declared})")
    responses = operation.get("responses") or {}
    response = declared_response(responses, status)
    if isinstance(response, dict) and isinstance(response.get("$ref"), str):
        response = pointer(spec, response["$ref"])
    if response is None:
        declared = ", ".join(sorted(map(str, responses))) or "none"
        raise SpecError(f"status {status} is not declared for {method.upper()} {template} (declared: {declared})")
    if str(spec.get("swagger") or "").startswith("2"):
        schema = response.get("schema")
    else:
        content = response.get("content") or {}
        content_type = _DEFAULT_CONTENT_TYPE if _DEFAULT_CONTENT_TYPE in content else next(iter(content), None)
        schema = content[content_type].get("schema") if content_type else None
    if not schema:
        raise SpecError(f"no response schema is declared for {method.upper()} {template} -> {status}")
    return schema


def match_template(spec: dict[str, Any], request_path: str, prefixes: tuple[str, ...] = ()) -> str | None:
    """The spec path template a concrete request path matches (``/pets/7`` -> ``/pets/{petId}``), literal paths first.

    The path prefix of the spec's server URLs (``/v1``) and any of ``prefixes`` (the
    path of the base URL the request was made against) are tried both kept and removed.
    """
    paths = [str(path) for path in (spec.get("paths") or {})]
    known = {"", *(prefix.rstrip("/") for prefix in prefixes)}
    for server in spec.get("servers") or []:
        url = str(server.get("url", "")) if isinstance(server, dict) else ""
        try:
            known.add(urlsplit(url).path.rstrip("/"))
        except ValueError:
            continue
    if spec.get("basePath"):
        known.add(str(spec["basePath"]).rstrip("/"))
    candidates = [request_path] + [request_path[len(prefix) :] or "/" for prefix in sorted(known, key=len, reverse=True) if prefix and request_path.startswith(prefix)]
    for candidate in candidates:
        if candidate in paths:
            return candidate
    ranked = sorted(paths, key=lambda template: (template.count("{"), -len(template)))
    for candidate in candidates:
        for template in ranked:
            regex = _TEMPLATE_PARAMETER.sub("[^/]+", re.escape(template))
            if re.fullmatch(regex, candidate.rstrip("/") or "/") or re.fullmatch(regex, candidate):
                return template
    return None


def validate_operation(instance: Any, spec: dict[str, Any], template: str, method: str, status: int | str, *, path: str | None = None) -> Verdict:
    """Validate a response body against the schema ``spec`` declares for ``method template -> status``."""
    try:
        schema = response_schema(spec, template, method, status)
        validator_class(spec, schema)
    except (SpecError, ImportError) as exc:
        return {"assertion": "ERROR", "reason": str(exc)}
    verdict = validate(instance, schema, root=spec, path=path)
    return {**verdict, "operation": f"{method.upper()} {template} -> {status}"}
