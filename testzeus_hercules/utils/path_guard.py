"""Confine model-supplied filesystem paths to the configured roots.

Tools that read local files on the model's behalf (multipart uploads, Hurl
suites, OpenAPI specs) must not be steerable at arbitrary files. Every such
path is resolved with ``os.path.realpath`` (so ``..`` traversal and symlinks
are followed) and must then sit inside one of the allowed roots:

- the test data folder (``TEST_DATA_PATH``),
- the project source root (``PROJECT_SOURCE_ROOT``),
- any extra roots listed in ``API_ALLOWED_PATHS`` (``os.pathsep``-separated).
"""

import os
from typing import Iterable, List, Optional

from testzeus_hercules.config import get_global_conf


class PathConfinementError(ValueError):
    """Raised when a path resolves outside every allowed root."""


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
