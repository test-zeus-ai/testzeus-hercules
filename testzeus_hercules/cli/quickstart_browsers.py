"""``testzeus-hercules`` step 2: which browsers to set up for Playwright.

Chromium is the default. The browsers the project's saved environments name (``.hercules/envs/*.json``) are suggested
too. Playwright's own Chromium, Firefox and WebKit are downloaded; Google Chrome and Microsoft Edge are the installed
browsers, which Playwright's installer can add (it may ask for your password). ``--browsers`` names them up front.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from testzeus_hercules.cases.browser_options import BROWSERS

if TYPE_CHECKING:
    from testzeus_hercules.cli.quickstart import Ran, _Session

__all__ = ["DEFAULT", "browser_ready", "parse_browsers", "project_browsers", "setup_browsers"]

DEFAULT = "chromium"
CHROMIUM_ENV = "HERCULES_CHROMIUM"
_ABOUT = {
    "chromium": "Playwright's Chromium, about 150 MB",
    "firefox": "Playwright's Firefox, about 90 MB",
    "webkit": "Playwright's WebKit (Safari's engine), about 70 MB",
    "chrome": "Google Chrome, installed for the whole machine",
    "msedge": "Microsoft Edge, installed for the whole machine",
}
# Where an installed Chrome or Edge lives (Playwright reports these as <system>).
_SYSTEM = {
    "chrome": ("/Applications/Google Chrome.app", "/opt/google/chrome/chrome", "C:/Program Files/Google/Chrome/Application/chrome.exe"),
    "msedge": ("/Applications/Microsoft Edge.app", "/opt/microsoft/msedge/msedge", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
}
_COMMANDS = {"chrome": ("google-chrome", "google-chrome-stable"), "msedge": ("microsoft-edge", "microsoft-edge-stable")}

Run = Callable[[Sequence[str], bool], "Ran"]


def parse_browsers(value: str | None) -> tuple[str, ...] | str | None:
    """``--browsers firefox,webkit``: the names; ``none``: (); None when not given; an error message for an unknown name."""
    if value is None:
        return None
    names = tuple(dict.fromkeys(name.strip().lower() for name in value.split(",") if name.strip()))
    names = tuple("msedge" if name == "edge" else name for name in names)
    if names in ((), ("none",)):
        return ()
    unknown = [name for name in names if name not in BROWSERS]
    if unknown:
        return f"unknown browser {unknown[0]!r}; use any of {', '.join(BROWSERS)}, or none"
    return names


def project_browsers(project: Path) -> list[str]:
    """The browsers the project's saved environments name (unreadable files are skipped)."""
    found: list[str] = []
    for path in sorted((project / ".hercules" / "envs").glob("*.json")):
        try:
            browser = json.loads(path.read_text(encoding="utf-8")).get("browser")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(browser, str) and browser in BROWSERS and browser not in found:
            found.append(browser)
    return found


def browser_ready(name: str, python: str, env: Mapping[str, str], run: Run, which: Callable[[str], str | None], exists: Callable[[str], bool] = os.path.exists) -> bool:
    """Whether ``name`` can start now: Playwright's download is there, or Chrome / Edge is installed."""
    if name in _SYSTEM:
        return any(exists(place) for place in _SYSTEM[name]) or any(which(command) for command in _COMMANDS[name])
    if name == "chromium":
        named = env.get(CHROMIUM_ENV, "").strip()
        if named and Path(named).exists():
            return True
    result = run([python, "-m", "playwright", "install", "--dry-run", name], True)
    if result.code != 0:
        return False
    places = [line.split(":", 1)[1].strip() for line in result.output.splitlines() if line.strip().startswith("Install location:")]
    own = [place for place in places if Path(place).name.split("-")[0].split("_")[0] == name]  # chromium-1148, chromium_headless_shell-1148
    return bool(own) and all(Path(place).is_dir() for place in own)


def _choose(session: _Session, ready: dict[str, bool], suggested: list[str]) -> tuple[str, ...]:
    for name in BROWSERS:
        # Pad the visible words, not the colour codes around them, so the descriptions line up.
        mark = session.style.ok("✓") + " ready   " if ready[name] else session.style.dim("not set up")
        tag = "  (your environments use it)" if name in suggested and name != DEFAULT else ""
        session.say(f"   {name:<9} {mark} {_ABOUT[name]}{tag}")
    default = ",".join(suggested)
    if session.yes:
        session.say(f"   Browsers to set up: {default} (--yes; --browsers names others)")
        return tuple(suggested)
    while True:
        try:
            answer = session.deps.ask(f"   Browsers to set up, comma-separated (none to skip) [{default}]: ").strip()
        except EOFError:
            answer = ""
        chosen = parse_browsers(answer or default)
        if isinstance(chosen, str):
            session.say(f"   {chosen}")
            continue
        return chosen or ()


def setup_browsers(session: _Session, wanted: tuple[str, ...] | None, project: Path) -> list[str]:
    """Set up the browsers asked for (``wanted``; None: ask); returns the ones that are ready afterwards."""
    deps = session.deps
    ready = {name: browser_ready(name, deps.python, session.env, deps.run, deps.which, deps.exists) for name in BROWSERS}
    suggested = [DEFAULT, *[name for name in project_browsers(project) if name != DEFAULT]]
    chosen = _choose(session, ready, suggested) if wanted is None else wanted
    if wanted is not None:
        session.say(f"   Browsers to set up: {', '.join(chosen) or 'none'} (--browsers)")
    if not chosen:
        session.say("   skipped; later: python -m playwright install chromium (or firefox, webkit, chrome, msedge)")
    done: list[str] = []
    for name in chosen:
        if ready[name]:
            session.say(f"   {session.style.ok('✓')} {name} is ready")
            done.append(name)
            continue
        if name in _SYSTEM:
            session.say(f"   installing {_ABOUT[name]}: Playwright's installer may ask for your password")
        result = deps.run([deps.python, "-m", "playwright", "install", name], False)
        if result.code == 0:
            session.say(f"   {session.style.ok('✓')} {name} set up")
            done.append(name)
        else:
            hint = f"install {_ABOUT[name].split(',')[0]} yourself, or run" if name in _SYSTEM else "try"
            session.say(f"   {session.style.fail('✗')} {name} could not be set up (exit {result.code}); {hint}: python -m playwright install {name}")
    if done and sys.platform.startswith("linux"):
        session.say(f"   on a bare Linux machine they may also need system libraries: sudo python -m playwright install-deps {' '.join(done)}")
    return done
