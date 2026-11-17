"""The daemon's run queue and event log, persisted in ``<project>/.hercules/history.db`` (SQLite).

Runs move through ``queued → running → passed | failed | error | cancelled``; a draft
case waits in ``needs_approval`` until approved. A running run may be ``paused`` by the
tester or wait in ``needs_approval`` for a decision, or in ``needs_plan_approval`` for its
plan's approval before the browser starts (its worker stays alive), then goes back to ``running``. Events (from the worker and from the
daemon itself) are numbered per run from 1 so clients can resume a stream with
``Last-Event-ID``.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

FINAL_STATUSES = frozenset({"passed", "failed", "error", "cancelled"})
OPEN_STATUSES = frozenset({"queued", "running", "paused", "needs_approval", "needs_plan_approval"})
# Statuses of a run whose worker is alive.
LIVE_STATUSES = frozenset({"running", "paused", "needs_approval", "needs_plan_approval"})
ALL_STATUSES = FINAL_STATUSES | OPEN_STATUSES
_DB_MODE = 0o600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    case_id TEXT,
    title TEXT,
    case_json TEXT NOT NULL,
    options_json TEXT NOT NULL,
    source_json TEXT,
    parent_run_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    total_steps INTEGER,
    steps_done INTEGER NOT NULL DEFAULT 0,
    current_step INTEGER,
    current_action TEXT,
    reason TEXT,
    worker_pid INTEGER,
    exit_code INTEGER
);
CREATE INDEX IF NOT EXISTS runs_status ON runs (status, created_at);
CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    data_json TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""
_UPDATABLE = frozenset({"status", "case_json", "case_id", "title", "started_at", "finished_at", "total_steps", "steps_done", "current_step", "current_action", "reason", "worker_pid", "exit_code"})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class RunStore:
    """Thread-safe access to the runs and events tables."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        # The runs table holds each case with its real values: the database and its WAL files are this user's only.
        os.close(os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), _DB_MODE))
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
        for name in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            with contextlib.suppress(FileNotFoundError):
                os.chmod(name, _DB_MODE)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------ runs

    def create_run(
        self,
        run_id: str,
        *,
        case: Mapping[str, Any],
        options: Mapping[str, Any],
        status: str = "queued",
        source: Mapping[str, Any] | None = None,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        steps = case.get("steps")
        with self._lock:
            self._db.execute(
                "INSERT INTO runs (id, status, case_id, title, case_json, options_json, source_json, parent_run_id, created_at, total_steps) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    status,
                    case.get("id"),
                    case.get("title"),
                    json.dumps(case),
                    json.dumps(dict(options)),
                    json.dumps(dict(source)) if source is not None else None,
                    parent_run_id,
                    now_iso(),
                    len(steps) if isinstance(steps, list) else None,
                ),
            )
        record = self.get_run(run_id)
        assert record is not None
        return record

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _run(row) if row is not None else None

    def delete_run(self, run_id: str) -> bool:
        with self._lock:
            self._db.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
            deleted = self._db.execute("DELETE FROM runs WHERE id = ?", (run_id,)).rowcount
        return deleted > 0

    def list_runs(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM runs", []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        return [_run(row) for row in rows]

    def queued(self) -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT id FROM runs WHERE status = 'queued' ORDER BY created_at, rowid").fetchall()
        return [row["id"] for row in rows]

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) AS n FROM runs WHERE status IN ('queued', 'running', 'paused', 'needs_approval', 'needs_plan_approval') GROUP BY status").fetchall()
        found = {row["status"]: row["n"] for row in rows}
        return {status: int(found.get(status, 0)) for status in ("queued", "running", "paused", "needs_approval", "needs_plan_approval")}

    def update_run(self, run_id: str, **fields: Any) -> None:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"cannot update run fields: {', '.join(sorted(unknown))}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._lock:
            self._db.execute(f"UPDATE runs SET {assignments} WHERE id = ?", (*fields.values(), run_id))  # noqa: S608 - column names are allow-listed

    def transition(self, run_id: str, expected: Iterable[str], status: str, **fields: Any) -> bool:
        """Set ``status`` only if the run is currently in one of ``expected``; True when it changed."""
        allowed = tuple(expected)
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"cannot update run fields: {', '.join(sorted(unknown))}")
        assignments = ", ".join(["status = ?", *(f"{name} = ?" for name in fields)])
        placeholders = ", ".join("?" for _ in allowed)
        with self._lock:
            cursor = self._db.execute(
                f"UPDATE runs SET {assignments} WHERE id = ? AND status IN ({placeholders})",  # noqa: S608 - column names are allow-listed
                (status, *fields.values(), run_id, *allowed),
            )
        return cursor.rowcount == 1

    def recover_after_restart(self) -> list[str]:
        """Runs whose worker a previous daemon left behind become ``error``; returns their ids.

        Queued runs stay queued and drafts stay in ``needs_approval`` (they have no worker).
        """
        live = "status IN ('running', 'paused', 'needs_plan_approval') OR (status = 'needs_approval' AND worker_pid IS NOT NULL)"
        with self._lock:
            rows = self._db.execute(f"SELECT id FROM runs WHERE {live}").fetchall()  # noqa: S608 - fixed text
            ids = [row["id"] for row in rows]
            self._db.execute(
                f"UPDATE runs SET status = 'error', reason = ?, finished_at = ?, worker_pid = NULL WHERE {live}",  # noqa: S608 - fixed text
                ("the daemon stopped while this run was running", now_iso()),
            )
        return ids

    # ------------------------------------------------------------------ events

    def add_event(self, run_id: str, kind: str, data: Mapping[str, Any]) -> int:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM run_events WHERE run_id = ?", (run_id,)).fetchone()
            seq = int(row["next"])
            self._db.execute("INSERT INTO run_events (run_id, seq, kind, data_json, at) VALUES (?, ?, ?, ?, ?)", (run_id, seq, kind, json.dumps(dict(data), default=str), now_iso()))
        return seq

    def events(self, run_id: str, *, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT seq, kind, data_json, at FROM run_events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?", (run_id, after, limit)).fetchall()
        return [{"seq": row["seq"], "kind": row["kind"], "data": json.loads(row["data_json"]), "at": row["at"]} for row in rows]

    def last_event(self, run_id: str, kinds: Iterable[str]) -> dict[str, Any] | None:
        """The newest event of one of ``kinds``, or None."""
        wanted = tuple(kinds)
        placeholders = ", ".join("?" for _ in wanted)
        with self._lock:
            row = self._db.execute(
                f"SELECT seq, kind, data_json, at FROM run_events WHERE run_id = ? AND kind IN ({placeholders}) ORDER BY seq DESC LIMIT 1",  # noqa: S608 - placeholders only
                (run_id, *wanted),
            ).fetchone()
        return {"seq": row["seq"], "kind": row["kind"], "data": json.loads(row["data_json"]), "at": row["at"]} if row is not None else None

    def last_seq(self, run_id: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(MAX(seq), 0) AS last FROM run_events WHERE run_id = ?", (run_id,)).fetchone()
        return int(row["last"])


def _run(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    record["case"] = json.loads(record.pop("case_json"))
    record["options"] = json.loads(record.pop("options_json"))
    source = record.pop("source_json")
    record["source"] = json.loads(source) if source else None
    return record
