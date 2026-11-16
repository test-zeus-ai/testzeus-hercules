"""What a scan may target: the app under test, and nothing else.

A URL scan targets the case's base URL, or a URL under it (``path``): an absolute URL on
another origin (scheme, host and port) is refused. An OpenAPI scan reads a spec file
that must be inside the project (secret and state files are refused, ``..`` and links
leaving the project too). With a base URL, every operation is sent to the base URL (the
scanner gets a copy of the spec whose only server is the base URL); without one, the
spec's own absolute server URLs are the scope.

An OpenAPI scan has two forms. By default the scanner runs the chosen templates against
the API's URLs (the server URL and each path, path parameters filled with their example
or ``1``), written to a list file. With intrusive templates allowed it reads the spec
itself and fuzzes each operation's parameters with its DAST templates.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal
from urllib.parse import quote, urlsplit, urlunsplit

from testzeus_hercules.act.api_schema import SpecError, load_document
from testzeus_hercules.act.files import PathConfinementError, confine
from testzeus_hercules.act.redact import redact_url

__all__ = ["PathConfinementError", "ScanTarget", "ScopeError", "openapi_target", "origin", "url_target"]

SCOPED_SPEC = "openapi.scoped.json"
URL_LIST = "openapi.urls.txt"
_MAX_URLS = 300
_PARAM = re.compile(r"\{([^{}/]+)\}")


class ScopeError(ValueError):
    """The scan would leave the app under test (or has no target); the message is safe to show."""


@dataclass(frozen=True)
class ScanTarget:
    """What the scanner gets: a URL, or a spec file with its input mode, and the origins in scope."""

    input: Literal["url", "openapi"]
    shown: str  # the URL (credentials masked) or the spec's name as given
    url: str | None = None
    spec: Path | None = None
    spec_mode: Literal["openapi", "swagger"] = "openapi"
    url_list: Path | None = None  # openapi: the API's URLs, one per line
    origins: tuple[str, ...] = ()


def origin(url: str) -> str:
    """``scheme://host:port`` of an http(s) URL (default ports made explicit); :class:`ScopeError` for anything else."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise ScopeError(f"{redact_url(url)} is not a valid URL") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ScopeError(f"{redact_url(url)} is not an http(s) URL with a host")
    host = parts.hostname.lower().rstrip(".")
    host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{host}:{port or (443 if parts.scheme == 'https' else 80)}"


def _base(base_url: str | None) -> str:
    if not base_url or not base_url.strip():
        raise ScopeError("a security scan needs the app's base URL: set environment.base_url in the case, or pass --base-url or --env")
    origin(base_url)  # validates
    return base_url.strip()


def url_target(base_url: str | None, path: str | None = None) -> ScanTarget:
    """The base URL, or ``path`` under it (relative, or an absolute URL on the same origin)."""
    base = _base(base_url)
    scope = origin(base)
    if path is None or not path.strip():
        url = base
    else:
        text = path.strip()
        scheme = urlsplit(text).scheme
        if scheme in ("http", "https"):
            if origin(text) != scope:
                raise ScopeError(f"refused: {redact_url(text)} is not the app under test ({redact_url(base)}); a scan only targets the case's base URL")
            url = text
        elif scheme or text.startswith("//"):
            raise ScopeError(f"refused: {redact_url(text)} is not a path under the base URL")
        else:
            url = base.rstrip("/") + "/" + text.lstrip("/")
    return ScanTarget(input="url", shown=redact_url(url), url=url, origins=(scope,))


def openapi_target(spec_name: str, roots: Iterable[Path], base_url: str | None, work_dir: Path) -> ScanTarget:
    """A spec inside ``roots`` (:class:`PathConfinementError` otherwise), scoped to ``base_url`` when given."""
    path = confine(spec_name, roots)  # before anything else: a path outside the project is never read
    try:
        document = load_document(path)
    except SpecError as exc:
        raise ScopeError(str(exc)) from None
    mode: Literal["openapi", "swagger"] = "swagger" if "swagger" in document else "openapi"
    if mode == "openapi" and "openapi" not in document:
        raise ScopeError(f"{spec_name} is not an OpenAPI (or Swagger) document")
    work_dir.mkdir(parents=True, exist_ok=True)
    if base_url:
        base = _base(base_url)
        scoped = _scoped(document, mode, base)
        spec = work_dir / SCOPED_SPEC
        spec.write_text(json.dumps(scoped, indent=2, default=str), encoding="utf-8")
        servers, origins = _servers(scoped, mode), tuple[str, ...]((origin(base),))
    else:
        servers = _servers(document, mode)
        if not servers:
            raise ScopeError(f"{spec_name} names no absolute server URL: give the app's base URL (--base-url or --env) so the scan knows where the API runs")
        spec, origins = path, tuple(dict.fromkeys(origin(server) for server in servers))
    url_list = work_dir / URL_LIST
    url_list.write_text("".join(url + "\n" for url in api_urls(document, servers)), encoding="utf-8")
    return ScanTarget(input="openapi", shown=spec_name, spec=spec, spec_mode=mode, url_list=url_list, origins=origins)


def api_urls(document: dict[str, Any], servers: list[str]) -> list[str]:
    """The servers and every path of the spec under each server, path parameters filled (at most a few hundred)."""
    raw_paths = document.get("paths")
    paths: dict[str, Any] = raw_paths if isinstance(raw_paths, dict) else {}
    urls: list[str] = []
    for server in servers:
        root = server.rstrip("/")
        urls.append(root + "/")
        for template, item in paths.items():
            if isinstance(template, str) and template.startswith("/"):
                urls.append(root + _filled(template, item if isinstance(item, dict) else {}))
    return list(dict.fromkeys(urls))[:_MAX_URLS]


def _filled(template: str, item: dict[str, Any]) -> str:
    """A path template with each ``{name}`` replaced by the parameter's example (or default), else ``1``."""
    examples: dict[str, str] = {}
    operations = [item] + [operation for operation in item.values() if isinstance(operation, dict)]
    for holder in operations:
        for parameter in holder.get("parameters") or []:
            if isinstance(parameter, dict) and parameter.get("in") == "path" and isinstance(parameter.get("name"), str):
                raw_schema = parameter.get("schema")
                schema: dict[str, Any] = raw_schema if isinstance(raw_schema, dict) else {}
                value = next((candidate for candidate in (parameter.get("example"), schema.get("example"), schema.get("default")) if candidate is not None), None)
                if value is not None:
                    examples.setdefault(parameter["name"], quote(str(value), safe=""))
    return _PARAM.sub(lambda found: examples.get(found.group(1), "1"), template)


def _servers(document: dict[str, Any], mode: str) -> list[str]:
    if mode == "swagger":
        host = document.get("host")
        if not isinstance(host, str) or not host:
            return []
        schemes = [scheme for scheme in document.get("schemes") or ["https"] if scheme in ("http", "https")]
        return [f"{scheme}://{host}" for scheme in schemes]
    urls = []
    for server in document.get("servers") or []:
        url = server.get("url") if isinstance(server, dict) else None
        if isinstance(url, str) and "{" not in url and urlsplit(url).scheme in ("http", "https"):
            urls.append(url)
    return urls


def _server_path(document: dict[str, Any], mode: str) -> str:
    """The path of the spec's first server (``/v1`` of ``http://localhost/v1`` or of ``/v1``), or ``basePath``."""
    if mode == "swagger":
        base_path = document.get("basePath")
        return base_path if isinstance(base_path, str) else ""
    for server in document.get("servers") or []:
        url = server.get("url") if isinstance(server, dict) else None
        if isinstance(url, str):
            return urlsplit(url).path if "{" not in url else ""
    return ""


def _scoped(document: dict[str, Any], mode: str, base: str) -> dict[str, Any]:
    """The spec with the base URL as its only server: the base URL as given when it has a path, else plus the spec's server path."""
    parts = urlsplit(base)
    path = parts.path.rstrip("/") or _server_path(document, mode).rstrip("/")
    if mode == "swagger":
        return {**document, "host": parts.netloc, "basePath": path or "/", "schemes": [parts.scheme]}
    return {**document, "servers": [{"url": urlunsplit((parts.scheme, parts.netloc, path, "", ""))}]}
