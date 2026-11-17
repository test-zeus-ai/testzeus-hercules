"""``hercules cloud``: connect BrowserStack, Sauce Labs and TestMu AI, and run passed runs' exported tests on them.

* ``hercules cloud list``: each cloud, whether it is connected and what it can run here;
* ``hercules cloud connect CLOUD --username NAME [--region REGION]`` reads the access key from standard input (never
  an argument), saves both (and the Sauce Labs region) to ``~/.hercules/clouds.env`` (mode 600) and checks them;
* ``hercules cloud check CLOUD`` and ``hercules cloud disconnect CLOUD``;
* ``hercules cloud run RUN --cloud CLOUD`` runs the run's exported test on the cloud and waits for it (exit code 0
  when it passed, 1 when it failed, 2 when it could not start);
* ``hercules cloud bundle RUN --cloud CLOUD --out FILE.zip`` writes the same project, with the cloud's CI config.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from testzeus_hercules.clouds.registry import CloudId
    from testzeus_hercules.clouds.runner import CloudRunRecord
    from testzeus_hercules.kernel.settings import Settings

CLOUD_CHOICES = ("browserstack", "saucelabs", "testmu")
SAUCELABS_REGION_CHOICES = ("us-west-1", "us-east-4", "eu-central-1")
_CLOUD_HELP = "browserstack, saucelabs or testmu (TestMu AI, formerly LambdaTest)"


def add_cloud_command(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser(
        "cloud",
        help="connect BrowserStack, Sauce Labs or TestMu AI and run exported tests on them",
        description="Connect device and browser clouds, and run a passed run's exported test on one.",
    )
    actions = parser.add_subparsers(dest="cloud_action", metavar="<action>", required=True)
    listing = actions.add_parser("list", help="each cloud, whether it is connected and what it can run here", description="Show each cloud, whether it is connected and what it can run here.")
    listing.add_argument("--json", action="store_true", help="print JSON")
    connect = actions.add_parser(
        "connect",
        help="save a cloud's user name and access key (the key is read from standard input) and check them",
        description='Save a cloud\'s user name and access key and check them. The key is read from standard input, never an argument: echo "$KEY" | hercules cloud connect testmu --username NAME',
    )
    connect.add_argument("cloud", choices=CLOUD_CHOICES, help=_CLOUD_HELP)
    connect.add_argument("--username", required=True, help="the cloud user name (from its account page, not an example)")
    connect.add_argument("--region", choices=SAUCELABS_REGION_CHOICES, default=None, help="Sauce Labs only: the data center of the account (default us-west-1)")
    for name, text in (("check", "check that the cloud accepts the current credentials"), ("disconnect", "remove the saved credentials of a cloud")):
        actions.add_parser(name, help=text, description=text[0].upper() + text[1:] + ".").add_argument("cloud", choices=CLOUD_CHOICES, help=_CLOUD_HELP)
    for name, text in (("run", "run a passed run's exported test on a cloud and wait for it"), ("bundle", "write a passed run's test as a project for a cloud (ZIP)")):
        action = actions.add_parser(name, help=text, description=text[0].upper() + text[1:] + ".")
        action.add_argument("run", metavar="RUN", help="a run id (from .hercules/runs) or a run folder")
        action.add_argument("--cloud", required=True, choices=CLOUD_CHOICES, help=_CLOUD_HELP)
        action.add_argument("--browser", choices=("chrome", "edge", "chromium", "firefox", "webkit"), default=None, help="web runs: the browser (default: the run's own; chrome for a Chromium run)")
        action.add_argument("--os", dest="os_name", choices=("windows", "macos"), default="windows", help="web runs: the operating system (default windows)")
        action.add_argument("--device", type=_matching(r"[A-Za-z0-9 ._()+-]{1,80}", "a device name"), default=None, help="native app runs: the device name (default: the run's)")
        action.add_argument("--platform-version", type=_matching(r"[A-Za-z0-9._-]{1,20}", "an OS version"), default=None, help="native app runs: the OS version (default: the run's)")
        action.add_argument("--allow-failed", action="store_true", help="use a run that did not pass, up to its last passed step")
        if name == "bundle":
            action.add_argument("--out", type=Path, required=True, metavar="FILE", help="the ZIP file to write")
        if name == "run":
            action.add_argument("--json", action="store_true", help="print the final record as JSON")
    for action in actions.choices.values():
        action.add_argument("--project", type=Path, default=None, metavar="DIR", help="project directory (default: the current directory)")


def _matching(pattern: str, what: str) -> Any:
    import re

    def check(text: str) -> str:
        if not re.fullmatch(pattern, text):
            raise argparse.ArgumentTypeError(f"expected {what}, got {text!r}")
        return text

    return check


def run_cloud_command(args: argparse.Namespace, *, env: Mapping[str, str] | None = None, out: TextIO, err: TextIO, stdin: TextIO | None = None) -> int:
    import os

    from testzeus_hercules.kernel.settings import Settings, SettingsError

    environ = dict(os.environ if env is None else env)
    project = (args.project or Path.cwd()).resolve()
    try:
        settings = Settings.load(project, env=environ)
    except SettingsError as exc:
        err.write(f"hercules: error: {exc}\n")
        return 2
    action = args.cloud_action
    if action == "list":
        return _list(settings, environ, out, as_json=args.json)
    if action == "connect":
        return _connect(args.cloud, args.username, args.region, settings, environ, stdin or sys.stdin, out, err)
    if action == "check":
        return _check(args.cloud, settings, environ, out)
    if action == "disconnect":
        return _disconnect(args.cloud, settings, environ, out, err)
    return _run_or_bundle(args, settings, environ, project, out, err)


def _list(settings: "Settings", env: Mapping[str, str], out: TextIO, *, as_json: bool) -> int:
    from testzeus_hercules.clouds.credentials import find_credentials, placeholder_problem
    from testzeus_hercules.clouds.registry import CLOUD_IDS, CLOUDS
    from testzeus_hercules.clouds.runner import requirements
    from testzeus_hercules.cli.style import Style

    rows: list[dict[str, Any]] = []
    for cloud in CLOUD_IDS:
        found = find_credentials(cloud, settings, env)
        info = CLOUDS[cloud]
        rows.append(
            {
                "cloud": cloud,
                "name": info.name,
                "connected": found.found,
                "source": found.source,
                "username": found.username,
                "web": requirements("web", info.web_mode) or ("ready" if found.found else "not connected"),
                "native": requirements("mobile", "appium") or ("ready" if found.found else "not connected"),
                "problem": placeholder_problem(found) or found.problem,
            }
        )
    if as_json:
        out.write(json.dumps(rows, indent=2) + "\n")
        return 0
    style = Style.for_stream(out, env)
    for row in rows:
        where = {"env": "from the environment", "file": "from ~/.hercules/clouds.env", "none": "not connected"}[row["source"]]
        user = f" as {row['username']}" if row["username"] else ""
        state = style.ok(f"connected {where}{user}") if row["connected"] else style.dim(where)
        name = f"{row['name']:<13}"
        out.write(f"{style.bold(name)} {state}\n")
        out.write(f"  {style.dim('web:')}         {_readiness(row['web'], style)}\n  {style.dim('native apps:')} {_readiness(row['native'], style)}\n")
        if row["problem"]:
            out.write(f"  {style.warn('warning:')}     {row['problem']}\n")
    if not any(row["connected"] for row in rows):
        out.write(f"\n{style.dim('Connect one with:')} echo \"$ACCESS_KEY\" | hercules cloud connect <cloud> --username NAME\n")
    return 0


def _readiness(text: str, style: Any) -> str:
    if text == "ready":
        return style.ok(text)
    return style.dim(text) if text == "not connected" else style.warn(text)


def _connect(cloud: "CloudId", username: str, region: str | None, settings: "Settings", env: Mapping[str, str], stdin: TextIO, out: TextIO, err: TextIO) -> int:
    from testzeus_hercules.clouds.credentials import CredentialError, save_credentials

    key = stdin.readline().strip()
    if not key:
        err.write('hercules: error: pipe the access key on standard input, for example: echo "$KEY" | hercules cloud connect ...\n')
        return 2
    try:
        path = save_credentials(cloud, settings, username, key, region)
    except CredentialError as exc:
        err.write(f"hercules: error: {exc}\n")
        return 2
    out.write(f"saved to {path}\n")
    return _check(cloud, settings, env, out)


def _check(cloud: "CloudId", settings: "Settings", env: Mapping[str, str], out: TextIO) -> int:
    from testzeus_hercules.clouds.check import check_credentials
    from testzeus_hercules.clouds.credentials import find_credentials, placeholder_problem

    found = find_credentials(cloud, settings, env)
    result = asyncio.run(check_credentials(cloud, found, settings))
    from testzeus_hercules.cli.style import Style

    style = Style.for_stream(out, env)
    out.write(f"{style.ok('ok:') if result.ok else style.fail('failed:')} {result.message}\n")
    problem = placeholder_problem(found)
    if problem and not result.ok:
        out.write(f"{style.warn('hint:')} {problem}\n")
    return 0 if result.ok else 1


def _disconnect(cloud: "CloudId", settings: "Settings", env: Mapping[str, str], out: TextIO, err: TextIO) -> int:
    from testzeus_hercules.clouds.credentials import (
        CredentialError,
        find_credentials,
        remove_credentials,
    )

    try:
        removed = remove_credentials(cloud, settings)
    except CredentialError as exc:
        err.write(f"hercules: error: {exc}\n")
        return 2
    out.write("removed the saved credentials\n" if removed else "no saved credentials to remove\n")
    found = find_credentials(cloud, settings, env)
    if found.source == "env":
        out.write(f"note: {found.username_env} and {found.access_key_env} are still set in the environment\n")
    return 0


def _run_or_bundle(args: argparse.Namespace, settings: "Settings", env: Mapping[str, str], project: Path, out: TextIO, err: TextIO) -> int:
    from testzeus_hercules.cli.export_cmd import _run_dir
    from testzeus_hercules.clouds.bundle import MobileOptions, WebOptions
    from testzeus_hercules.clouds.runner import CloudRunError, CloudRuns
    from testzeus_hercules.export import ExportError, read_run
    from testzeus_hercules.kernel.paths import HerculesPaths

    try:
        result = read_run(_run_dir(args.run, project, env))
    except ExportError as exc:
        err.write(f"hercules: error: {exc}\n")
        return 2
    paths = HerculesPaths.from_settings(settings)
    cloud_runs = CloudRuns(paths.project_hercules_dir / "cloud-runs", settings, env, project)
    web, mobile = WebOptions(args.browser, args.os_name), MobileOptions(args.device, args.platform_version)
    if args.cloud_action == "bundle":
        try:
            bundle = cloud_runs.bundle(result, args.cloud, web, mobile, allow_failed=args.allow_failed)
        except CloudRunError as exc:
            err.write(f"hercules: error: {exc}\n")
            return 2
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(bundle.zip_bytes())
        out.write(f"wrote {args.out} ({len(bundle.files)} files: {', '.join(sorted(bundle.files))})\n")
        return 0
    if result.status != "passed" and not args.allow_failed:
        err.write(f"hercules: error: run {result.run_id} did not pass ({result.status}); only passed runs run on a cloud (or pass --allow-failed)\n")
        return 2

    async def go() -> CloudRunRecord:
        record = cloud_runs.start(result, args.cloud, web, mobile)
        err.write(f"started cloud run {record.id} on {record.cloud}; output in {cloud_runs.root / record.id / 'output.log'}\n")
        while True:
            await asyncio.sleep(1.0)
            current = cloud_runs.get(record.id)
            if current is not None and current.status in ("passed", "failed", "error", "cancelled") and not cloud_runs.is_active(record.id):
                return current

    try:
        final = asyncio.run(go())
    except CloudRunError as exc:
        err.write(f"hercules: error: {exc}\n")
        return 2
    record = final
    if args.json:
        out.write(json.dumps(record.to_dict(), indent=2) + "\n")
    else:
        from testzeus_hercules.cli.style import Style

        style = Style.for_stream(out, env)
        out.write(f"{style.status(record.status, record.status + ':')} {record.message or ''}\n")
        if record.session_url:
            out.write(f"{style.dim('session:')} {record.session_url}\n")
        out.write(f"{style.dim('dashboard:')} {record.dashboard_url}\n")
    return {"passed": 0, "failed": 1}.get(record.status, 2)
