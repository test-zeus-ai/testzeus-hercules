"""Serving the tester UI: built files, the SPA fallback for client routes, and a page when the build is missing.

The guard middleware has already checked the session cookie (or token) before any of
this runs, so the UI is never served to a browser that has not signed in.
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from testzeus_hercules.ui.assets import SECURITY_HEADERS, STATIC_DIR, index_page, looks_like_file, missing_assets_page, resolve_asset
from testzeus_hercules.ui.brand import PAGE_CSP


def ui_root(configured: Path | None) -> Path:
    return configured if configured is not None else STATIC_DIR


def serve_ui(request: Request, root: Path) -> Response:
    """``GET /`` and ``GET /<path>``: a built file, else ``index.html`` for client routes."""
    relative = str(request.path_params.get("path", "")).lstrip("/")
    if relative == "api" or relative.startswith("api/"):
        return JSONResponse({"error": "not found"}, status_code=404)
    index = index_page(root)
    if index is None:
        return HTMLResponse(missing_assets_page(), status_code=503, headers={**SECURITY_HEADERS, "Cache-Control": "no-store", "Content-Security-Policy": PAGE_CSP})
    if relative:
        asset = resolve_asset(root, relative)
        if asset is not None and asset.path.name != index.path.name:
            return FileResponse(asset.path, media_type=asset.media_type, headers=asset.headers())
        if looks_like_file(relative):
            return Response("not found\n", status_code=404, media_type="text/plain", headers={**SECURITY_HEADERS, "Cache-Control": "no-store"})
    return FileResponse(index.path, media_type=index.media_type, headers=index.headers())
