"""A LangGraph checkpoint saver backed by SQLite, so a run can resume after its worker died.

It keeps LangGraph's in-memory saver as the working copy and writes every checkpoint,
channel value and pending write through to a SQLite file (created with mode 0600: a
checkpoint may hold the page's cookies). A new process opening the same file sees the
same checkpoints. Values are serialized with LangGraph's own serializer; only the
engine's state types (and the models and enums nested in them) are allowed back in.

Only the latest checkpoint of a thread and its parent are kept (with their channel values
and pending writes), so the file stays the size of one run state rather than growing with
every step. File writes run on one worker thread, in order, each in its own transaction.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import os
import sqlite3
import threading
import typing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterator, Sequence

from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from testzeus_hercules.cases.automation import AutomationStep
from testzeus_hercules.cases.models import QualityPage, StepResult
from testzeus_hercules.cases.plan import Amendment, CriterionResult
from testzeus_hercules.cases.security import SecurityScanResult
from testzeus_hercules.engine.verify import LedgerEntry

# The types the run graph's state (engine.run_graph.RunState) holds.
STATE_TYPES: tuple[type, ...] = (StepResult, LedgerEntry, QualityPage, SecurityScanResult, AutomationStep, CriterionResult, Amendment)


def _reachable(roots: Sequence[type]) -> tuple[tuple[str, str], ...]:
    """Every pydantic model and enum reachable from ``roots`` through field annotations (the serializer writes a model's
    nested models as plain values, but enums keep their type), as (module, name) pairs."""
    seen: dict[type, None] = {}
    pending: list[Any] = list(roots)
    while pending:
        node = pending.pop()
        if isinstance(node, type) and issubclass(node, (BaseModel, enum.Enum)):
            if node in seen:
                continue
            seen[node] = None
            if issubclass(node, BaseModel):
                pending.extend(field.annotation for field in node.model_fields.values())
            continue
        pending.extend(typing.get_args(node))
    return tuple(sorted((cls.__module__, cls.__qualname__) for cls in seen))


# Anything else in a checkpoint file is refused (comes back as a plain value).
ALLOWED_TYPES: tuple[tuple[str, str], ...] = _reachable(STATE_TYPES)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL, ns TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
    checkpoint_type TEXT NOT NULL, checkpoint BLOB NOT NULL, metadata_type TEXT NOT NULL, metadata BLOB NOT NULL, parent_id TEXT,
    PRIMARY KEY (thread_id, ns, checkpoint_id)
);
CREATE TABLE IF NOT EXISTS blobs (
    thread_id TEXT NOT NULL, ns TEXT NOT NULL, channel TEXT NOT NULL, version TEXT NOT NULL, value_type TEXT NOT NULL, value BLOB NOT NULL,
    PRIMARY KEY (thread_id, ns, channel, version)
);
CREATE TABLE IF NOT EXISTS writes (
    thread_id TEXT NOT NULL, ns TEXT NOT NULL, checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL, idx INTEGER NOT NULL,
    channel TEXT NOT NULL, value_type TEXT NOT NULL, value BLOB NOT NULL, task_path TEXT NOT NULL,
    PRIMARY KEY (thread_id, ns, checkpoint_id, task_id, idx)
);
"""


class SqliteCheckpointer(InMemorySaver):
    """Checkpoints in ``path`` (SQLite); see the module docstring."""

    def __init__(self, path: Path) -> None:
        super().__init__(serde=JsonPlusSerializer(allowed_msgpack_modules=list(ALLOWED_TYPES)))
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        self.path = path
        self._db_lock = threading.Lock()
        # One thread, so file writes land in the order they were made.
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="checkpoint")
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        with self._db_lock:
            self._db.executescript(_SCHEMA)
        self._load()

    def close(self) -> None:
        self._writer.shutdown(wait=True)
        with self._db_lock:
            self._db.close()

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._db_lock:
            self._db.execute("BEGIN")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _load(self) -> None:
        with self._db_lock:
            checkpoints = self._db.execute("SELECT thread_id, ns, checkpoint_id, checkpoint_type, checkpoint, metadata_type, metadata, parent_id FROM checkpoints").fetchall()
            blobs = self._db.execute("SELECT thread_id, ns, channel, version, value_type, value FROM blobs").fetchall()
            writes = self._db.execute("SELECT thread_id, ns, checkpoint_id, task_id, idx, channel, value_type, value, task_path FROM writes").fetchall()
        for thread_id, ns, checkpoint_id, checkpoint_type, checkpoint, metadata_type, metadata, parent_id in checkpoints:
            self.storage[thread_id][ns][checkpoint_id] = ((checkpoint_type, bytes(checkpoint)), (metadata_type, bytes(metadata)), parent_id)
        for thread_id, ns, channel, version, value_type, value in blobs:
            self.blobs[(thread_id, ns, channel, version)] = (value_type, bytes(value))
        for thread_id, ns, checkpoint_id, task_id, idx, channel, value_type, value, task_path in writes:
            self.writes[(thread_id, ns, checkpoint_id)][(task_id, idx)] = (task_id, channel, (value_type, bytes(value)), task_path)

    def put(self, config: Any, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> Any:
        saved, job = self._put(config, checkpoint, metadata, new_versions)
        job()
        return saved

    async def aput(self, config: Any, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> Any:
        saved, job = self._put(config, checkpoint, metadata, new_versions)
        await asyncio.get_running_loop().run_in_executor(self._writer, job)
        return saved

    def put_writes(self, config: Any, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        self._put_writes(config, writes, task_id, task_path)()

    async def aput_writes(self, config: Any, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        job = self._put_writes(config, writes, task_id, task_path)
        await asyncio.get_running_loop().run_in_executor(self._writer, job)

    def _put(self, config: Any, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> tuple[Any, Any]:
        """Store in memory, prune older checkpoints there; returns the saved config and the file write to run."""
        saved = super().put(config, checkpoint, metadata, new_versions)
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"]["checkpoint_ns"]
        stored_checkpoint, stored_metadata, parent_id = self.storage[thread_id][ns][checkpoint["id"]]
        rows = [(thread_id, ns, channel, str(version), *self.blobs[(thread_id, ns, channel, version)]) for channel, version in new_versions.items()]
        keep_ids, keep_blobs = self._prune_memory(thread_id, ns, checkpoint, parent_id)
        row = (thread_id, ns, checkpoint["id"], stored_checkpoint[0], stored_checkpoint[1], stored_metadata[0], stored_metadata[1], parent_id)

        def job() -> None:
            with self._transaction() as db:
                db.execute("INSERT OR REPLACE INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
                db.executemany("INSERT OR REPLACE INTO blobs VALUES (?, ?, ?, ?, ?, ?)", rows)
                marks = ", ".join("?" for _ in keep_ids)
                db.execute(f"DELETE FROM checkpoints WHERE thread_id = ? AND ns = ? AND checkpoint_id NOT IN ({marks})", (thread_id, ns, *keep_ids))  # noqa: S608
                db.execute(f"DELETE FROM writes WHERE thread_id = ? AND ns = ? AND checkpoint_id NOT IN ({marks})", (thread_id, ns, *keep_ids))  # noqa: S608
                stale = [
                    (thread_id, ns, channel, version)
                    for channel, version in db.execute("SELECT channel, version FROM blobs WHERE thread_id = ? AND ns = ?", (thread_id, ns))
                    if (channel, version) not in keep_blobs
                ]
                db.executemany("DELETE FROM blobs WHERE thread_id = ? AND ns = ? AND channel = ? AND version = ?", stale)

        return saved, job

    def _prune_memory(self, thread_id: str, ns: str, checkpoint: Checkpoint, parent_id: str | None) -> tuple[tuple[str, ...], set[tuple[str, str]]]:
        """Drop every checkpoint of ``thread_id``/``ns`` but ``checkpoint`` and its parent, with the writes and channel values
        only they used; returns the kept checkpoint ids and (channel, version as text) pairs."""
        keep_ids = (checkpoint["id"],) + ((parent_id,) if parent_id else ())
        versions = dict.fromkeys((channel, version) for channel, version in checkpoint["channel_versions"].items())
        stored = self.storage[thread_id][ns]
        if parent_id and parent_id in stored:
            parent: Checkpoint = self.serde.loads_typed(stored[parent_id][0])
            versions.update(dict.fromkeys(parent["channel_versions"].items()))
        for old in [key for key in stored if key not in keep_ids]:
            del stored[old]
            self.writes.pop((thread_id, ns, old), None)
        for key in [key for key in self.blobs if key[0] == thread_id and key[1] == ns and (key[2], key[3]) not in versions]:
            del self.blobs[key]
        return keep_ids, {(channel, str(version)) for channel, version in versions}

    def _put_writes(self, config: Any, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str) -> Any:
        super().put_writes(config, writes, task_id, task_path)
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        stored = self.writes.get((thread_id, ns, checkpoint_id), {})
        rows = [(thread_id, ns, checkpoint_id, key[0], key[1], value[1], value[2][0], value[2][1], value[3]) for key, value in stored.items() if key[0] == task_id]

        def job() -> None:
            with self._transaction() as db:
                db.executemany("INSERT OR IGNORE INTO writes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

        return job

    def delete_thread(self, thread_id: str) -> None:
        super().delete_thread(thread_id)
        self._writer.submit(self._delete_rows, thread_id).result()

    async def adelete_thread(self, thread_id: str) -> None:
        super().delete_thread(thread_id)
        await asyncio.get_running_loop().run_in_executor(self._writer, self._delete_rows, thread_id)

    def _delete_rows(self, thread_id: str) -> None:
        with self._transaction() as db:
            for table in ("checkpoints", "blobs", "writes"):
                db.execute(f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,))  # noqa: S608 - fixed table names
