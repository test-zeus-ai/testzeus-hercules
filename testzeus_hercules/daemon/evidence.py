"""Serving run evidence safely: confined paths, safe names, no symlinks, fixed content types.

A request path such as ``steps/2/plain.jpg`` is accepted only when every segment
is a plain file name (letters, digits, ``._-``, not starting with a dot), no
segment or the run directory itself is a symbolic link, and the target is a
regular file inside ``<project>/.hercules/runs/<run_id>/``.
"""

from __future__ import annotations

import io
import os
import re
import stat
from pathlib import Path

from testzeus_hercules.kernel.paths import validate_run_id

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_DEPTH = 6
_MAX_PATH = 512
CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".xml": "application/xml",
    ".zip": "application/zip",
    ".webm": "video/webm",
}
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
TEXT_TYPES = ("text/", "application/json", "application/x-ndjson", "application/xml")
FALLBACK_TYPE = "application/octet-stream"
MIN_SIDE, MAX_SIDE = 64, 4096


def content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), FALLBACK_TYPE)


def is_text_type(media_type: str) -> bool:
    return media_type.startswith(TEXT_TYPES)


def resolve_evidence(runs_dir: Path, run_id: str, relative: str) -> Path | None:
    """The evidence file for ``relative`` inside the run's directory, or None if it is unsafe or missing."""
    try:
        validate_run_id(run_id)
    except ValueError:
        return None
    if not relative or len(relative) > _MAX_PATH:
        return None
    parts = relative.split("/")
    if len(parts) > _MAX_DEPTH or any(not _SEGMENT.match(part) or ".." in part for part in parts):
        return None
    run_dir = runs_dir / run_id
    current = run_dir
    try:
        if not stat.S_ISDIR(os.lstat(current).st_mode):
            return None  # missing, a file, or a symbolic link
        for part in parts:
            current = current / part
            mode = os.lstat(current).st_mode
            if stat.S_ISLNK(mode):
                return None
    except OSError:
        return None
    if not stat.S_ISREG(mode):
        return None
    if not current.resolve().is_relative_to(run_dir.resolve()):
        return None
    return current


def downscale_jpeg(path: Path, max_side: int, quality: int = 70) -> bytes:
    """The image at ``path`` as a JPEG no larger than ``max_side`` on its longer side."""
    from PIL import Image

    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()
