"""Resolving and describing the built UI files safely.

A request path such as ``assets/index-3f9a1c2b.js`` is served only when every
segment is a plain file name (letters, digits, ``._-``, not starting with a dot),
no segment is a symbolic link, the target is a regular file inside the static
directory and its suffix has a known content type. Everything under ``assets/``
carries a content hash in its name (Vite), so it may be cached forever; the
index page is always revalidated.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from testzeus_hercules.ui.brand import PAGE_CSP, branded_page

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_NAME = "index.html"
HASHED_DIR = "assets"

# Same-origin only: no remote origins, no inline scripts or styles, no frames.
UI_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
    "connect-src 'self'; media-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"}
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
REVALIDATE_CACHE = "no-cache"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".txt": "text/plain; charset=utf-8",
}

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_DEPTH = 4
_MAX_PATH = 300


@dataclass(frozen=True)
class StaticAsset:
    path: Path
    media_type: str
    immutable: bool

    def headers(self) -> dict[str, str]:
        headers = {**SECURITY_HEADERS, "Cache-Control": IMMUTABLE_CACHE if self.immutable else REVALIDATE_CACHE}
        if self.media_type.startswith("text/html"):
            headers["Content-Security-Policy"] = UI_CSP
        return headers


def resolve_asset(root: Path, relative: str) -> StaticAsset | None:
    """The built file for ``relative`` inside ``root``, or None when it is unsafe, unknown or missing."""
    if not relative or len(relative) > _MAX_PATH or "\\" in relative or "\x00" in relative:
        return None
    parts = relative.split("/")
    if len(parts) > _MAX_DEPTH or any(not _SEGMENT.match(part) or ".." in part for part in parts):
        return None
    media_type = CONTENT_TYPES.get(Path(parts[-1]).suffix.lower())
    if media_type is None:
        return None
    current = root
    try:
        if not stat.S_ISDIR(os.lstat(current).st_mode):
            return None
        mode = 0
        for part in parts:
            current = current / part
            mode = os.lstat(current).st_mode
            if stat.S_ISLNK(mode):
                return None
    except OSError:
        return None
    if not stat.S_ISREG(mode) or not current.resolve().is_relative_to(root.resolve()):
        return None
    return StaticAsset(path=current, media_type=media_type, immutable=len(parts) > 1 and parts[0] == HASHED_DIR)


def index_page(root: Path) -> StaticAsset | None:
    """The built ``index.html``, or None when the UI has not been built."""
    return resolve_asset(root, INDEX_NAME)


def looks_like_file(relative: str) -> bool:
    """Whether a path names a built file (``assets/...``, ``favicon.svg``) rather than a client route.

    Client routes may end in a file-like name (``cases/login.feature``), so only top-level
    names with a suffix and anything under ``assets/`` count as files.
    """
    parts = relative.split("/")
    return parts[0] == HASHED_DIR or (len(parts) == 1 and bool(re.search(r"\.[A-Za-z0-9]{1,8}$", parts[0])))


def missing_assets_page() -> str:
    return branded_page(
        title="UI not built",
        heading="The UI has not been built",
        body_html=(
            "<p>The daemon is running and its API works, but this install has no UI files. "
            "Release builds include them. From a source checkout, build them once:</p>\n"
            "<pre>cd ui\nnpm ci\nnpm run build</pre>\n"
            "<p>or run <code>scripts/build_ui.sh</code>, then reload this page. "
            "The API is described at <code>/api/openapi.json</code>.</p>"
        ),
    )


def sign_in_page(*, expired: bool = False) -> str:
    reason = "That sign-in link was already used or has expired." if expired else "This browser is not signed in to the local Hercules daemon."
    return branded_page(
        title="sign in",
        heading="Sign in to Hercules",
        body_html=(
            f"<p>{reason}</p>\n"
            "<p>Run <code>hercules ui</code> in a terminal: it prints (and opens) a new one-time sign-in link. "
            "The link printed by <code>hercules serve</code> at start-up works once too.</p>"
        ),
    )
