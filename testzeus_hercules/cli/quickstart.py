"""``testzeus-hercules``: the one-line start, ``uvx testzeus-hercules``.

With no command it gets a machine from nothing to a working Hercules, asking before each change:

1. **The ``hercules`` command.** ``uvx`` runs the package from a temporary environment, so the command it would leave
   behind is gone with it. When no permanent ``hercules`` is on ``PATH``, it offers ``uv tool install`` (or ``pipx
   install``) of this same version, and offers to put uv's tool folder on ``PATH``.
2. **Chromium** for Playwright (about 150 MB), unless it is already downloaded.
3. **``hercules setup``**: the local model, the hosted key, egress, telemetry and coding agents. Every flag that is not
   one of this command's own is passed to it, so ``uvx testzeus-hercules --yes --agents claude`` sets up without
   questions.

With a command (``uvx testzeus-hercules doctor``) it is the same as ``hercules <command>``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from testzeus_hercules.cli.style import Style
from testzeus_hercules.cli.version import DISTRIBUTION, package_version

__all__ = ["QuickstartDeps", "main", "install_spec", "is_temporary_env", "chromium_ready"]

PYTHON = "3.12"
FROM_ENV = "HERCULES_INSTALL_FROM"
SOURCE_URL = "git+https://github.com/test-zeus-ai/testzeus-hercules.git"  # development versions are not on PyPI
CHROMIUM_ENV = "HERCULES_CHROMIUM"
OWN_FLAGS_HELP = """\
usage: testzeus-hercules [--no-install] [--browsers LIST] [--from SPEC] [SETUP FLAGS ...]
       testzeus-hercules <command> [...]      (same as `hercules <command>`)

Install Hercules by TestZeus and set it up, asking before each change:
  1. install the `hercules` command (uv tool install, or pipx)
  2. set up the browsers to test in: Chromium (default), Firefox, WebKit, Chrome, Edge
  3. run `hercules setup`: model, hosted key, egress, telemetry, coding agents

options:
  -h, --help     show this help and exit
  --no-install   do not install the `hercules` command
  --browsers LIST
                 browsers to set up without asking: comma-separated chromium, firefox, webkit,
                 chrome, msedge, or none (default: ask; with --yes, chromium and the ones the
                 project's environments use)
  --no-browser   the same as --browsers none
  --from SPEC    what to install instead of this version (a version pin, a wheel, or a git URL);
                 also read from HERCULES_INSTALL_FROM

Every other flag goes to `hercules setup` (see `testzeus-hercules setup --help`), for example
  uvx testzeus-hercules --yes --agents claude
"""


@dataclass(frozen=True)
class Ran:
    code: int
    output: str = ""


def _run(command: Sequence[str], capture: bool) -> Ran:
    try:
        done = subprocess.run(list(command), capture_output=capture, text=True, check=False)
    except OSError as exc:
        return Ran(127, str(exc))
    return Ran(done.returncode, (done.stdout or "") + (done.stderr or "") if capture else "")


@dataclass(frozen=True)
class QuickstartDeps:
    """Seams for tests: prompts, ``PATH`` lookups, commands, and where this Python lives."""

    ask: Callable[[str], str] = input
    which: Callable[[str], str | None] = shutil.which
    run: Callable[[Sequence[str], bool], Ran] = _run
    prefix: Path = field(default_factory=lambda: Path(sys.prefix))
    python: str = sys.executable
    version: Callable[[], str] = package_version
    exists: Callable[[str], bool] = os.path.exists  # an installed Chrome or Edge


# --------------------------------------------------------------------------- pure helpers


def is_temporary_env(prefix: Path) -> bool:
    """Whether ``prefix`` is a throwaway environment, such as the ones ``uvx`` and ``pipx run`` build in a cache."""
    return any("cache" in part.lower() or part.startswith("archive-v") for part in prefix.parts)


def install_spec(version: str, env: Mapping[str, str], override: str | None = None) -> str:
    """What to install: ``--from``, then ``HERCULES_INSTALL_FROM``, then this exact version from PyPI (a development
    version, which PyPI does not have, comes from the repository)."""
    chosen = override or env.get(FROM_ENV, "").strip()
    if chosen:
        return chosen
    if ".dev" in version:
        return SOURCE_URL
    if version and version != "unknown" and "+" not in version:
        return f"{DISTRIBUTION}=={version}"
    return DISTRIBUTION


def chromium_ready(python: str, env: Mapping[str, str], run: Callable[[Sequence[str], bool], Ran]) -> bool:
    """Whether Playwright's Chromium is already downloaded (or ``HERCULES_CHROMIUM`` names a browser)."""
    from testzeus_hercules.cli.quickstart_browsers import browser_ready

    return browser_ready("chromium", python, env, run, lambda command: None)


def _forwarded_command(argv: Sequence[str]) -> bool:
    """Whether ``argv`` starts with a ``hercules`` command, so it is passed through unchanged."""
    if not argv or argv[0].startswith("-"):
        return False
    from testzeus_hercules.cli.main import build_parser

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return argv[0] in action.choices
    return False


# --------------------------------------------------------------------------- the steps


class _Session:
    def __init__(self, deps: QuickstartDeps, env: Mapping[str, str], out: TextIO, yes: bool) -> None:
        self.deps, self.env, self.out, self.yes = deps, env, out, yes
        self.style = Style.for_stream(out, env)

    def say(self, text: str = "") -> None:
        self.out.write(text + "\n")
        self.out.flush()

    def confirm(self, question: str) -> bool:
        if self.yes:
            self.say(f"{question} [Y/n] y (--yes)")
            return True
        try:
            answer = self.deps.ask(f"{question} [Y/n] ").strip().lower()
        except EOFError:
            answer = ""
        return answer in ("", "y", "yes")

    def step(self, number: int, title: str) -> None:
        self.say()
        self.say(self.style.bold(f"{number}. {title}"))


def _hercules_command(session: _Session, spec: str) -> bool:
    """Make sure a permanent ``hercules`` is on ``PATH``; returns whether one is."""
    deps = session.deps
    found = deps.which("hercules")
    uv, pipx = deps.which("uv"), deps.which("pipx")
    if found and not is_temporary_env(Path(found).resolve().parent):
        session.say(f"   {session.style.ok('✓')} found {found}")
        if uv:  # uvx reuses an installed tool's environment, whose folder is not the one on the user's PATH
            _offer_path(session, uv)
        return True
    if uv:
        command = [uv, "tool", "install", "--python", PYTHON, spec]
    elif pipx:
        command = [pipx, "install", spec]
    else:
        session.say(f"   {session.style.warn('!')} neither uv nor pipx is installed, so the `hercules` command cannot be installed.")
        session.say("     Install uv (https://docs.astral.sh/uv/getting-started/installation/), then run this again,")
        session.say(f"     or install with pip: pip install '{spec}'")
        return False
    session.say(f"   Installs {spec} as the `hercules` command, in its own environment:")
    session.say(session.style.dim(f"   $ {' '.join(command)}"))
    if not session.confirm("   Install it?"):
        session.say("   skipped; coding agents start `hercules mcp`, so they need this command")
        return False
    result = deps.run(command, False)
    if result.code != 0:
        session.say(f"   {session.style.fail('✗')} the install failed (exit {result.code}); see the output above")
        return False
    session.say(f"   {session.style.ok('✓')} installed")
    if uv:
        _offer_path(session, uv)
    return True


def _offer_path(session: _Session, uv: str) -> None:
    """Offer ``uv tool update-shell`` when uv's tool folder is not on ``PATH``."""
    result = session.deps.run([uv, "tool", "dir", "--bin"], True)
    folder = result.output.strip().splitlines()[-1] if result.code == 0 and result.output.strip() else ""
    on_path = folder and folder in session.env.get("PATH", "").split(os.pathsep)
    if not folder or on_path:
        return
    session.say(f"   {folder} is not on your PATH yet.")
    if session.confirm("   Add it to your shell's PATH (uv tool update-shell)?"):
        session.deps.run([uv, "tool", "update-shell"], False)
        session.say("   open a new terminal for `hercules` to be found")
    else:
        session.say(f"   add {folder} to PATH yourself, or run `uv tool update-shell`")


def _project(flags: Sequence[str]) -> Path:
    """The ``--project`` passed on to setup, else the current folder (where the saved environments are)."""
    for index, flag in enumerate(flags):
        if flag == "--project" and index + 1 < len(flags):
            return Path(flags[index + 1])
        if flag.startswith("--project="):
            return Path(flag.split("=", 1)[1])
    return Path.cwd()


def _own_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="testzeus-hercules", add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("--no-install", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--browsers", default=None)
    parser.add_argument("--from", dest="source", default=None)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    deps: QuickstartDeps | None = None,
    setup: Callable[[list[str]], int] | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Run ``testzeus-hercules`` and return its exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    environ: Mapping[str, str] = os.environ if env is None else env
    out = stdout if stdout is not None else sys.stdout
    from testzeus_hercules.cli.main import main as hercules_main

    if _forwarded_command(args):
        return hercules_main(args, env=env, stdout=stdout)
    own, rest = _own_parser().parse_known_args(args)
    if own.help:
        out.write(OWN_FLAGS_HELP)
        return 0
    from testzeus_hercules.cli.quickstart_browsers import parse_browsers, setup_browsers

    wanted = () if own.no_browser else parse_browsers(own.browsers)
    if isinstance(wanted, str):
        out.write(f"testzeus-hercules: error: --browsers: {wanted}\n")
        return 2
    deps = deps or QuickstartDeps()
    yes = any(flag in ("--yes", "-y") for flag in rest)
    session = _Session(deps, environ, out, yes)
    session.say(session.style.bold("Hercules by TestZeus") + f" {deps.version()}: install and set up. Nothing changes without a yes.")

    session.step(1, "The `hercules` command")
    if own.no_install:
        session.say("   skipped (--no-install)")
    else:
        _hercules_command(session, install_spec(deps.version(), environ, own.source))

    session.step(2, "Browsers")
    setup_browsers(session, wanted, _project(rest))

    session.step(3, "Setup")
    code = (setup or (lambda flags: hercules_main(["setup", *flags], env=env, stdout=stdout)))(rest)
    if code == 0:
        session.say()
        session.say(session.style.ok("Hercules is ready.") + " Try `hercules doctor`, then ask your coding agent to test a change.")
        session.say("Restart your coding agent if it was open, so it loads the Hercules tools.")
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
