"""Import OpenAPI specifications and generate functional Gherkin API tests.

Ported from ``helper_scripts/generate_api_functional_gherkin_test.py`` as a
first-class project tool: specs are parsed into structured summaries, prompts
are built from those summaries, and the LLM call goes through the project's
LiteLLM chat model (``agents_llm_config``) instead of a raw OpenAI client, so
no ``OPENAI_API_KEY`` is required.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

import aiofiles
import yaml
from testzeus_hercules.utils.gherkin_helper import split_feature_file

_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
_MAX_ENDPOINTS = 100
_MAX_REF_DEPTH = 20

# Per-endpoint prompt budget: a request-body schema whose serialized JSON form
# exceeds this many characters is truncated (with a marker) so the total prompt
# stays bounded even for specs with deep or huge schemas on all 100 endpoints.
_MAX_SCHEMA_CHARS = 2000
_SCHEMA_TRUNCATION_MARKER = " ...[truncated]"


class _SchemaTruncated(Exception):
    """Internal: raised by the bounded JSON sink to abort ``json.dump`` at the cap."""


class _BoundedJsonSink:
    """Minimal ``write()`` sink for :func:`json.dump` that aborts past ``limit`` characters.

    Aborting mid-walk (instead of serializing first and slicing afterwards)
    keeps the work bounded for aliased/deep schemas that would expand to
    hundreds of megabytes of JSON.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._written = 0
        self.chunks: list[str] = []

    def write(self, text: str) -> int:
        self._written += len(text)
        self.chunks.append(text)
        if self._written > self._limit:
            raise _SchemaTruncated
        return len(text)


def _bounded_schema_block(schema: Any) -> Any:
    """Cap the serialized size of a request-body schema for the LLM prompt.

    Schemas whose JSON form fits ``_MAX_SCHEMA_CHARS`` pass through unchanged
    (still a dict); oversized ones become a truncated JSON string suffixed with
    ``_SCHEMA_TRUNCATION_MARKER``. Serialization is aborted as soon as the cap
    is exceeded, so pathological schemas cannot blow up time or memory.
    """
    sink = _BoundedJsonSink(_MAX_SCHEMA_CHARS)
    try:
        json.dump(schema, sink, indent=2, default=str)
    except _SchemaTruncated:
        return "".join(sink.chunks)[:_MAX_SCHEMA_CHARS].rstrip() + _SCHEMA_TRUNCATION_MARKER
    except (TypeError, ValueError):
        return str(schema)[:_MAX_SCHEMA_CHARS]
    return schema


def _merge_parameters(path_item: dict, operation: dict) -> list[dict]:
    """Path-item level parameters plus operation parameters; operation wins on (name, in) collision.

    OpenAPI applies ``parameters`` declared on the path item to every operation
    underneath it, so a required path parameter declared there must reach the
    prompt even though the endpoint's ``parameters`` key stays operation-level.
    """
    merged: dict[tuple[str, str], dict] = {}
    for source in (path_item.get("parameters"), operation.get("parameters")):
        if not isinstance(source, list):
            continue
        for parameter in source:
            if not isinstance(parameter, dict):
                continue
            merged[(str(parameter.get("name")), str(parameter.get("in")))] = parameter
    return list(merged.values())


def _endpoint_summary_text(operation: dict, parameters: list[dict]) -> str:
    """Operation summary plus a compact listing of the merged (path-item + operation) parameters."""
    parts = [str(operation.get("summary") or "").strip()]
    rendered = []
    for parameter in parameters:
        name = str(parameter.get("name") or "").strip()
        if not name:
            continue
        location = str(parameter.get("in") or "").strip()
        flag = ", required" if parameter.get("required") else ""
        rendered.append(f"{name} ({location}{flag})" if location else name)
    if rendered:
        parts.append("Parameters: " + "; ".join(rendered))
    return " | ".join(part for part in parts if part)


def _resolve_pointer(spec: dict, pointer: str) -> Any | None:
    """Resolve a local JSON pointer like ``#/components/schemas/User``.

    Returns ``None`` when the pointer cannot be resolved. Handles the standard
    ``~1`` (``/``) and ``~0`` (``~``) escapes and array index tokens.
    """
    node: Any = spec
    pointer = pointer.lstrip("#").lstrip("/")
    if not pointer:
        return None
    for raw_token in pointer.split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            if token not in node:
                return None
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            return None
    return node


def _resolve_local_refs(node: Any, spec: dict, depth: int = 0, active_refs: frozenset = frozenset(), resolved_nodes: dict[tuple[int, frozenset, int], Any] | None = None) -> Any:
    """Recursively resolve local ``#/...`` ``$ref`` nodes against ``spec``.

    External and unresolvable refs are left untouched. Cyclic refs (a ref
    appearing twice on the current resolution path) and chains deeper than
    ``_MAX_REF_DEPTH`` keep their ``$ref`` node instead of recursing.

    Resolution is memoized per top-level call by node identity plus the
    active-ref/depth context, so shared/aliased subtree nodes (a DAG, common in
    specs built in-process) are resolved once instead of exponentially often.
    The cache is local to the call, so the input spec is never mutated and
    results of different calls never share objects.
    """
    if resolved_nodes is None:
        resolved_nodes = {}
    if depth > _MAX_REF_DEPTH:
        return node
    memo_key = (id(node), active_refs, depth)
    if memo_key in resolved_nodes:
        return resolved_nodes[memo_key]
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            if ref in active_refs:
                resolved: Any = node
            else:
                target = _resolve_pointer(spec, ref)
                resolved = node if target is None else _resolve_local_refs(target, spec, depth + 1, active_refs | {ref}, resolved_nodes)
        else:
            resolved = {key: _resolve_local_refs(value, spec, depth, active_refs, resolved_nodes) for key, value in node.items()}
    elif isinstance(node, list):
        resolved = [_resolve_local_refs(item, spec, depth, active_refs, resolved_nodes) for item in node]
    else:
        return node
    resolved_nodes[memo_key] = resolved
    return resolved


def read_spec(path: str) -> dict:
    """Read an OpenAPI spec from a YAML or JSON file.

    Args:
        path: Path to the OpenAPI spec file (``.yaml``, ``.yml`` or ``.json``).
            Files with a UTF-8 BOM are handled transparently.

    Returns:
        The parsed spec as a dict.

    Raises:
        ValueError: If the file cannot be parsed as OpenAPI YAML/JSON (including
            parser recursion/memory failures on absurdly nested documents) or
            does not carry an ``openapi`` (3.x) or ``swagger`` (2.0) version key.
    """
    extension = os.path.splitext(path)[1].lower()
    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()
    try:
        if extension == ".json":
            parsed = json.loads(content)
        elif extension in (".yaml", ".yml"):
            parsed = yaml.safe_load(content)
        else:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = yaml.safe_load(content)
    except (json.JSONDecodeError, yaml.YAMLError, RecursionError, MemoryError) as exc:
        # RecursionError/MemoryError: parsers recurse per nesting level, so absurdly
        # deep documents blow the stack instead of raising a parse error.
        raise ValueError(f"Failed to parse OpenAPI spec '{path}': {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"OpenAPI spec '{path}' must contain a mapping at the top level, got {type(parsed).__name__}.")
    if "openapi" not in parsed and "swagger" not in parsed:
        raise ValueError(f"OpenAPI spec '{path}' is missing the required 'openapi' or 'swagger' version key.")
    return parsed


def summarize_spec(spec: dict) -> dict:
    """Summarize an OpenAPI spec into title, version, base URL and endpoint list.

    Supports both OpenAPI 3.x (``servers``) and Swagger 2.0 (``host`` +
    ``schemes`` + ``basePath``). Local ``#/...`` ``$ref`` nodes are resolved
    (path-item refs would otherwise silently drop endpoints and schema refs
    would reach the LLM prompt unresolved); external/unresolvable refs are left
    as-is. Non-HTTP path-item keys are skipped and the endpoint list is capped
    at 100 entries.

    Path-item level ``parameters`` are merged with operation-level ones
    (operation wins on ``(name, in)`` collisions) into the endpoint's summary
    text only, so required path parameters declared on the path item are
    visible to the LLM. Schema blocks stay structured here; the prompt builder
    (:func:`build_prompt`) caps each endpoint's serialized schema size.
    """
    info = spec.get("info") or {}
    if not isinstance(info, dict):
        info = {}

    base_url = ""
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict) and servers[0].get("url"):
        base_url = str(servers[0]["url"])
    elif spec.get("host"):
        schemes = spec.get("schemes") or ["https"]
        base_path = spec.get("basePath") or ""
        base_url = f"{schemes[0]}://{spec['host']}{base_path}"

    endpoints: list[dict] = []
    paths = spec.get("paths") or {}
    if isinstance(paths, dict):
        for path, raw_path_item in paths.items():
            if not isinstance(path, str) or not isinstance(raw_path_item, dict):
                continue
            path_item = _resolve_local_refs(raw_path_item, spec)
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if not isinstance(method, str) or method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                    continue
                if len(endpoints) >= _MAX_ENDPOINTS:
                    break
                request_body_schema: Any = {}
                request_body = operation.get("requestBody")
                if isinstance(request_body, dict):
                    request_body_schema = request_body.get("content") or {}
                else:
                    # Swagger 2.0: request body arrives as an `in: body` parameter.
                    for param in operation.get("parameters") or []:
                        if isinstance(param, dict) and param.get("in") == "body":
                            request_body_schema = param.get("schema") or {}
                            break
                endpoints.append(
                    {
                        "method": method.upper(),
                        "path": path,
                        "operation_id": operation.get("operationId", ""),
                        "summary": _endpoint_summary_text(operation, _merge_parameters(path_item, operation)),
                        "parameters": operation.get("parameters") or [],
                        "request_body_schema": request_body_schema,
                    }
                )

    return {
        "title": info.get("title", ""),
        "version": info.get("version", ""),
        "base_url": base_url,
        "endpoints": endpoints,
    }


def _bounded_spec_summary(spec_summary: dict) -> dict:
    """Copy of a spec summary with each endpoint's schema block size-capped for the prompt.

    :func:`summarize_spec` keeps ``request_body_schema`` structured (resolvers
    and callers rely on that shape); this is the last step before
    ``json.dumps``, exactly where the unbounded prompt cost used to arise.
    """
    return {
        **spec_summary,
        "endpoints": [{**endpoint, "request_body_schema": _bounded_schema_block(endpoint.get("request_body_schema"))} for endpoint in spec_summary.get("endpoints") or []],
    }


def build_prompt(spec_summary: dict, number_of_testcase: int) -> str:
    """Build the LLM prompt asking for functional Gherkin API test scenarios.

    Each endpoint's serialized schema block is truncated to
    ``_MAX_SCHEMA_CHARS`` characters (ellipsis marker), so the total prompt
    stays bounded even for 100 endpoints with huge request-body schemas.
    """
    spec_json = json.dumps(_bounded_spec_summary(spec_summary), indent=2, default=str)
    return (
        f"Analyse the following API specification summary and produce functional API test scenarios in Gherkin format.\n"
        f"\n"
        f"Requirements:\n"
        f"- Target about {number_of_testcase} scenarios in total across all features.\n"
        f"- Write ONE Feature per endpoint-group (all scenarios for the same group of endpoints share one Feature).\n"
        f"- Each Scenario must have:\n"
        f"  * 'Given': a realistic precondition (the service is available at the base URL, required auth/token is set up).\n"
        f"  * 'When': a concrete call to the endpoint with its HTTP method and path, including a sample request body when "
        f"applicable. Use angle-bracket placeholders (e.g. <user_id>) for dynamic values.\n"
        f"  * 'Then': the expected HTTP status plus concrete response assertions (status code, field values, data types, "
        f"null handling).\n"
        f"- For every critical endpoint include positive scenarios and at least one negative scenario (4xx / validation "
        f"failures such as missing fields, wrong types, invalid or unknown ids).\n"
        f"- Validate data correctness, data types, null value handling and adherence to the specification. Cover "
        f"functional, positive, negative, error handling and integration cases.\n"
        f"- NEVER use markdown fences (```) or headings. Output ONLY the Gherkin text, no commentary, no explanation.\n"
        f"\n"
        f"Example output format:\n"
        f"\n"
        f"Feature: User management\n"
        f"    Scenario: Create_user_successfully\n"
        f"        Given the service is available\n"
        f'        When I send a POST request to "/users" with body {{"name": "<name>"}}\n'
        f"        Then the response status is 201\n"
        f"\n"
        f"OpenAPI specification summary:\n"
        f"{spec_json}"
    )


async def generate_feature_text(spec_path: str, number_of_testcase: int = 20, llm: Any | None = None) -> str:
    """Generate the full Gherkin feature text for one OpenAPI spec file via an LLM.

    Args:
        spec_path: Path to the OpenAPI spec file (YAML or JSON).
        number_of_testcase: Target number of test scenarios to request.
        llm: Optional LangChain chat model. Defaults to the project's
            ``planner_agent`` LiteLLM chat model (resolved lazily so that spec
            parsing and validation work without any LLM configuration).

    Returns:
        The cleaned Gherkin text (markdown fences and preamble removed).
    """
    spec = read_spec(spec_path)
    prompt = build_prompt(summarize_spec(spec), number_of_testcase)
    if llm is None:
        from testzeus_hercules.utils.litellm_helper import get_litellm_chat_model

        llm = get_litellm_chat_model("planner_agent")
    from testzeus_hercules.utils.gherkin_generator import clean_gherkin_output

    response = await llm.ainvoke(prompt)
    # LLM doubles and some backends return a plain string instead of a message
    # object; normalize like prd_parser does.
    content = getattr(response, "content", response)
    if not isinstance(content, str):
        content = str(content)
    return clean_gherkin_output(content)


async def generate_api_tests(input_files: list[str], output_dir: str, number_of_testcase: int = 20, llm: Any | None = None) -> list[str]:
    """Generate per-scenario Gherkin feature files for each OpenAPI spec.

    For every input spec, the LLM-generated feature text is split into one
    feature file per scenario and written into ``{output_dir}/{spec_basename}/``.

    Args:
        input_files: Paths to OpenAPI spec files (YAML or JSON).
        output_dir: Output folder where the per-spec subfolders are created.
        number_of_testcase: Target number of test scenarios per spec.
        llm: Optional LangChain chat model (see :func:`generate_feature_text`).

    Returns:
        The list of written feature file paths.
    """
    written: list[str] = []
    for spec_path in input_files:
        feature_text = await generate_feature_text(spec_path, number_of_testcase=number_of_testcase, llm=llm)
        base_name = os.path.splitext(os.path.basename(spec_path))[0]
        subfolder = os.path.join(output_dir, base_name)
        os.makedirs(subfolder, exist_ok=True)

        combined_file = os.path.join(subfolder, f"_combined_{base_name}.feature")
        async with aiofiles.open(combined_file, "w", encoding="utf-8") as f:
            await f.write(feature_text)
        try:
            split = await split_feature_file(combined_file, subfolder)
        finally:
            os.remove(combined_file)
        written.extend(entry["output_file"] for entry in split)
    return written


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: generate Gherkin API tests from OpenAPI spec files."""
    parser = argparse.ArgumentParser(description="Generate functional Gherkin API tests from OpenAPI spec files.")
    parser.add_argument(
        "input_files",
        metavar="input_files",
        type=str,
        nargs="+",
        help="One or more OpenAPI spec files (YAML or JSON).",
    )
    parser.add_argument(
        "--output",
        metavar="output",
        type=str,
        required=True,
        help="Output folder path where feature files will be generated.",
    )
    parser.add_argument(
        "--number_of_testcase",
        metavar="number_of_testcase",
        type=int,
        default=20,
        help="The number of test cases to generate (default: 20).",
    )
    args = parser.parse_args(argv)

    try:
        written = asyncio.run(generate_api_tests(args.input_files, args.output, number_of_testcase=args.number_of_testcase))
    except Exception as exc:
        print(f"Error generating API tests: {exc}")
        return 1

    for path in written:
        print(path)
    print(f"Generated {len(written)} feature file(s) in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
