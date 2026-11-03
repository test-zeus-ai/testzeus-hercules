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

import fnmatch
import logging
import os
from collections.abc import Iterable
from typing import List, Optional

from testzeus_hercules.config import get_global_conf

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
    if "\x00" in str(path):
        raise ForbiddenFileError(f"{label} contains an invalid character.")
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
    try:
        real_root = _real(root)
        real_path = _real(path)
        return os.path.commonpath([real_path, real_root]) == real_root
    except (ValueError, OSError):  # NUL byte / name too long / different drives on Windows
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
    if "\x00" in str(path):
        raise PathOutsideRootsError(f"{label} contains an invalid character")
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

    Like ``resolve_within``, the filesystem root and the home directory are
    never accepted as roots, and secret/state files (dotfiles, ``*.env``,
    ``agents_llm_config*``, keys, databases, see ``is_sensitive_name``) are
    refused by requested name and by resolved name, so a symlink cannot expose
    one either.

    Raises:
        PathConfinementError: the path is empty, contains a NUL byte,
            resolves outside every allowed root or names a sensitive file.
    """
    text = str(path or "")
    if not text.strip() or "\x00" in text:
        raise PathConfinementError("Path is empty or invalid.")
    raw_roots = [str(root) for root in roots if root] if roots is not None else allowed_roots()
    allowed = []
    for root in raw_roots:
        if _is_overbroad_root(root):
            if root not in _WARNED_OVERBROAD_ROOTS:
                _WARNED_OVERBROAD_ROOTS.add(root)
                _LOGGER.warning("Ignoring allowed root %r: the filesystem root and the home directory are too broad; configure a specific project directory instead.", root)
            continue
        allowed.append(os.path.realpath(os.path.abspath(root)))
    if _names_sensitive_file(text):
        raise PathConfinementError(f"Access denied: '{os.path.basename(text.rstrip('/' + os.sep))}' looks like a secret or state file and cannot be used.")
    resolved_path = _resolve_candidates(text, allowed)
    if _names_sensitive_file(resolved_path):
        raise PathConfinementError(f"Access denied: '{os.path.basename(text.rstrip('/' + os.sep))}' resolves to a secret or state file and cannot be used.")
    return resolved_path


def _names_sensitive_file(path: str) -> bool:
    """``is_sensitive_name`` on the basename, ignoring the '.'/'..' directory references."""
    base = os.path.basename(str(path).rstrip("/\\"))
    if base in ("", ".", ".."):
        return False
    return is_sensitive_name(base)


def _resolve_candidates(text: str, allowed: List[str]) -> str:
    """Return the first confined, existing candidate for ``text`` (see ``resolve_in_roots``)."""
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
