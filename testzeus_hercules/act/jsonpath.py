"""JSONPath for API and SQL assertions: one parser for matching, no-match hints and path shape.

``jsonpath_ng`` is a core dependency, imported on first use; a missing package (a broken
install) raises :class:`ImportError` with the install hint, which callers turn into an
``error`` verdict.
"""

from __future__ import annotations

from typing import Any

INSTALL_HINT = "jsonpath_ng is not installed; it is a Hercules dependency, so reinstall Hercules: pip install --force-reinstall testzeus-hercules"


def _parse(path: str) -> Any:
    try:
        from jsonpath_ng.ext import parse
    except ImportError as exc:
        raise ImportError(INSTALL_HINT) from exc
    return parse(path)


def find(document: Any, path: str) -> list[Any]:
    """The values ``path`` matches in ``document`` (possibly none); parse errors propagate."""
    return [match.value for match in _parse(path).find(document)]


def closest_parent(document: Any, path: str) -> dict[str, Any] | None:
    """The closest resolvable parent of ``path``: its location and keys (a dict's, a list's indexes, None for a scalar)."""
    expression = _parse(path)
    candidates = []
    node = expression
    while getattr(node, "left", None) is not None:
        node = node.left
        candidates.append(node)
    for position, candidate in enumerate(candidates):
        if position > 0 and position == len(candidates) - 1 and str(candidate) == "$":
            continue
        matches = candidate.find(document)
        if not matches:
            continue
        value = matches[0].value
        location = str(candidate).replace(".[", "[")
        if not location.startswith("$"):
            location = f"$.{location}" if location else "$"
        if isinstance(value, dict):
            return {"location": location, "keys": sorted(str(key) for key in value)}
        if isinstance(value, list):
            return {"location": location, "keys": [f"[{index}]" for index in range(min(len(value), 20))]}
        return {"location": location, "keys": None}
    return None


def is_singular(path: str) -> bool:
    """True when ``path`` selects at most one value (root, single names and single indexes only)."""
    try:
        from jsonpath_ng import Child, Fields, Index, Root, This
    except ImportError as exc:
        raise ImportError(INSTALL_HINT) from exc

    def singular(node: Any) -> bool:
        if type(node) is Child:
            return singular(node.left) and singular(node.right)
        if type(node) in (Root, This, Index):
            return True
        if type(node) is Fields:
            return len(node.fields) == 1 and node.fields[0] != "*"
        return False

    return singular(_parse(path))


def leaf_name(path: str) -> str | None:
    """The field name of the path's last segment, or None when it is not a named field."""
    try:
        from jsonpath_ng import Fields
    except ImportError as exc:
        raise ImportError(INSTALL_HINT) from exc
    leaf = _parse(path)
    while getattr(leaf, "right", None) is not None:
        leaf = leaf.right
    return str(leaf.fields[0]) if isinstance(leaf, Fields) and leaf.fields else None
