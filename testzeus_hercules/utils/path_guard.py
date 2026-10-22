"""Confine model/client-supplied filesystem paths to configured roots.

Two entry points share this module:

- ``resolve_within(path, roots)``: strict containment check against explicit roots
  (MCP session / PRD paths).
- ``resolve_in_roots(path)``: API tooling (multipart uploads, Hurl suites, OpenAPI
  specs); roots default to ``TEST_DATA_PATH``, ``PROJECT_SOURCE_ROOT`` and any extra
  roots in ``API_ALLOWED_PATHS`` (``os.pathsep``-separated). Relative paths are tried
  against each root.

Paths are resolved with ``os.path.realpath`` first, so ``..`` traversal and symlinks
that leave a root are rejected.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import List, Optional

from testzeus_hercules.config import get_global_conf


class PathOutsideRootsError(ValueError):
    """The path resolves outside every allowed root."""


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def is_within(path: str, root: str) -> bool:
    """True when ``path`` (after symlink resolution) is ``root`` or inside it."""
    real_root = _real(root)
    real_path = _real(path)
    try:
        return os.path.commonpath([real_path, real_root]) == real_root
    except ValueError:  # e.g. different drives on Windows
        return False


def resolve_within(path: str, roots: Iterable[str], label: str = "path") -> str:
    """Return the realpath of ``path`` when it lies inside one of ``roots``.

    Symlinks are resolved before the prefix check, so a link inside a root that
    points outside it is rejected.

    Raises:
        PathOutsideRootsError: ``path`` is outside every root (or no root is configured).
    """
    candidates = [root for root in roots if root]
    if not path:
        raise PathOutsideRootsError(f"{label} is empty")
    for root in candidates:
        if is_within(path, root):
            return _real(path)
    raise PathOutsideRootsError(
        f"{label} {os.path.basename(path.rstrip(os.sep)) or path!r} is outside the allowed roots; place it under the project/test-data directory or add its directory to the allowed roots setting."
    )


class PathConfinementError(PathOutsideRootsError):
    """Raised by ``resolve_in_roots`` when a path resolves outside every allowed root."""


def allowed_roots() -> List[str]:
    """Return the de-duplicated, symlink-resolved roots model-supplied paths may live in."""
    config = get_global_conf().get_config()
    candidates: List[str] = []
    for key in ("TEST_DATA_PATH", "PROJECT_SOURCE_ROOT"):
        value = config.get(key)
        if value:
            candidates.append(str(value))
    extra = str(config.get("API_ALLOWED_PATHS") or "")
    candidates.extend(part.strip() for part in extra.split(os.pathsep) if part.strip())
    roots: List[str] = []
    for candidate in candidates:
        resolved = os.path.realpath(os.path.abspath(candidate))
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _inside(resolved: str, root: str) -> bool:
    try:
        return os.path.commonpath([resolved, root]) == root
    except ValueError:  # e.g. different drives on Windows
        return False


def resolve_in_roots(path: str, roots: Optional[Iterable[str]] = None) -> str:
    """Resolve ``path`` to a real absolute path that is inside one of the roots.

    Relative paths are tried against each root first (test data first), then
    the working directory; the first existing, confined candidate wins, so a
    missing file still reports "not found" rather than a confinement error.

    Raises:
        PathConfinementError: the path is empty, contains a NUL byte or
            resolves outside every allowed root.
    """
    text = str(path or "")
    if not text.strip() or "\x00" in text:
        raise PathConfinementError("Path is empty or invalid.")
    allowed = [os.path.realpath(os.path.abspath(root)) for root in roots] if roots is not None else allowed_roots()
    if os.path.isabs(text):
        candidates = [text]
    else:
        candidates = [os.path.join(root, text) for root in allowed] + [os.path.abspath(text)]
    confined: List[str] = []
    escaped = False
    for candidate in candidates:
        resolved = os.path.realpath(candidate)
        if any(_inside(resolved, root) for root in allowed):
            if os.path.exists(resolved):
                return resolved
            confined.append(resolved)
        elif os.path.lexists(candidate):
            # Something exists at the requested name but resolves outside the
            # roots (traversal or a symlink): refuse instead of falling back.
            escaped = True
    if confined and not escaped:
        return confined[0]
    raise PathConfinementError(
        f"Access denied: '{text}' is outside the allowed roots ({', '.join(allowed) or 'none configured'}). "
        "Place the file under the test data folder or the project root, or list its folder in API_ALLOWED_PATHS."
    )
