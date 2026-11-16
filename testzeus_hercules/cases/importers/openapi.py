"""Generate API test cases from an OpenAPI 3.x spec: one case per operation.

Each case sends the operation's request (path and required query parameters
filled from the spec's examples, defaults or type-based placeholders, a JSON body
built the same way) and checks the first documented 2xx status and, when that
response has a JSON body, its schema (by reference into the spec file). Path
parameters become ``test_data`` entries used as ``{{name}}`` placeholders, so they
can be edited in one place. Security schemes become secret references by name.

Cases are tagged ``kind:api`` and ``openapi`` and have one step: a readable sentence
(``I send a GET request to "/pets/{{petId}}"``) with the structured ``api`` payload the
engine executes (method, path, query, headers, body, and ``expect`` with the status and
the schema). The spec itself is copied to ``.hercules/specs/`` so the schema references
resolve when the cases run. Only local ``#/...`` references are resolved; external ones
are reported.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import yaml
from pydantic import ValidationError

from testzeus_hercules.cases.importers.common import ImporterError, ImportReport, PlannedFile, case_file, data_key, secret_name, unique_case_id, write_plan
from testzeus_hercules.cases.models import BASE_URL_PATTERN, ApiAuth, ApiCall, ApiExpect, EnvironmentRef, SecretRef, Step, TestCase, TestDataValue

API_TAGS = ("kind:api", "openapi")
SPECS_DIRNAME = "specs"
_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_MAX_SPEC_BYTES = 10 * 1024 * 1024
_MAX_OPERATIONS = 1000
_MAX_DEPTH = 6
_MAX_EXAMPLE_NODES = 2_000  # schema nodes visited for one operation's examples (wide schemas stop here)
_SERVER_VARIABLE = re.compile(r"\{([^{}]+)\}")
_PATH_PARAMETER = re.compile(r"\{([^{}]+)\}")
_STRING_FORMATS = {
    "email": "user@example.com",
    "date": "2024-01-01",
    "date-time": "2024-01-01T00:00:00Z",
    "uuid": "00000000-0000-4000-8000-000000000000",
    "uri": "https://example.com",
    "url": "https://example.com",
    "hostname": "example.com",
    "ipv4": "192.0.2.1",
    "ipv6": "2001:db8::1",
    "password": "",
    "byte": "",
    "binary": "",
}


@dataclass(frozen=True)
class OpenAPIImportOptions:
    spec_path: Path
    project_dir: Path
    base_url: str | None = None
    tags: tuple[str, ...] = ()
    dry_run: bool = False
    force: bool = False


@dataclass
class _Context:
    spec: dict[str, Any]
    spec_name: str
    notes: set[str] = field(default_factory=set)
    budget: int = 0  # schema nodes example_for may still visit for the current operation

    def resolve(self, node: Any, seen: frozenset[str] = frozenset()) -> tuple[Any, frozenset[str]]:
        """Follow ``$ref`` chains; returns the target and the refs followed (for cycle checks)."""
        while isinstance(node, dict) and isinstance(node.get("$ref"), str):
            ref = node["$ref"]
            if not ref.startswith("#/"):
                self.notes.add(f"external reference {ref!r} was not followed (only #/... references are resolved)")
                return {}, seen
            if ref in seen:
                return {}, seen
            seen = seen | {ref}
            node = self._pointer(ref)
        return node, seen

    def _pointer(self, ref: str) -> Any:
        node: Any = self.spec
        for part in ref[2:].split("/"):
            part = unquote(part).replace("~1", "/").replace("~0", "~")  # a URI fragment: percent-decoded, then JSON Pointer escapes
            if not isinstance(node, dict) or part not in node:
                self.notes.add(f"reference {ref!r} does not exist in the spec")
                return {}
            node = node[part]
        return node


def import_openapi(options: OpenAPIImportOptions) -> ImportReport:
    """Generate one case per operation of the spec; raises :class:`ImporterError` for unusable specs."""
    spec, raw = _load_spec(options.spec_path)
    ctx = _Context(spec=spec, spec_name=options.spec_path.name)
    report = ImportReport(title=f"Import of the OpenAPI spec {options.spec_path.name}", dry_run=options.dry_run)
    base_url = _base_url(options.base_url, spec, report)
    wanted = {tag.lower() for tag in options.tags}
    taken: set[str] = set()
    planned: list[PlannedFile] = []
    operations = 0
    for path, item in _paths(spec):
        for method in _METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            source = f"{method.upper()} {path}"
            tags = operation.get("tags")
            op_tags = [str(tag) for tag in tags if isinstance(tag, (str, int)) and not isinstance(tag, bool)] if isinstance(tags, list) else []
            if wanted and not wanted.intersection(tag.lower() for tag in op_tags):
                continue
            operations += 1
            if operations > _MAX_OPERATIONS:
                report.skip(source, f"more than {_MAX_OPERATIONS} operations; narrow the import with --tag")
                continue
            if operation.get("deprecated") is True:
                report.notes.append(f"{source} is deprecated; a case was still generated")
            ctx.budget = _MAX_EXAMPLE_NODES
            try:
                case = _case(ctx, path, method, item, operation, op_tags, base_url, taken, report)
            except ValidationError as exc:
                report.skip(source, "not a valid test case: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:3]))
                continue
            except (TypeError, AttributeError, KeyError, ValueError, RecursionError) as exc:  # a malformed part of the spec (null tags, a list where a mapping belongs)
                report.skip(source, f"the operation could not be read ({type(exc).__name__}: part of it is not shaped as OpenAPI 3 describes); write this case by hand")
                continue
            planned.append(case_file(case, source))
    if planned:
        planned.append(PlannedFile(kind="data", relative=f"{SPECS_DIRNAME}/{options.spec_path.name}", content=raw, source=options.spec_path.name))
    if operations == 0:
        report.notes.append("no operations matched" + (f" the tags {', '.join(options.tags)}" if options.tags else ""))
    report.notes.extend(sorted(ctx.notes))
    write_plan(options.project_dir, planned, report, dry_run=options.dry_run, force=options.force)
    return report


# --------------------------------------------------------------------------- spec


def _load_spec(path: Path) -> tuple[dict[str, Any], bytes]:
    """The parsed spec and its bytes as read (read once, size-capped: the copy kept in the project is what was parsed)."""
    if not path.is_file():
        raise ImporterError(f"{path} is not a file")
    too_large = ImporterError(f"{path.name} is larger than {_MAX_SPEC_BYTES // (1024 * 1024)} MB")
    if path.stat().st_size > _MAX_SPEC_BYTES:
        raise too_large
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_SPEC_BYTES + 1)
        if len(raw) > _MAX_SPEC_BYTES:
            raise too_large
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ImporterError(f"cannot read {path.name} ({type(exc).__name__})") from None
    try:
        spec = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError, RecursionError) as exc:
        raise ImporterError(f"cannot parse {path.name} as {'JSON' if path.suffix.lower() == '.json' else 'YAML'} ({type(exc).__name__})") from None
    if not isinstance(spec, dict):
        raise ImporterError(f"{path.name}: expected a mapping at the top level")
    version = str(spec.get("openapi", ""))
    if not version.startswith("3."):
        raise ImporterError(f"{path.name}: only OpenAPI 3.x specs are supported (found {'swagger ' + str(spec['swagger']) if 'swagger' in spec else 'no openapi version'})")
    if not isinstance(spec.get("paths"), dict) or not spec["paths"]:
        raise ImporterError(f"{path.name}: the spec has no paths")
    return spec, raw


def _paths(spec: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(str(path), item) for path, item in spec["paths"].items() if isinstance(item, dict)]


def _base_url(explicit: str | None, spec: dict[str, Any], report: ImportReport) -> str | None:
    if explicit is not None:
        if not _is_base_url(explicit):
            raise ImporterError("--base-url must be an http(s) URL with a host")
        return explicit.rstrip("/")
    servers = spec.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        server = servers[0]
        url = str(server.get("url", ""))
        variables = server.get("variables") if isinstance(server.get("variables"), dict) else {}

        def default(match: re.Match[str]) -> str:
            variable = variables.get(match.group(1))
            return str(variable.get("default", match.group(0))) if isinstance(variable, dict) else match.group(0)

        url = _SERVER_VARIABLE.sub(default, url)
        if _is_base_url(url):
            return url.rstrip("/")
        report.notes.append(f"the spec's first server URL {url!r} is not absolute; pass --base-url so the cases know where to send requests")
    else:
        report.notes.append("the spec lists no servers; pass --base-url so the cases know where to send requests")
    return None


def _is_base_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and parts.username is None and re.match(BASE_URL_PATTERN, value) is not None


# --------------------------------------------------------------------------- cases


def _case(
    ctx: _Context,
    path: str,
    method: str,
    item: dict[str, Any],
    operation: dict[str, Any],
    op_tags: list[str],
    base_url: str | None,
    taken: set[str],
    report: ImportReport,
) -> TestCase:
    test_data: dict[str, TestDataValue] = {}
    parameters = _parameters(ctx, item, operation)
    request_path = _fill_path(ctx, path, parameters, test_data)
    query = {name: _no_placeholders(_text(value)) for name, value in _required(ctx, parameters, "query")}
    headers = {name: _no_placeholders(_text(value)) for name, value in _required(ctx, parameters, "header")}
    auth = _auth(ctx, operation, test_data, report, headers, query)
    body = _request_body(ctx, operation)
    if body is not None:
        body = _escape_tree(body)
    status, schema_ref = _expected(ctx, operation)
    shown_query = "?" + "&".join(f"{quote(name, safe='')}={quote(value, safe='{}')}" for name, value in query.items()) if query else ""
    expected = f"the response status should be {status}"
    if schema_ref is not None:
        expected += f' and the response body should match the schema "{schema_ref}"'
    api = ApiCall(
        method=method.upper(),  # type: ignore[arg-type]
        path=request_path,
        headers=headers,
        query=query,  # type: ignore[arg-type]
        body=body,
        auth=auth,
        expect=ApiExpect(status=int(status), json_schema=schema_ref),
    )
    steps = [Step(action=f'I send a {method.upper()} request to "{request_path}{shown_query}"' + (" with a JSON body" if body is not None else ""), expected=expected, api=api)]
    operation_id = operation.get("operationId")
    base = _kebab(str(operation_id)) if isinstance(operation_id, str) and operation_id.strip() else f"{method}-{path}"
    summary = str(operation.get("summary") or "").strip()
    title = summary or f"{method.upper()} {path}"
    details = str(operation.get("description") or "").strip()
    description = f"Generated from {ctx.spec_name} {method.upper()} {path}." + (f" {details[:1000]}" if details else "")
    return TestCase(
        id=unique_case_id(base, taken),
        title=title[:300],
        description=description,
        tags=list(dict.fromkeys([*API_TAGS, *op_tags])),
        environment=EnvironmentRef(base_url=base_url),
        test_data=test_data,
        steps=steps,
    )


def _kebab(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name)


def _parameters(ctx: _Context, item: dict[str, Any], operation: dict[str, Any]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in [*(item.get("parameters") or []), *(operation.get("parameters") or [])]:
        parameter, _ = ctx.resolve(raw)
        if isinstance(parameter, dict) and "name" in parameter and "in" in parameter:
            merged[(str(parameter["name"]), str(parameter["in"]))] = parameter
    return list(merged.values())


def _required(ctx: _Context, parameters: list[dict[str, Any]], where: str) -> list[tuple[str, Any]]:
    found = []
    for parameter in parameters:
        if parameter["in"] == where and parameter.get("required") is True:
            if where == "header" and str(parameter["name"]).lower() in ("authorization", "accept", "content-type"):
                continue
            found.append((str(parameter["name"]), _parameter_value(ctx, parameter)))
    return found


def _parameter_value(ctx: _Context, parameter: dict[str, Any]) -> Any:
    if "example" in parameter:
        return parameter["example"]
    examples = parameter.get("examples")
    if isinstance(examples, dict) and examples:
        first, _ = ctx.resolve(next(iter(examples.values())))
        if isinstance(first, dict) and "value" in first:
            return first["value"]
    return example_for(ctx, parameter.get("schema") or {})


def _fill_path(ctx: _Context, path: str, parameters: list[dict[str, Any]], test_data: dict[str, TestDataValue]) -> str:
    by_name = {str(p["name"]): p for p in parameters if p["in"] == "path"}
    keys: dict[str, str] = {}  # path parameter name -> its test_data key (pet-id and pet_id get different keys)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        key = keys.get(name)
        if key is None:
            key = keys[name] = _free_key(data_key(name) or "path_value", test_data)
            parameter = by_name.get(name, {"schema": {"type": "string"}})
            test_data[key] = _no_placeholders(_text(_parameter_value(ctx, parameter))) or "1"
        return "{{" + key + "}}"

    return _PATH_PARAMETER.sub(replace, path)


def _free_key(base: str, test_data: dict[str, TestDataValue]) -> str:
    """``base``, or ``base_2``, ``base_3``... when a value already uses it."""
    key, number = base, 1
    while key in test_data:
        number += 1
        key = f"{base}_{number}"
    return key


def _auth(ctx: _Context, operation: dict[str, Any], test_data: dict[str, TestDataValue], report: ImportReport, headers: dict[str, str], query: dict[str, str]) -> ApiAuth | None:
    """Secret references for the operation's first security requirement, placed as headers, query parameters or auth."""
    requirements = operation.get("security", ctx.spec.get("security"))
    if not isinstance(requirements, list) or not requirements or not isinstance(requirements[0], dict):
        return None
    components = ctx.spec.get("components")
    schemes = components.get("securitySchemes") if isinstance(components, dict) else None
    schemes = schemes if isinstance(schemes, dict) else {}
    auth: ApiAuth | None = None
    for name in requirements[0]:
        scheme, _ = ctx.resolve(schemes.get(name, {}))
        if not isinstance(scheme, dict):
            continue
        key = _free_key(data_key(str(name).lower()) or "credential", test_data)  # never the key of a path parameter
        secret = secret_name(str(name))
        test_data[key] = SecretRef(secret=secret)
        report.add_secret(secret)
        placeholder = "{{" + key + "}}"
        kind = str(scheme.get("type", "")).lower()
        where = str(scheme.get("in", "header"))
        if kind == "apikey" and where == "query":
            query[str(scheme.get("name", name))] = placeholder
        elif kind == "apikey" and where == "cookie":
            headers["Cookie"] = f"{scheme.get('name', name)}={placeholder}"
        elif kind == "apikey":
            headers[str(scheme.get("name", "X-API-Key"))] = placeholder
        elif kind == "http" and str(scheme.get("scheme", "")).lower() == "basic":
            auth = ApiAuth(type="basic", value=placeholder)
        else:
            auth = ApiAuth(type="bearer", value=placeholder)
    return auth


def _escape_tree(node: Any) -> Any:
    """A JSON example with ``{{``/``}}`` in its strings broken apart, so they are never read as placeholders."""
    if isinstance(node, dict):
        return {_no_placeholders(str(key)): _escape_tree(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_escape_tree(item) for item in node]
    return _no_placeholders(node) if isinstance(node, str) else node


def _request_body(ctx: _Context, operation: dict[str, Any]) -> Any | None:
    body, _ = ctx.resolve(operation.get("requestBody"))
    if not isinstance(body, dict):
        return None
    media = _json_media(body.get("content"))
    if media is None:
        return None
    if "example" in media:
        return media["example"]
    examples = media.get("examples")
    if isinstance(examples, dict) and examples:
        first, _ = ctx.resolve(next(iter(examples.values())))
        if isinstance(first, dict) and "value" in first:
            return first["value"]
    return example_for(ctx, media.get("schema") or {})


def _json_media(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, dict):
        return None
    for media_type, media in content.items():
        if isinstance(media, dict) and (media_type == "application/json" or media_type.endswith("+json")):
            return media
    return None


def _expected(ctx: _Context, operation: dict[str, Any]) -> tuple[str, str | None]:
    responses = operation.get("responses") if isinstance(operation.get("responses"), dict) else {}
    codes = sorted(str(code) for code in responses if re.fullmatch(r"2\d\d", str(code)))
    if not codes:
        ctx.notes.add("operations without a documented 2xx response expect 200")
        return "200", None
    code = codes[0]
    response, _ = ctx.resolve(responses.get(code) if code in responses else responses.get(int(code)))
    media = _json_media(response.get("content")) if isinstance(response, dict) else None
    if media is None or not isinstance(media.get("schema"), dict):
        return code, None
    schema = media["schema"]
    if isinstance(schema.get("$ref"), str) and schema["$ref"].startswith("#/"):
        return code, f"{ctx.spec_name}{schema['$ref']}"
    pointer = _response_pointer(operation, code, ctx)
    return code, f"{ctx.spec_name}{pointer}"


def _response_pointer(operation: dict[str, Any], code: str, ctx: _Context) -> str:
    for path, item in _paths(ctx.spec):
        for method in _METHODS:
            if item.get(method) is operation:
                escaped = path.replace("~", "~0").replace("/", "~1")
                return f"#/paths/{escaped}/{method}/responses/{code}/content/application~1json/schema"
    return "#/"


# --------------------------------------------------------------------------- examples


def example_for(ctx: _Context, schema: Any, depth: int = 0, seen: frozenset[str] = frozenset()) -> Any:
    """An example value for ``schema``: its example, default or first enum value, else a typed placeholder."""
    schema, seen = ctx.resolve(schema, seen)
    if not isinstance(schema, dict) or depth > _MAX_DEPTH or ctx.budget <= 0:
        return None
    ctx.budget -= 1
    for key in ("example", "default", "const"):
        if key in schema:
            return schema[key]
    if isinstance(schema.get("examples"), list) and schema["examples"]:
        return schema["examples"][0]
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    if isinstance(schema.get("allOf"), list):
        merged: dict[str, Any] = {}
        for part in schema["allOf"]:
            value = example_for(ctx, part, depth + 1, seen)
            if isinstance(value, dict):
                merged.update(value)
        return merged
    for key in ("oneOf", "anyOf"):
        if isinstance(schema.get(key), list) and schema[key]:
            return example_for(ctx, schema[key][0], depth + 1, seen)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), None)
    if kind == "object" or (kind is None and isinstance(schema.get("properties"), dict)):
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        result = {}
        for name, sub in properties.items():
            sub_resolved, _ = ctx.resolve(sub, seen)
            if isinstance(sub_resolved, dict) and sub_resolved.get("readOnly") is True:
                continue
            value = example_for(ctx, sub, depth + 1, seen)
            if value is not None:
                result[str(name)] = value
        return result
    if kind == "array":
        item = example_for(ctx, schema.get("items") or {}, depth + 1, seen)
        return [] if item is None else [item]
    if kind == "integer":
        return int(schema.get("minimum", 1))
    if kind == "number":
        return float(schema.get("minimum", 1.0))
    if kind == "boolean":
        return True
    if kind == "string":
        return _STRING_FORMATS.get(str(schema.get("format", "")), "string")
    return None


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return "" if value is None else str(value)


def _no_placeholders(text: str) -> str:
    return text.replace("{{", "{ {").replace("}}", "} }")
