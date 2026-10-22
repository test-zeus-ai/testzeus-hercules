"""Confine model/client-supplied filesystem paths to configured roots."""

from __future__ import annotations

import fnmatch
import logging
import os
from collections.abc import Iterable

_LOGGER = logging.getLogger(__name__)
_WARNED_OVERBROAD_ROOTS: set[str] = set()

# Document types a client may point prd_path at (URL fetches are validated separately).
PRD_ALLOWED_EXTENSIONS = frozenset({".md", ".markdown", ".txt", ".rst", ".pdf", ".docx"})

# Basenames that hold secrets/state and must never be read or served on behalf of a client
# (matched case-insensitively with fnmatch; every dotfile is refused as well).
SENSITIVE_NAME_PATTERNS = (
    "agents_llm_config*",
    "*.env",
    ".env*",
    "*.pem",
    "*.key",
    "sessions.json",
    "selector_store.json",
    "hercules_history.db*",
    "*.db",
    "*.sqlite*",
)


class PathOutsideRootsError(ValueError):
    """The path resolves outside every allowed root."""


class ForbiddenFileError(ValueError):
    """The file type or name is not permitted, even inside an allowed root."""


def is_sensitive_name(name: str) -> bool:
    """True for dotfiles and names matching ``SENSITIVE_NAME_PATTERNS`` (basename only)."""
    base = os.path.basename(str(name).rstrip("/\\")).lower()
    if not base:
        return False
    return base.startswith(".") or any(fnmatch.fnmatchcase(base, pattern) for pattern in SENSITIVE_NAME_PATTERNS)


def check_prd_document(path: str, label: str = "prd_path") -> None:
    """Refuse PRD files that are not plain documents or that look like secrets/state.

    Both the supplied name and the symlink-resolved name are checked, so a
    harmless-looking link cannot expose a sensitive target.

    Raises:
        ForbiddenFileError: unsupported extension or a sensitive file name.
    """
    for candidate in (path, _real(path)):
        if is_sensitive_name(candidate):
            raise ForbiddenFileError(f"{label} {os.path.basename(path.rstrip(os.sep)) or path!r} is not an allowed PRD document.")
    extension = os.path.splitext(_real(path))[1].lower()
    if extension not in PRD_ALLOWED_EXTENSIONS:
        raise ForbiddenFileError(f"{label} must be one of {', '.join(sorted(PRD_ALLOWED_EXTENSIONS))} (got {extension or 'no extension'!r}).")


def _is_overbroad_root(root: str) -> bool:
    """True for the filesystem root (or drive root) and the user's home directory."""
    real_root = _real(root)
    if os.path.dirname(real_root) == real_root:
        return True
    return real_root == _real("~")


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
    candidates = []
    for root in roots:
        if not root:
            continue
        if _is_overbroad_root(root):
            # "/" or $HOME would neuter confinement (e.g. server started from the home directory).
            if root not in _WARNED_OVERBROAD_ROOTS:
                _WARNED_OVERBROAD_ROOTS.add(root)
                _LOGGER.warning("Ignoring allowed root %r: the filesystem root and the home directory are too broad; configure a specific project directory instead.", root)
            continue
        candidates.append(root)
    if not path:
        raise PathOutsideRootsError(f"{label} is empty")
    for root in candidates:
        if is_within(path, root):
            return _real(path)
    raise PathOutsideRootsError(
        f"{label} {os.path.basename(path.rstrip(os.sep)) or path!r} is outside the allowed roots; place it under the project/test-data directory or add its directory to the allowed roots setting."
    )
