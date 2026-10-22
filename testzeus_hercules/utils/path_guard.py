"""Confine model/client-supplied filesystem paths to configured roots."""

from __future__ import annotations

import os
from collections.abc import Iterable


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
