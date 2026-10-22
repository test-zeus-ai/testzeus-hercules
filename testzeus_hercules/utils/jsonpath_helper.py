"""
Shared JSONPath helper so every tool that queries a JSON document parses
paths identically (assertions, extraction, schema validation target
selection all go through this one function).

``jsonpath_ng`` is an optional dependency (shipped in the ``api`` extra), so
the import happens lazily inside :func:`find_json_path`. Importing this
module never fails; callers catch ``ImportError`` and turn it into a
structured tool result instead.
"""

from typing import Any, List, Optional

JSONPATH_NG_INSTALL_HINT = "jsonpath_ng is not installed. Install the API extras to enable JSONPath " "tooling: pip install 'testzeus-hercules[api]' (provides jsonpath-ng)."


def _ext_parse(path: str):
    """Import jsonpath_ng lazily and return its ext parser (raises ImportError)."""
    try:
        # Imported lazily: jsonpath_ng is an optional dependency and
        # core.tools.__init__ eager-imports every tool module, so a top-level
        # import here would break the whole product when the extra is absent.
        from jsonpath_ng.ext import parse
    except ImportError as e:
        raise ImportError(JSONPATH_NG_INSTALL_HINT) from e
    return parse(path)


def find_json_path(doc: Any, path: str) -> List[Any]:
    """
    Evaluate a JSONPath expression against a document and return the list of
    matched values (possibly empty).

    Raises:
        ImportError: when the optional ``jsonpath_ng`` package is missing.
        Exception: propagates JSONPath parse errors from jsonpath_ng so
            callers can surface them as structured ERROR results.
    """
    return [match.value for match in _ext_parse(path).find(doc)]


def closest_parent_keys(doc: Any, path: str) -> Optional[dict]:
    """
    Describe the closest resolvable parent of ``path``: the parent's location
    and its keys (dict) or indices (list), so a no-match failure can name what
    *does* exist instead of leaving the agent guessing.

    Returns None when nothing resolves (not even the root prefix), or a dict
    {"location": str, "keys": list[str] | None} otherwise (keys is None for a
    scalar parent).
    """
    expression = _ext_parse(path)
    # Walk up the expression tree: each .left strips the right-most segment,
    # so candidates run from the most specific parent up to the root.
    candidates = []
    node = expression
    while getattr(node, "left", None) is not None:
        node = node.left
        candidates.append(node)
    for position, candidate in enumerate(candidates):
        # The bare root is only a meaningful "closest parent" when it is the
        # immediate parent; if even the first segment fails to resolve, nothing
        # of the path resolves (documented None).
        if position > 0 and position == len(candidates) - 1 and str(candidate) == "$":
            continue
        matches = candidate.find(doc)
        if not matches:
            continue
        value = matches[0].value
        # jsonpath_ng renders an index segment as ".[0]"; normalize to "[0]".
        location = str(candidate).replace(".[", "[")
        if not location.startswith("$"):
            location = f"$.{location}" if location else "$"
        if isinstance(value, dict):
            return {"location": location, "keys": sorted(str(key) for key in value.keys())}
        if isinstance(value, list):
            return {"location": location, "keys": [f"[{index}]" for index in range(min(len(value), 20))]}
        return {"location": location, "keys": None}
    return None


def path_leaf_name(path: str) -> Optional[str]:
    """
    Return the plain field name of the path's right-most segment, or None when
    the leaf is not a named field (index, wildcard, filter, recursive descent).
    """
    try:
        from jsonpath_ng import Fields
    except ImportError as e:
        raise ImportError(JSONPATH_NG_INSTALL_HINT) from e

    leaf = _ext_parse(path)
    while getattr(leaf, "right", None) is not None:
        leaf = leaf.right
    if isinstance(leaf, Fields) and leaf.fields:
        return str(leaf.fields[0])
    return None
