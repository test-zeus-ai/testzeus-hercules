"""Terminal styling for the ``hercules`` command: colour and symbols on a terminal, plain text everywhere else.

Colour is on when the stream is a terminal, ``NO_COLOR`` is unset (https://no-color.org) and ``TERM`` is not
``dumb``. ``FORCE_COLOR`` (any value but ``0`` or empty) turns it on for a stream that is not a terminal, such as
a CI log that renders ANSI colour. Without colour the text is exactly what it would be without this module, so
scripts, pipes and ``--json`` output never see escape codes.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TextIO

_RESET = "\x1b[0m"
_CODES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
}


def color_enabled(stream: TextIO, env: Mapping[str, str] | None = None) -> bool:
    """Whether to colour what is written to ``stream``."""
    environ = os.environ if env is None else env
    if environ.get("NO_COLOR", ""):
        return False
    forced = environ.get("FORCE_COLOR", "")
    if forced and forced != "0":
        return True
    if environ.get("TERM", "") == "dumb":
        return False
    isatty = getattr(stream, "isatty", None)
    try:
        return bool(isatty and isatty())
    except (OSError, ValueError):  # a closed or detached stream
        return False


def terminal_width(default: int = 100) -> int:
    return max(40, shutil.get_terminal_size((default, 24)).columns)


@dataclass(frozen=True)
class Style:
    """Wraps text in ANSI codes when ``enabled``; returns it unchanged otherwise."""

    enabled: bool = False

    @classmethod
    def for_stream(cls, stream: TextIO, env: Mapping[str, str] | None = None) -> Style:
        return cls(color_enabled(stream, env))

    def paint(self, text: str, *styles: str) -> str:
        if not self.enabled or not text:
            return text
        codes = ";".join(_CODES[name] for name in styles)
        return f"\x1b[{codes}m{text}{_RESET}"

    def bold(self, text: str) -> str:
        return self.paint(text, "bold")

    def dim(self, text: str) -> str:
        return self.paint(text, "dim")

    def ok(self, text: str) -> str:
        return self.paint(text, "green")

    def warn(self, text: str) -> str:
        return self.paint(text, "yellow")

    def fail(self, text: str) -> str:
        return self.paint(text, "red", "bold")

    def accent(self, text: str) -> str:
        return self.paint(text, "cyan")

    def status(self, status: str, text: str | None = None) -> str:
        """``text`` (default ``status``) coloured by what the status means."""
        shown = status if text is None else text
        key = status.lower()
        if key in ("ok", "passed", "pass", "ready", "connected"):
            return self.ok(shown)
        if key in ("warn", "warning", "skipped", "skip", "cancelled", "not run"):
            return self.warn(shown) if key.startswith("warn") else self.dim(shown)
        if key in ("fail", "failed", "error", "blocked"):
            return self.fail(shown)
        return shown

    def symbol(self, status: str) -> str:
        """A one-character mark for a step status (only used on a terminal)."""
        return {
            "passed": self.ok("✓"),
            "failed": self.fail("✗"),
            "error": self.fail("✗"),
            "blocked": self.fail("✗"),
            "skipped": self.dim("○"),
            "cancelled": self.dim("○"),
            "running": self.accent("▸"),
            "recovering": self.warn("↻"),
        }.get(status, "·")
