"""Live step progress for ``hercules run``: one line per step as the run goes.

On a terminal the current step is shown on a line that is rewritten in place (``▸ 2/5 …`` becomes ``✓ 2/5 …``);
anywhere else (a pipe, a CI log) only finished steps and recoveries are printed, one plain line each, so logs
stay readable. It is an :class:`~testzeus_hercules.kernel.run_context.EventSink`; with ``--json`` it is not used,
so standard output stays one JSON document.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any, TextIO

from testzeus_hercules.cli.style import Style, terminal_width

_LOG = logging.getLogger(__name__)
_CLEAR_LINE = "\r\x1b[2K"


class StepProgress:
    """Writes run progress to ``stream``; ``live`` rewrites the current line (a terminal)."""

    def __init__(self, stream: TextIO, style: Style, *, live: bool | None = None, width: int | None = None) -> None:
        self._out = stream
        self._style = style
        self._live = style.enabled if live is None else live
        self._width = width or terminal_width()
        self._total: int | None = None
        self._actions: dict[int, str] = {}
        self._started: dict[int, float] = {}
        self._open = False  # a live line is waiting to be rewritten

    def emit(self, kind: str, /, **data: Any) -> None:
        # Progress is a display: a problem drawing it must never stop the run.
        try:
            self._handle(kind, data)
        except Exception:  # noqa: BLE001 - logged, and the run goes on
            _LOG.debug("could not show run progress for %s", kind, exc_info=True)

    def close(self) -> None:
        """End a live line left open (a cancelled or crashed run)."""
        if self._open:
            self._out.write("\n")
            self._out.flush()
            self._open = False

    def _handle(self, kind: str, data: Mapping[str, Any]) -> None:
        if kind == "run_started":
            self._total = data.get("steps")
            return
        if kind == "step_started":
            step = int(data["step"])
            self._actions[step] = str(data.get("action") or "")
            self._started[step] = time.monotonic()
            if self._live:
                self._line(f"  {self._style.symbol('running')} {self._label(step)}", final=False)
            return
        if kind == "step_recovering":
            step = int(data["step"])
            reason = data.get("reason")
            why = f": {reason}" if reason else ""
            note = self._style.warn(f"recovering, attempt {data.get('attempt', '?')}{why}")
            if self._live:
                self._line(f"  {self._style.symbol('recovering')} {self._label(step)}  {note}", final=False)
            else:
                self._line(f"  {'recovering':<10} step {self._position(step)}  attempt {data.get('attempt', '?')}{why}", final=True)
            return
        if kind in ("step_finished", "step_skipped"):
            step = int(data["step"])
            status = str(data.get("status") or "skipped")
            self._finish(step, status, data.get("duration_ms"))
            return
        if kind == "run_finished":
            self.close()

    def _finish(self, step: int, status: str, duration_ms: Any) -> None:
        started = self._started.pop(step, None)
        seconds = duration_ms / 1000 if isinstance(duration_ms, (int, float)) else (time.monotonic() - started if started is not None else None)
        took = f"  {self._style.dim(f'{seconds:.1f}s')}" if seconds is not None else ""
        if self._live:
            self._line(f"  {self._style.symbol(status)} {self._label(step)}{took}", final=True)
        else:
            self._line(f"  {status:<10} step {self._position(step)}  {self._action(step)}{took}", final=True)

    def _position(self, step: int) -> str:
        return f"{step}/{self._total}" if self._total else str(step)

    def _action(self, step: int) -> str:
        return " ".join(self._actions.get(step, "").split())

    def _label(self, step: int) -> str:
        position = self._position(step)
        # Leave room for the mark, the timing and a recovery note so a live line never wraps.
        room = max(10, self._width - len(position) - 30)
        action = self._action(step)
        if len(action) > room:
            action = action[: room - 1].rstrip() + "…"
        return f"{self._style.bold(position)} {action}"

    def _line(self, text: str, *, final: bool) -> None:
        if self._live:
            self._out.write(_CLEAR_LINE + text + ("\n" if final else ""))
            self._open = not final
        else:
            self._out.write(text + "\n")
        self._out.flush()
