"""Confine files named by steps (upload parts, schema and spec files) to the project.

A relative name is tried against each root in order; symlinks are resolved before
the containment check, so ``..`` and links that leave a root are refused, and so are
secret or state files (dotfiles, ``*.env``, keys, databases) by requested and by
resolved name. The filesystem root and the home directory are never roots.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterable

SECRET_FILE_PATTERNS = ("*.env", ".env*", "*.pem", "*.key", "*.db", "*.sqlite*", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "credentials*")


class PathConfinementError(ValueError):
    """The path is empty, outside every root, or names a secret file; the message is safe to show."""


def is_secret_file(name: str | Path) -> bool:
    """True for dotfiles and names that usually hold secrets or state (basename only)."""
    base = os.path.basename(str(name).rstrip("/\\")).lower()
    if base in ("", ".", ".."):
        return False
    return base.startswith(".") or any(fnmatch.fnmatchcase(base, pattern) for pattern in SECRET_FILE_PATTERNS)


def _usable_root(root: Path) -> Path | None:
    real = Path(os.path.realpath(root))
    if real.parent == real or real == Path(os.path.realpath(Path.home())):
        return None
    return real


def confine(name: str, roots: Iterable[Path]) -> Path:
    """The real path of the existing file ``name`` inside one of ``roots``; :class:`PathConfinementError` otherwise."""
    text = str(name or "").strip()
    if not text or "\x00" in text:
        raise PathConfinementError("the file name is empty or invalid")
    if is_secret_file(text):
        raise PathConfinementError(f"'{os.path.basename(text)}' looks like a secret or state file and cannot be used")
    allowed = [root for root in (_usable_root(Path(item)) for item in roots) if root is not None]
    candidates = [Path(text)] if os.path.isabs(text) else [root / text for root in allowed]
    for candidate in candidates:
        real = Path(os.path.realpath(candidate))
        if not any(real == root or real.is_relative_to(root) for root in allowed):
            if os.path.lexists(candidate):
                raise PathConfinementError(f"'{text}' resolves outside the project; keep the file inside the project folder")
            continue
        if real.is_file():
            if is_secret_file(real):
                raise PathConfinementError(f"'{os.path.basename(text)}' resolves to a secret or state file and cannot be used")
            return real
    if os.path.isabs(text) and not any(Path(os.path.realpath(text)).is_relative_to(root) for root in allowed):
        raise PathConfinementError(f"'{text}' is outside the project; keep the file inside the project folder")
    raise PathConfinementError(f"file not found: '{text}' (looked in the project folder)")
