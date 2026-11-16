"""The browser page's console and network activity during a run, as JSON lines in the run directory.

``console.jsonl`` holds one record per console message and uncaught page error;
``network.jsonl`` one per response (method, URL, status, resource type, request
headers) and per failed request. Everything passes through the run's redactor
(secret values) and the evidence redaction helpers (credentialed URLs, secret query
values, auth and cookie headers), like every other evidence file. Each file holds at
most :data:`MAX_RECORDS` records; one last record says how many were left out.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from testzeus_hercules.act.redact import redact_headers, redact_text, redact_url
from testzeus_hercules.engine.masking import masked_json

__all__ = ["CONSOLE_FILE", "MAX_RECORDS", "NETWORK_FILE", "PageLog"]

logger = logging.getLogger(__name__)

CONSOLE_FILE = "console.jsonl"
NETWORK_FILE = "network.jsonl"
MAX_RECORDS = 5000
_TEXT_CLIP = 2000
_URL_CLIP = 2000


class _Sink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.count = 0
        self.dropped = 0
        self._handle: Any = None  # opened on the first record, kept open (line buffered) until close

    def write(self, record: dict[str, Any], redact: Callable[[str], str]) -> None:
        if self.count >= MAX_RECORDS:
            self.dropped += 1
            return
        self.count += 1
        line = masked_json(record, redact, sort_keys=True)
        try:
            if self._handle is None:
                self._handle = self.path.open("a", encoding="utf-8", buffering=1)
            self._handle.write(line + "\n")
        except OSError as exc:  # evidence is best effort
            logger.warning("could not write %s (%s)", self.path.name, type(exc).__name__)

    def close(self, redact: Callable[[str], str]) -> None:
        if self.dropped:
            self.count = 0  # let the note through
            self.write({"note": f"{self.dropped} more records were left out (limit {MAX_RECORDS})"}, redact)
            self.dropped = 0
        if self._handle is not None:
            handle, self._handle = self._handle, None
            try:
                handle.close()
            except OSError as exc:
                logger.warning("could not close %s (%s)", self.path.name, type(exc).__name__)


class PageLog:
    """Listens to one page's console and network events and appends them to the run directory.

    Code that watches the page while the run goes on (the exploration oracles) subscribes with :meth:`subscribe`: it
    gets every record as written (already redacted), with its kind, ``console`` or ``network``.
    """

    def __init__(self, run_dir: Path, redact: Callable[[str], str]) -> None:
        self.run_dir = run_dir
        self._redact = redact
        self._console = _Sink(run_dir / CONSOLE_FILE)
        self._network = _Sink(run_dir / NETWORK_FILE)
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []

    def subscribe(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        """Call ``listener(kind, record)`` for every record; a failing listener never breaks the run."""
        self._listeners.append(listener)

    def _notify(self, kind: str, record: dict[str, Any]) -> None:
        for listener in self._listeners:
            try:
                listener(kind, record)
            except Exception as exc:
                logger.debug("page log listener failed (%s)", type(exc).__name__)

    @property
    def console_path(self) -> Path | None:
        return self._console.path if self._console.path.exists() else None

    @property
    def network_path(self) -> Path | None:
        return self._network.path if self._network.path.exists() else None

    def attach(self, page: Any) -> None:
        on = getattr(page, "on", None)
        if not callable(on):
            return
        self.run_dir.mkdir(parents=True, exist_ok=True)
        on("console", self._on_console)
        on("pageerror", self._on_page_error)
        on("response", self._on_response)
        on("requestfailed", self._on_request_failed)

    def close(self) -> None:
        self._console.close(self._redact)
        self._network.close(self._redact)

    def _text(self, value: Any) -> str:
        text = redact_text(str(value))
        return text if len(text) <= _TEXT_CLIP else text[: _TEXT_CLIP - 1] + "…"

    @staticmethod
    def _url(value: Any) -> str:
        url = redact_url(str(value or ""))
        return url if len(url) <= _URL_CLIP else url[: _URL_CLIP - 1] + "…"

    def _on_console(self, message: Any) -> None:
        try:
            location = getattr(message, "location", None) or {}
            record = {"at": _now(), "type": str(getattr(message, "type", "log")), "text": self._text(getattr(message, "text", ""))}
            if isinstance(location, dict) and location.get("url"):
                record["source"] = f"{self._url(location.get('url'))}:{location.get('lineNumber', 0)}"
            self._console.write(record, self._redact)
            self._notify("console", record)
        except Exception as exc:  # a listener must never break the run
            logger.debug("console record skipped (%s)", type(exc).__name__)

    def _on_page_error(self, error: Any) -> None:
        try:
            record = {"at": _now(), "type": "pageerror", "text": self._text(getattr(error, "message", None) or error)}
            stack = getattr(error, "stack", None)
            if stack:
                record["stack"] = self._text(stack)[:4000]
            self._console.write(record, self._redact)
            self._notify("console", record)
        except Exception as exc:
            logger.debug("page error record skipped (%s)", type(exc).__name__)

    def _on_response(self, response: Any) -> None:
        try:
            request = response.request
            record = {
                "at": _now(),
                "method": str(request.method),
                "url": self._url(response.url),
                "status": int(response.status),
                "resource_type": str(getattr(request, "resource_type", "") or ""),
                "request_headers": [list(pair) for pair in redact_headers(sorted((request.headers or {}).items()))],
            }
            self._network.write(record, self._redact)
            self._notify("network", record)
        except Exception as exc:
            logger.debug("network record skipped (%s)", type(exc).__name__)

    def _on_request_failed(self, request: Any) -> None:
        try:
            failure = getattr(request, "failure", None)
            record = {
                "at": _now(),
                "method": str(request.method),
                "url": self._url(request.url),
                "failed": self._text(failure if isinstance(failure, str) else getattr(failure, "error_text", failure) or "failed"),
                "resource_type": str(getattr(request, "resource_type", "") or ""),
            }
            self._network.write(record, self._redact)
            self._notify("network", record)
        except Exception as exc:
            logger.debug("failed-request record skipped (%s)", type(exc).__name__)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"
