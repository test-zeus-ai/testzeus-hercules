"""Persistent cross-run selector memory.

Stores alternative element selectors (``xpath``/``aria``/``css``) keyed by a
canonical element name such as ``role=button|name=Submit``, so later runs can
heal element lookup failures without a database: the whole store is a single
JSON file, written atomically (tmp file + ``os.replace``) with a bounded flush
cadence — small stores persist on every mutation, larger ones batch the
whole-store dump until ``_FLUSH_EVERY`` mutations accumulated since the last
flush (or the store grew by more than ``_FLUSH_GROWTH_FRACTION``); an explicit
``save()`` always flushes immediately.

Entries accumulate ``hits`` (successful lookups, tracked in memory and flushed
on the next save — a mutating call on small stores, the next cadence flush on
large ones) and ``failures``; entries that keep failing without ever being hit
are pruned so stale selectors are forgotten.
"""

import json
import os
from datetime import datetime, timezone
from typing import Callable

from testzeus_hercules.utils.logger import logger

# Keys of a store entry that are metadata rather than selector kinds.
META_KEYS = frozenset({"last_seen", "hits", "failures", "source"})

# An entry is forgotten once it accumulated at least this many failures ...
FAILURES_PRUNE_THRESHOLD = 5

# Flush cadence: stores with fewer than _FLUSH_EVERY entries keep the legacy
# write-on-every-mutation behavior (a crash cannot lose recent heal data);
# larger stores defer the whole-store JSON dump until _FLUSH_EVERY mutations
# piled up since the last flush, or the store grew by more than
# _FLUSH_GROWTH_FRACTION — so a record burst on a big store costs a handful of
# writes instead of one full serialization per record. save() flushes
# unconditionally.
_FLUSH_EVERY = 25
_FLUSH_GROWTH_FRACTION = 0.10

EventCallback = Callable[[dict], None]

_default_store: "SelectorStore | None" = None


def _default_store_path() -> str:
    """Resolve the default store path: ``<project_source_root>/selector_store.json``."""
    project_source_root = None
    try:
        # Lazy import: config pulls in dotenv/telemetry and must not be loaded
        # merely by importing this module.
        from testzeus_hercules.config import get_global_conf

        project_source_root = get_global_conf().get_project_source_root()
    except Exception as exc:
        logger.debug("Could not read project source root from config: %s", exc)
    return os.path.join(project_source_root or "./opt", "selector_store.json")


class SelectorStore:
    """JSON-file-backed mapping of canonical element names to selectors."""

    def __init__(self, store_path: str | None = None, on_event: EventCallback | None = None) -> None:
        """
        Args:
            store_path (str | None): Path of the JSON store file. Defaults to
                ``<project_source_root>/selector_store.json`` (falls back to
                ``./opt`` when config is unavailable).
            on_event (EventCallback | None): Optional callback invoked with an
                event dict on every mutation (matches the
                ``record_healing_event(run_id, case_key, kind, canonical_name,
                detail, status)`` shape used by run_db, minus the ids).
        """
        self.store_path = os.path.abspath(store_path) if store_path else _default_store_path()
        self._on_event = on_event
        self._entries: dict[str, dict] = {}
        self._mutations_since_flush = 0
        self._size_at_flush = 0
        self._load()
        self._size_at_flush = len(self._entries)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, canonical_name: str, selectors: dict[str, str], source: str = "observed") -> None:
        """Add or merge selectors for a canonical element name, then autosave."""
        entry = self._entries.setdefault(canonical_name, {})
        for kind, value in (selectors or {}).items():
            if kind in META_KEYS or not isinstance(value, str) or not value.strip():
                continue
            entry[kind] = value
        entry["last_seen"] = datetime.now(timezone.utc).isoformat()
        entry["source"] = source
        entry.setdefault("hits", 0)
        entry.setdefault("failures", 0)
        self._maybe_save()
        kinds = ", ".join(sorted(kind for kind in entry if kind not in META_KEYS)) or "(none)"
        self._emit({"kind": "selector_recorded", "canonical_name": canonical_name, "detail": f"selectors: {kinds}, source={source}", "status": "ok"})

    def lookup(self, canonical_name: str) -> dict | None:
        """Return a copy of the entry for ``canonical_name`` (incrementing hits).

        Hits are counted in memory only; they reach disk on the next flush (see
        the module docstring: immediately on small stores, at the next cadence
        flush on large ones, always on ``save()``). Lookup is a hot path, so it
        never pays for a full JSON dump + atomic rename.
        """
        entry = self._entries.get(canonical_name)
        if entry is None:
            return None
        entry["hits"] = entry.get("hits", 0) + 1
        return dict(entry)

    def record_failure(self, canonical_name: str) -> None:
        """Count a failed lookup; prune entries that are stale (fail >= 5, fail > hits)."""
        entry = self._entries.get(canonical_name)
        if entry is None:
            return
        entry["failures"] = entry.get("failures", 0) + 1
        status = "ok"
        if entry["failures"] >= FAILURES_PRUNE_THRESHOLD and entry["failures"] > entry.get("hits", 0):
            del self._entries[canonical_name]
            status = "pruned"
            logger.info("Forgot stale selector entry %s from %s", canonical_name, self.store_path)
        self._maybe_save()
        self._emit(
            {
                "kind": "selector_failed",
                "canonical_name": canonical_name,
                "detail": f"failures={entry['failures']}, hits={entry.get('hits', 0)}",
                "status": status,
            }
        )

    def _maybe_save(self) -> None:
        """Persist per the flush cadence (see module docstring); never raises."""
        self._mutations_since_flush += 1
        if len(self._entries) < _FLUSH_EVERY:
            self.save()
            return
        grew_beyond_cadence = len(self._entries) > self._size_at_flush * (1 + _FLUSH_GROWTH_FRACTION) + 1
        if self._mutations_since_flush >= _FLUSH_EVERY or grew_beyond_cadence:
            self.save()

    def save(self) -> None:
        """Persist the store atomically (tmp file + os.replace); never raises.

        Explicit save() is an unconditional flush: it also resets the cadence
        (pending mutation count and last-flushed size).
        """
        self._mutations_since_flush = 0
        self._size_at_flush = len(self._entries)
        try:
            parent = os.path.dirname(self.store_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp_path = self.store_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as store_file:
                json.dump(self._entries, store_file, indent=2, sort_keys=True)
            os.replace(tmp_path, self.store_path)
        except OSError as exc:
            logger.warning("Could not persist selector store %s: %s", self.store_path, exc)

    def entries(self) -> dict:
        """Return a shallow copy of all entries keyed by canonical name."""
        return {name: dict(entry) for name, entry in self._entries.items()}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load entries from disk; a corrupt or non-object file starts fresh."""
        if not os.path.isfile(self.store_path):
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as store_file:
                data = json.load(store_file)
            if not isinstance(data, dict):
                raise ValueError("store root must be a JSON object")
            self._entries = {name: self._coerce_entry(value) for name, value in data.items() if isinstance(value, dict)}
        except Exception as exc:
            logger.warning("Selector store %s is corrupt (%s); starting fresh.", self.store_path, exc)
            self._entries = {}

    @staticmethod
    def _coerce_entry(entry: dict) -> dict:
        """Force numeric counters back to ints; junk in the file (e.g. "failures": "many") must not crash healing."""
        for key in ("hits", "failures"):
            try:
                entry[key] = int(entry.get(key, 0))
            except (TypeError, ValueError):
                entry[key] = 0
        return entry

    def _emit(self, event: dict) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception as exc:
            logger.warning("on_event callback failed: %s", exc)


def get_default_store() -> SelectorStore:
    """Return the module-level cached default store (created on first use)."""
    global _default_store
    if _default_store is None:
        _default_store = SelectorStore()
    return _default_store
