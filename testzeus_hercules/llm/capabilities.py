"""Measured server capabilities and their on-disk cache (``~/.hercules/capabilities.json``).

Entries are keyed by (base URL, model id, server version) so an upgraded
server is probed again. A missing or corrupt file is treated as empty.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)

ImageMode = Literal["data_url", "none"]

_FORMAT_VERSION = 1
_UNKNOWN_VERSION = "unknown"


class Capabilities(BaseModel):
    """What preflight measured for one model on one server."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    server_version: str | None
    model_id: str
    context_tokens: int | None
    supports_tools: bool
    supports_tool_choice: bool
    supports_json_schema: bool
    image_mode: ImageMode


def capability_url(base_url: str, request_params: Mapping[str, Any] | None = None) -> str:
    """The base URL a capability entry is filed under: with a digest of ``llm.request_params`` when there are any,
    since they can change what the server does (a reasoning model answering directly, or not at all)."""
    if not request_params:
        return base_url
    digest = hashlib.sha256(json.dumps(dict(request_params), sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
    return f"{base_url}#params={digest}"


def cache_key(base_url: str, model_id: str, server_version: str | None) -> str:
    return f"{base_url.rstrip('/')}|{model_id}|{server_version or _UNKNOWN_VERSION}"


class CapabilityCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self, base_url: str, model_id: str, server_version: str | None) -> Capabilities | None:
        entry = self._entries().get(cache_key(base_url, model_id, server_version))
        if entry is None:
            return None
        try:
            return Capabilities.model_validate(entry)
        except ValidationError:
            return None

    def put(self, base_url: str, capabilities: Capabilities) -> None:
        """Store an entry, replacing the file atomically. The read-modify-write is locked (threads in this process,
        and other processes where file locks exist), so concurrent probes never drop each other's entries. Write
        failures are logged, not raised."""
        try:
            with _locked(self.path):
                entries = self._entries()
                entries[cache_key(base_url, capabilities.model_id, capabilities.server_version)] = capabilities.model_dump(mode="json")
                self._write({"version": _FORMAT_VERSION, "entries": entries})
        except OSError as exc:
            logger.warning("could not write the capability cache: %s", exc.__class__.__name__)

    def _entries(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return {}
        entries = data.get("entries") if isinstance(data, dict) and data.get("version") == _FORMAT_VERSION else None
        return dict(entries) if isinstance(entries, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _thread_lock(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(path.resolve()), threading.Lock())


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold the cache's lock: an in-process lock, plus an exclusive lock on the cache's folder where ``fcntl`` exists
    (no extra file is left behind)."""
    with _thread_lock(path):
        try:
            import fcntl
        except ImportError:  # Windows: in-process locking only; the write itself stays atomic
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)  # closing releases the lock
