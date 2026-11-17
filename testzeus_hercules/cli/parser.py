"""The ``hercules`` argument parser: argparse with "did you mean" for mistyped choices and a coloured error label.

``hercules clod`` says ``did you mean 'cloud'?`` instead of only listing every command; the same goes for
choices such as ``--cloud browsrstack``. Subcommand parsers inherit the class, so every level behaves the same.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any, NoReturn

from testzeus_hercules.cli.style import Style

_INVALID_CHOICE = re.compile(r"invalid choice: '(?P<given>[^']*)' \(choose from (?P<choices>.*)\)")
ERROR_ENV: ContextVar[Mapping[str, str] | None] = ContextVar("hercules_parser_env", default=None)


def did_you_mean(message: str) -> str | None:
    """A suggestion for an argparse "invalid choice" message, or None."""
    match = _INVALID_CHOICE.search(message)
    if match is None:
        return None
    choices = [choice.strip().strip("'\"") for choice in match.group("choices").split(",")]
    close = difflib.get_close_matches(match.group("given"), choices, n=2, cutoff=0.6)
    if not close:
        return None
    return "did you mean " + " or ".join(f"'{choice}'" for choice in close) + "?"


class HerculesArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.suggest_on_error = False

    def error(self, message: str) -> NoReturn:
        style = Style.for_stream(sys.stderr, ERROR_ENV.get())
        self.color = style.enabled
        self.print_usage(sys.stderr)
        hint = did_you_mean(message)
        suffix = f"\n{style.accent('hint:')} {hint}" if hint else ""
        self.exit(2, f"{self.prog}: {style.fail('error:')} {message}{suffix}\n")
