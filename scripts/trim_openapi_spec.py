#!/usr/bin/env python3
"""
Trim an OpenAPI/Swagger specification down to the paths a test suite actually
exercises, dropping the schema definitions those paths no longer reference.

The static data loader minifies every test-data file into each scenario's
prompt, so a full specification taxes every scenario; a trimmed one keeps the
prompt (and token cost) proportional to what the suite touches.

Usage:
    python scripts/trim_openapi_spec.py spec.json -o trimmed.json \
        --paths /api/pets /api/owners --path-prefix /api/v2/pets

JSON and YAML input/output are both supported (output format follows the
output file extension; stdout output is JSON). Standard library plus PyYAML
only — fully offline.
"""

import argparse
import copy
import json
import sys
from typing import Any, Dict, Iterable, List, Set, Tuple

# Schema sections whose unreferenced targets get dropped, keyed by the first
# pointer segment (Swagger 2.0) / first two segments (OpenAPI 3.x).
SCHEMA_SECTIONS_20 = "definitions"
SCHEMA_SECTIONS_3X = ("components", "schemas")


def _yaml():
    try:
        import yaml
    except ImportError as e:  # pragma: no cover - PyYAML is a main dependency
        raise SystemExit("PyYAML is required for YAML input/output; pip install pyyaml") from e
    return yaml


def load_spec(path: str) -> Dict[str, Any]:
    """Load a spec from a JSON or YAML file."""
    with open(path, "r", encoding="utf-8") as spec_file:
        text = spec_file.read()
    try:
        spec = json.loads(text)
    except json.JSONDecodeError:
        spec = _yaml().safe_load(text)
    if not isinstance(spec, dict):
        raise ValueError(f"{path}: spec must be a mapping/object")
    return spec


def dump_spec(spec: Dict[str, Any], path: str = None) -> None:
    """Write a spec to a file (or stdout when path is None), honoring the extension."""
    if path is not None and path.lower().endswith((".yaml", ".yml")):
        text = _yaml().safe_dump(spec, sort_keys=False, allow_unicode=True)
    else:
        text = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
    if path is None:
        sys.stdout.write(text)
    else:
        with open(path, "w", encoding="utf-8") as spec_file:
            spec_file.write(text)


def _unescape_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _iter_refs(node: Any):
    """Yield every local $ref pointer ('#/...') in a JSON-ish subtree."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/"):
                yield value
            else:
                yield from _iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_refs(item)


def _resolve_pointer(spec: Dict[str, Any], ref: str) -> Tuple[Iterable[str], Any]:
    """Resolve '#/a/b' to its path segments and target node (or ([], None)).

    Each segment is tried JSON-pointer-unescaped first (``a~1b`` -> ``a/b``), then
    raw: generators that skip the escaping emit refs like ``#/definitions/a~1b``
    for a schema literally named ``a~1b``.
    """
    node: Any = spec
    resolved: List[str] = []
    for raw in ref[2:].split("/"):
        if not isinstance(node, dict):
            return [], None
        unescaped = _unescape_token(raw)
        if unescaped in node:
            node = node[unescaped]
            resolved.append(unescaped)
        elif raw in node:
            node = node[raw]
            resolved.append(raw)
        else:
            return [], None
    return resolved, node


def _is_schema_pointer(segments: Iterable[str]) -> bool:
    segments = list(segments)
    if segments and segments[0] == SCHEMA_SECTIONS_20:
        return True
    return tuple(segments[:2]) == SCHEMA_SECTIONS_3X


def _collect_schema_refs(spec: Dict[str, Any], roots: List[Any]) -> Set[str]:
    """BFS over local $refs from roots, returning kept schema pointer keys.

    Refs into sections that are kept verbatim (responses, parameters, headers,
    requestBodies, ...) are followed too: their targets survive the trim, so the
    schemas they point at must be kept as well or the output would carry dangling
    $refs.
    """
    kept: Set[str] = set()
    queue = list(roots)
    seen_refs: Set[str] = set()
    while queue:
        node = queue.pop()
        for ref in _iter_refs(node):
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            segments, target = _resolve_pointer(spec, ref)
            if target is None:
                continue  # dangling ref in the source: left as-is
            if _is_schema_pointer(segments):
                kept.add("/".join(segments))
            queue.append(target)  # transitive: schemas referenced from kept schemas and kept non-schema sections
    return kept


def _prune_section(spec: Dict[str, Any], kept: Set[str], section: List[str]) -> int:
    """Keep only referenced entries of one schema section; return kept count."""
    container: Any = spec
    for segment in section[:-1]:
        container = container.get(segment) if isinstance(container, dict) else None
        if container is None:
            return 0
    entries = container.get(section[-1]) if isinstance(container, dict) else None
    if not isinstance(entries, dict):
        return 0
    prefix = "/".join(section) + "/"

    def _referenced(name: str) -> bool:
        if f"{prefix}{name}" in kept:
            return True
        # Refs are collected with JSON-pointer-unescaped segments; match the
        # unescaped form of the raw name too (e.g. "a~1b" -> "a/b").
        return f"{prefix}{_unescape_token(name)}" in kept

    survivors = {name: body for name, body in entries.items() if _referenced(name)}
    if survivors:
        entries.clear()
        entries.update(survivors)
        return len(survivors)
    # Nothing referenced: drop the (now empty) section entirely.
    del container[section[-1]]
    if section[:2] == list(SCHEMA_SECTIONS_3X) and isinstance(spec.get("components"), dict) and not spec["components"]:
        del spec["components"]
    return 0


def trim_spec(spec: Dict[str, Any], exact_paths: Iterable[str], path_prefixes: Iterable[str]) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """
    Return a trimmed copy of ``spec`` keeping only the requested paths and the
    schema definitions they (transitively) reference.
    """
    exact = set(exact_paths)
    prefixes = tuple(path_prefixes)
    if not exact and not prefixes:
        raise ValueError("Nothing to keep: pass --paths and/or --path-prefix")

    trimmed = copy.deepcopy(spec)
    declared = trimmed.get("paths")
    if not isinstance(declared, dict):
        raise ValueError("Spec has no 'paths' section to trim")

    trimmed["paths"] = {path: item for path, item in declared.items() if path in exact or any(path.startswith(prefix) for prefix in prefixes)}
    if not trimmed["paths"]:
        raise ValueError("No declared path matched the requested --paths/--path-prefix filters")

    # Every section that survives the trim (paths, responses, parameters, ...)
    # may reference schemas, so collect from the whole kept document.
    kept_refs = _collect_schema_refs(spec, [trimmed])
    swagger_20 = str(spec.get("swagger") or "").startswith("2")
    kept_schemas: Dict[str, int] = {}
    if swagger_20:
        kept_schemas["definitions"] = _prune_section(trimmed, kept_refs, ["definitions"])
    elif isinstance(spec.get("components"), dict):
        kept_schemas["components.schemas"] = _prune_section(trimmed, kept_refs, ["components", "schemas"])
    return trimmed, kept_schemas


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trim an OpenAPI/Swagger spec to the paths a suite exercises (and the schemas they reference).",
    )
    parser.add_argument("spec", help="Path to the OpenAPI/Swagger spec (JSON or YAML)")
    parser.add_argument("-o", "--output", help="Output file (.json/.yaml/.yml); defaults to JSON on stdout")
    parser.add_argument("--paths", nargs="+", default=[], help="Exact path values to keep")
    parser.add_argument("--path-prefix", action="append", default=[], help="Path prefix to keep (repeatable)")
    return parser


def main(argv: List[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        spec = load_spec(args.spec)
        trimmed, kept_schemas = trim_spec(spec, args.paths, args.path_prefix)
        dump_spec(trimmed, args.output)
    except Exception as e:  # noqa: BLE001 - CLI boundary: report any failure structurally
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(
        f"kept {len(trimmed['paths'])} path(s), {sum(kept_schemas.values())} referenced schema(s)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
