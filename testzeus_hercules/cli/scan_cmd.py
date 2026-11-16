"""``hercules scan``: scan your app for security issues with the nuclei scanner.

The target is the app under test: a saved environment's base URL (``--env``) or
``--base-url``, optionally narrowed to a path under it (``--path``), or the operations of
an OpenAPI spec inside the project (``--openapi``), sent to that base URL. Headers take
``{{NAME}}`` placeholders, filled from environment variables of that name; their values
are never printed or written. Evidence goes to ``.hercules/scans/<id>/``.

``--install`` downloads the pinned scanner release into the Hercules cache (checksum
checked); ``--update-templates`` runs ``nuclei -update-templates``.

Exit codes: 0 when no finding reaches ``--fail-on``, 1 when one does, 2 when the scan
could not run (no scanner, refused target, the scanner failed or timed out).
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from testzeus_hercules.cases.security import SecurityScanResult
    from testzeus_hercules.kernel.paths import HerculesPaths

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2
_SEVERITIES = ("critical", "high", "medium", "low", "info")
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Z_][A-Z0-9_]*)\s*\}\}")
_HEADER = re.compile(r"^([A-Za-z0-9!#$%&'*+.^_`|~-]+)\s*:\s*(.*)$")
_TEMPLATE_UPDATE_TIMEOUT_S = 900


def add_scan_command(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser(
        "scan",
        help="scan your app for security issues with the nuclei scanner (installed separately)",
        description="Scan the app under test (its base URL, or an OpenAPI spec's operations) with nuclei and report the findings.",
    )
    parser.add_argument("--project", type=Path, default=None, metavar="DIR", help="project directory (default: the current directory)")
    parser.add_argument("--env", default=None, metavar="NAME", help="saved environment whose base URL is scanned")
    parser.add_argument("--base-url", default=None, metavar="URL", help="base URL of the app under test")
    parser.add_argument("--path", default=None, metavar="PATH", help="scan this path under the base URL instead (e.g. /api)")
    parser.add_argument("--openapi", default=None, metavar="SPEC", help="an OpenAPI spec file in the project: scan its operations")
    parser.add_argument("--tags", default=None, metavar="TAGS", help="comma-separated template tags (default: cve,exposure,misconfig)")
    parser.add_argument("--exclude-tags", default=None, metavar="TAGS", help="comma-separated template tags to leave out")
    parser.add_argument("--fail-on", choices=_SEVERITIES, default="medium", help="exit 1 when a finding is at or above this severity (default medium)")
    parser.add_argument("--allow-intrusive", action="store_true", help="also run intrusive, denial-of-service and fuzzing templates (only on an app you may break)")
    parser.add_argument("--rate-limit", type=int, default=50, metavar="N", help="most requests per second (default 50)")
    parser.add_argument("--timeout-s", type=float, default=600.0, metavar="S", help="time limit of the scan in seconds (default 600)")
    parser.add_argument(
        "--header", dest="headers", action="append", default=[], metavar="'NAME: VALUE'", help="header for every request (repeatable); {{NAME}} in the value reads environment variable NAME"
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--install", action="store_true", help="download the pinned nuclei release into the Hercules cache (checksum checked), then exit")
    parser.add_argument("--update-templates", action="store_true", help="download or update nuclei's templates (nuclei -update-templates), then exit")


class _UsageError(ValueError):
    pass


def _tags(text: str | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(tag.strip().lower() for tag in (text or "").split(",") if tag.strip()))


def _headers(raw: list[str], env: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    headers: list[tuple[str, str]] = []
    for item in raw:
        match = _HEADER.match(item.strip())
        if match is None:
            raise _UsageError(f"--header must look like 'Name: value', got {item.split(':', 1)[0][:40]!r}")
        name, value = match.group(1), match.group(2)

        def fill(found: re.Match[str]) -> str:
            variable = found.group(1)
            if not env.get(variable):
                raise _UsageError(f"--header {name}: environment variable {variable} is not set")
            return env[variable]

        headers.append((name, _PLACEHOLDER.sub(fill, value)))
    return tuple(headers)


def _base_url(args: argparse.Namespace, paths: HerculesPaths) -> str | None:
    from testzeus_hercules.daemon.cases import load_environment
    from testzeus_hercules.scan.scope import origin

    base = args.base_url
    if args.env is not None:
        saved = load_environment(paths.envs_dir, args.env)
        if saved is None:
            raise _UsageError(f"unknown environment {args.env!r}: save it in {paths.envs_dir} (or from the UI), or pass --base-url")
        if base is not None and origin(base) != origin(saved.base_url):
            raise _UsageError(f"refused: --base-url is not the app of environment {args.env!r} ({saved.base_url}); a scan only targets the environment's base URL")
        base = base or saved.base_url
    return base


def run_scan_command(args: argparse.Namespace, *, env: Mapping[str, str] | None, out: TextIO, err: TextIO) -> int:
    from testzeus_hercules.kernel.paths import HerculesPaths
    from testzeus_hercules.kernel.settings import Settings, SettingsError

    environ: Mapping[str, str] = os.environ if env is None else env
    try:
        settings = Settings.load(args.project, env=environ)
    except SettingsError as exc:
        err.write(f"hercules scan: {exc}\n")
        return EXIT_ERROR
    paths = HerculesPaths.from_settings(settings)
    if args.install:
        return _install(paths, out, err)
    if args.update_templates:
        return _update_templates(environ, paths, out, err)
    try:
        return _scan(args, environ, paths, out, err)
    except _UsageError as exc:
        err.write(f"hercules scan: {exc}\n")
        return EXIT_ERROR


def _install(paths: HerculesPaths, out: TextIO, err: TextIO) -> int:
    from testzeus_hercules.scan.install import NUCLEI_VERSION, InstallError, install_nuclei, release_asset

    try:
        asset = release_asset()
        out.write(f"downloading {asset.name} (nuclei {NUCLEI_VERSION}) ...\n")
        out.flush()
        binary = install_nuclei(paths.cache_dir, asset=asset)
    except InstallError as exc:
        err.write(f"hercules scan --install: {exc}\n")
        return EXIT_ERROR
    out.write(f"installed nuclei {NUCLEI_VERSION} at {binary} (sha256 checked)\nnext: hercules scan --update-templates\n")
    return EXIT_OK


def _update_templates(env: Mapping[str, str], paths: HerculesPaths, out: TextIO, err: TextIO) -> int:
    import subprocess

    from testzeus_hercules.scan.install import ScannerMissing, find_nuclei

    try:
        binary = find_nuclei(env, paths.cache_dir)
    except ScannerMissing as exc:
        err.write(f"hercules scan: {exc}\n")
        return EXIT_ERROR
    out.write(f"updating templates with {binary} -update-templates ...\n")
    out.flush()
    try:
        # Not -duc here: with the update check off, nuclei installs no templates and still exits 0.
        done = subprocess.run([str(binary), "-update-templates", "-nc"], capture_output=True, text=True, timeout=_TEMPLATE_UPDATE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        err.write("hercules scan: the template update did not finish in time\n")
        return EXIT_ERROR
    tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-6:])
    out.write(tail + "\n")
    return EXIT_OK if done.returncode == 0 else EXIT_ERROR


def _scan(args: argparse.Namespace, env: Mapping[str, str], paths: HerculesPaths, out: TextIO, err: TextIO) -> int:
    import asyncio

    from testzeus_hercules.act.files import PathConfinementError
    from testzeus_hercules.scan.install import ScannerMissing
    from testzeus_hercules.scan.runner import ScanRequest, render_summary, scan
    from testzeus_hercules.scan.scope import ScopeError

    if args.rate_limit < 1 or args.timeout_s <= 0:
        raise _UsageError("--rate-limit and --timeout-s must be positive")
    base_url = _base_url(args, paths)
    if base_url is None and args.openapi is None:
        raise _UsageError("give the app to scan: --env NAME or --base-url URL (and --openapi SPEC to scan an API's operations)")
    request = ScanRequest(
        base_url=base_url,
        path=args.path,
        openapi=args.openapi,
        tags=_tags(args.tags),
        exclude_tags=_tags(args.exclude_tags),
        intrusive=args.allow_intrusive,
        fail_on=args.fail_on,
        rate_limit=args.rate_limit,
        timeout_s=args.timeout_s,
        headers=_headers(args.headers, env),
    )
    paths.ensure_dirs()
    out_dir = paths.project_hercules_dir / "scans" / datetime.now(timezone.utc).strftime("s_%Y%m%d_%H%M%S_%f")
    hercules = paths.project_hercules_dir
    if not args.json:
        err.write(f"scanning (tags: {', '.join(request.effective_tags())}; this can take minutes) ...\n")
        err.flush()
    try:
        result = asyncio.run(scan(request, roots=(hercules / "specs", hercules, paths.project_dir), out_dir=out_dir, env=env, cache_dir=paths.cache_dir))
    except (ScopeError, PathConfinementError) as exc:
        err.write(f"hercules scan: refused: {exc}\n")
        return EXIT_ERROR
    except ScannerMissing as exc:
        err.write(f"hercules scan: {exc}\n")
        return EXIT_ERROR
    result = result.model_copy(update={"evidence": [str(out_dir / name) for name in ("summary.txt", "findings.jsonl", "scan.json")]})
    if args.json:
        out.write(result.model_dump_json(indent=2) + "\n")
    else:
        out.write(render_summary(result))
        out.write(f"evidence: {out_dir}\n")
    return _exit_code(result)


def _exit_code(result: SecurityScanResult) -> int:
    if result.error is not None:
        return EXIT_ERROR
    return EXIT_FINDINGS if result.failing() else EXIT_OK
