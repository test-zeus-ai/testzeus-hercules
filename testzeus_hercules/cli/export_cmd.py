"""``hercules export``: write a passed run as automation code (Playwright Test, pytest-playwright, or for a native app
run pytest with the Appium Python client) or its spec.

Exit codes: 0 when the run was exported, 2 when it cannot be (not found, not finished, did not pass
without ``--allow-failed``, recorded before exports existed). The code goes to standard output, or to
``--out``; what it needs (environment variables) and what to review go to standard error.

``--check`` also runs the pytest-playwright export once (see :mod:`testzeus_hercules.export.check`):
in ``<run>/export-check/``, with a Playwright trace and a recording made from it. Credentials the run
took out of the case's URLs are read from the case file (``--case``, else the project's case with the
run's id). The test runs with only the environment it needs (PATH, HOME, locale, proxies and CA
bundles, ``PLAYWRIGHT_*``, ``HERCULES_CHROMIUM`` and the variables the export declares), never the
caller's whole environment. Exit code 0 when it passed, 1 when it failed or timed out.

For a native app run, ``--check`` runs the Appium export (``appium-py``) once on the device the run used,
through its Appium server (which must answer first, for a local server), with the same restricted
environment plus the ``APPIUM_*`` variables. An app file the run installed is passed by its path in the
project (``APPIUM_APP``) unless that variable is set.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from testzeus_hercules.export.common import ExportResult, Format


def add_export_command(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser(
        "export",
        help="write a passed run as automation code (Playwright Test, pytest-playwright, Appium) or its spec",
        description=(
            "Write a passed run as a Playwright Test (TypeScript) or pytest-playwright (Python) file, a native app run as a pytest file " "with the Appium Python client, or print its automation spec."
        ),
    )
    parser.add_argument("run", metavar="RUN", help="a run id (from .hercules/runs) or a run folder")
    parser.add_argument(
        "--format",
        dest="export_format",
        choices=("spec", "playwright-ts", "playwright-py", "appium-py"),
        default=None,
        help="what to write (default playwright-ts, playwright-py with --check; appium-py for a native app run)",
    )
    parser.add_argument("--out", type=Path, default=None, metavar="FILE", help="write to this file instead of standard output")
    parser.add_argument("--allow-failed", action="store_true", help="export a run that did not pass, up to its last passed step")
    parser.add_argument("--project", type=Path, default=None, metavar="DIR", help="project directory (default: the current directory)")
    parser.add_argument("--force", action="store_true", help="replace --out when it exists")
    parser.add_argument(
        "--check",
        action="store_true",
        help="also run the pytest export once and report whether it passes (needs pytest-playwright; for a native app run, the Appium Python client, the Appium server and the device)",
    )
    parser.add_argument("--check-timeout", type=float, default=None, metavar="SECONDS", help="stop the check after this long (default 600)")
    parser.add_argument("--case", type=Path, default=None, metavar="FILE", help="the run's case file, for credentials in its URLs (default: found by the run's case id)")


def _run_dir(reference: str, project: Path, env: Mapping[str, str] | None) -> Path:
    from testzeus_hercules.export import ExportError
    from testzeus_hercules.kernel.paths import HerculesPaths
    from testzeus_hercules.kernel.settings import Settings

    candidate = Path(reference).expanduser()
    if (candidate / "run.json").is_file():
        return candidate
    try:
        paths = HerculesPaths.from_settings(Settings.load(project, env=env))
        return paths.run_dir(reference)
    except ValueError:
        raise ExportError(f"{reference!r} is neither a run folder nor a run id") from None


def _url_credentials(case_id: str, case_path: Path | None, project: Path, env: Mapping[str, str] | None) -> dict[str, str]:
    """The credentials the run took out of its case's URLs, by the variable the export reads (``URL_CREDENTIAL_1`` ...)."""
    from testzeus_hercules.daemon.cases import CaseError, load_case_file
    from testzeus_hercules.engine.data import protect_case
    from testzeus_hercules.export import ExportError
    from testzeus_hercules.kernel.paths import HerculesPaths
    from testzeus_hercules.kernel.settings import Settings

    candidates = [case_path] if case_path is not None else sorted(HerculesPaths.from_settings(Settings.load(project, env=env)).cases_dir.glob("*"))
    for path in candidates:
        if path is None or not path.is_file():
            continue
        try:
            case = load_case_file(path)
        except (CaseError, ValueError):
            continue
        if case.id == case_id or case_path is not None:
            return {name.upper(): value for name, value in protect_case(case)[1].items()}
    raise ExportError(f"the case {case_id!r} was not found among the project's cases; pass its file with --case")


def run_export(args: argparse.Namespace, *, env: Mapping[str, str] | None, out: TextIO, err: TextIO) -> int:
    from testzeus_hercules.export import ExportError, default_format, export_run, read_run

    project = (args.project or Path.cwd()).expanduser().resolve()
    check = bool(getattr(args, "check", False))
    try:
        run_dir = _run_dir(args.run, project, env)
        run = read_run(run_dir)
        fallback: Format = default_format(run)
        fmt: Format = args.export_format or ("playwright-py" if check and fallback == "playwright-ts" else fallback)
        if check and fmt not in ("playwright-py", "appium-py"):
            err.write("hercules export: --check runs a pytest export; use --format playwright-py, or appium-py for a native app run (or leave --format out)\n")
            return 2
        result = export_run(run, fmt, allow_failed=args.allow_failed)
    except ExportError as exc:
        err.write(f"hercules export: {exc}\n")
        return 2
    if args.out is not None:
        if args.out.exists() and not args.force:
            err.write(f"hercules export: {args.out} exists; use --force to replace it\n")
            return 2
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(result.code, encoding="utf-8")
        err.write(f"Wrote {args.out} ({result.steps_exported} of {result.steps_total} steps from run {result.run_id}).\n")
    else:
        out.write(result.code)
        err.write(f"Suggested file: {result.filename}\n")
    for item in result.env:
        err.write(f"Environment: {item.name} ({'required' if item.required else 'optional'}): {item.purpose}\n")
    for note in result.notes:
        err.write(f"Review: {note}\n")
    if check:
        return _check(args, result, run_dir, project, env, err)
    return 0


def _check(args: argparse.Namespace, result: "ExportResult", run_dir: Path, project: Path, env: Mapping[str, str] | None, err: TextIO) -> int:
    import os

    from testzeus_hercules.export import ExportError
    from testzeus_hercules.export.check import DEFAULT_TIMEOUT_S, CheckError, browser_note, run_check
    from testzeus_hercules.export.common import BROWSER_ENV

    environment = dict(os.environ if env is None else env)
    if result.format == "appium-py":
        return _check_appium(args, result, run_dir, project, environment, err)
    try:
        extra = _url_credentials(result.case_id, args.case, project, env) if any(item.name.startswith("URL_CREDENTIAL_") for item in result.env) else {}
        state = ((result.spec.get("browser_options") or {}) if result.spec.get("browser") else {}).get("storage_state")
        if state and not environment.get("STORAGE_STATE"):
            extra["STORAGE_STATE"] = str((project / str(state)).resolve())  # the check runs in its own folder
        err.write(
            f"Checking the export: pytest-playwright with {browser_note(environment, result.spec.get('browser_name') if result.spec.get('browser') else None)}, once, with a trace, and only the environment it needs (PATH, HOME, locale, proxies, PLAYWRIGHT_*, {BROWSER_ENV} and the variables it declares) ...\n"
        )
        checked = run_check(result, run_dir / "export-check", env=environment, extra=extra, timeout_s=args.check_timeout or DEFAULT_TIMEOUT_S)
    except (CheckError, ExportError) as exc:
        err.write(f"hercules export: {exc}\n")
        return 2
    if checked.timed_out:
        verdict = f"stopped after {checked.duration_s:.0f} s (the time limit); it did not finish"
    else:
        verdict = f"{'passed' if checked.passed else 'failed'} in {checked.duration_s:.1f} s"
    err.write(f"Export check: {verdict}.\n")
    if not checked.passed and checked.output:
        err.write(checked.output + "\n")
    err.write(f"Test file: {checked.test_file}\n")
    if checked.recording is not None:
        err.write(f"Recording: {checked.recording}\n")
    if checked.trace is not None:
        err.write(f"Trace: {checked.trace} (holds what the browser sent, secrets included: keep it private; open it with `playwright show-trace`)\n")
    return 0 if checked.passed else 1


def _check_appium(args: argparse.Namespace, result: "ExportResult", run_dir: Path, project: Path, environment: dict[str, str], err: TextIO) -> int:
    from testzeus_hercules.act.redact import mask_url_credentials
    from testzeus_hercules.export.appium import APP_ENV, appium_server
    from testzeus_hercules.export.check import DEFAULT_TIMEOUT_S, CheckError, run_appium_check

    url, local = appium_server(result.spec)
    app = str((result.spec.get("app") or {}).get("app") or "")
    extra: dict[str, str] = {}
    if app and local and not environment.get(APP_ENV) and not app.startswith(("bs://", "storage:")):
        extra[APP_ENV] = str((project / app).resolve())  # the test runs in its own folder, not the project's
    err.write(
        f"Checking the export: pytest with the Appium Python client, once, on the device the run used ({'APPIUM_URL' if environment.get('APPIUM_URL') else mask_url_credentials(url)}), with only the environment it needs ...\n"
    )
    try:
        checked = run_appium_check(result, run_dir / "export-check", env=environment, appium_url=url, local=local, extra=extra, timeout_s=args.check_timeout or DEFAULT_TIMEOUT_S)
    except CheckError as exc:
        err.write(f"hercules export: {exc}\n")
        return 2
    if checked.timed_out:
        verdict = f"stopped after {checked.duration_s:.0f} s (the time limit); it did not finish"
    else:
        verdict = f"{'passed' if checked.passed else 'failed'} in {checked.duration_s:.1f} s"
    err.write(f"Export check: {verdict}.\n")
    if not checked.passed and checked.output:
        err.write(checked.output + "\n")
    if checked.hint:
        err.write(f"Hint: {checked.hint}\n")
    err.write(f"Test file: {checked.test_file}\n")
    return 0 if checked.passed else 1
