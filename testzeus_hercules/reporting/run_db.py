"""Run history database: durable per-run records of testcases, steps, artifacts and healing events.

Stores the results of each Hercules run (the summary dict produced by
``__main__.sequential_process`` plus per-testcase data from
``reporting.bundle.parse_junit_testcases``) in a SQLite database via SQLAlchemy 2.0,
so trends, flaky-case detection and per-case evidence can be queried later.

All timestamps are UTC and stored naive; nested dicts are stored as ``json.dumps``
strings and decoded back into dicts on read.
"""

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import (
    ForeignKey,
    String,
    Text,
    create_engine,
    delete,
    event,
    func,
    select,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from sqlalchemy.schema import CreateColumn
from testzeus_hercules.reporting.bundle import (
    PROP_INTERNAL_LOGS,
    PROP_NETWORK_LOGS,
    PROP_PLANNER_THOUGHTS,
    PROP_PROOFS_BASE,
    PROP_SCREENSHOT,
    PROP_VIDEO,
    parse_junit_testcases,
    resolve_bundle_dir_names,
    sanitize_name,
)
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.redact import redact_url

DEFAULT_DB_FILENAME = "hercules_history.db"
FALLBACK_PROJECT_SOURCE_ROOT = "./opt"
SQLITE_CONNECT_TIMEOUT_S = 30
SQLITE_BUSY_TIMEOUT_MS = 30000

RUN_STATUS_RUNNING = "running"
RUN_STATUS_PASSED = "passed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_ERROR = "error"

FINAL_RESPONSE_PREFIX = "Final Response:"


def _utcnow() -> datetime:
    """Naive UTC timestamp (SQLite drops tzinfo anyway; keep everything naive UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_number(value) -> float | None:
    """Best-effort float conversion; returns None when the value is not numeric."""
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_int(value) -> int | None:
    number = _parse_number(value)
    return int(number) if number is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _abspath(value) -> str:
    """Absolute form of a stored path so DB references survive CWD changes."""
    return os.path.abspath(str(value))


def _decode_json(raw: str | None):
    """Decode a json.dumps string back into a Python object; returns the raw string when not JSON."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _final_response_from_system_out(system_out: str | None) -> str | None:
    """Extract the "Final Response: ..." line recorded by junit_helper into system-out."""
    if not system_out:
        return None
    for line in system_out.splitlines():
        stripped = line.strip()
        if stripped.startswith(FINAL_RESPONSE_PREFIX):
            return stripped[len(FINAL_RESPONSE_PREFIX) :].strip()
    return None


def _cost_from_properties(props: dict) -> float | None:
    """Cost is stored by junit_helper under ``total_cost`` or ``<prefix>.total_cost`` properties."""
    if "total_cost" in props:
        return _parse_number(props["total_cost"])
    matches = [value for key, value in props.items() if key.endswith(".total_cost")]
    return _parse_number(matches[-1]) if matches else None


def _artifact_kind_for_property(prop_name: str) -> str | None:
    """Map a JUnit evidence property name to an artifact kind; None for non-evidence properties."""
    if prop_name == PROP_VIDEO:
        return "video"
    if prop_name == PROP_SCREENSHOT:
        return "screenshot"
    if prop_name == PROP_NETWORK_LOGS:
        return "network"
    if prop_name in (PROP_INTERNAL_LOGS, PROP_PLANNER_THOUGHTS):
        return "log"
    if prop_name == PROP_PROOFS_BASE:
        return "bundle"
    return None


class Base(DeclarativeBase):
    """Shared declarative base for the run-history schema."""


class Run(Base):
    """One Hercules execution (a single sequential_process invocation)."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    started_at: Mapped[datetime] = mapped_column(default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(String(16), default=RUN_STATUS_RUNNING)
    total: Mapped[int] = mapped_column(default=0)
    passed: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    errors: Mapped[int] = mapped_column(default=0)
    duration_s: Mapped[float | None] = mapped_column(default=None)
    total_cost: Mapped[float | None] = mapped_column(default=None)
    total_tokens: Mapped[int | None] = mapped_column(default=None)
    junit_xml: Mapped[str | None] = mapped_column(Text, default=None)
    html: Mapped[str | None] = mapped_column(Text, default=None)
    report_md: Mapped[str | None] = mapped_column(Text, default=None)
    feature_file: Mapped[str | None] = mapped_column(Text, default=None)
    meta_json: Mapped[str | None] = mapped_column(Text, default=None)

    cases: Mapped[list["TestCaseRow"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class TestCaseRow(Base):
    """One testcase within a run, as parsed from the merged JUnit XML."""

    __tablename__ = "test_cases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    case_key: Mapped[str] = mapped_column(String, index=True)
    feature: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String(16), default=RUN_STATUS_PASSED)
    duration_s: Mapped[float | None] = mapped_column(default=None)
    failure_message: Mapped[str | None] = mapped_column(Text, default=None)
    final_response: Mapped[str | None] = mapped_column(Text, default=None)
    cost: Mapped[float | None] = mapped_column(default=None)
    bundle_dir: Mapped[str | None] = mapped_column(Text, default=None)

    run: Mapped[Run] = relationship(back_populates="cases")
    steps: Mapped[list["StepRow"]] = relationship(back_populates="test_case", cascade="all, delete-orphan")
    artifacts: Mapped[list["ArtifactRow"]] = relationship(back_populates="test_case", cascade="all, delete-orphan")
    healing_events: Mapped[list["HealingEventRow"]] = relationship(back_populates="test_case", cascade="all, delete-orphan")


class StepRow(Base):
    """Forward-looking per-step record (populated via the recording API only)."""

    __tablename__ = "steps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(default=0)
    step_type: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str | None] = mapped_column(String(16), default=None)
    duration_s: Mapped[float | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    test_case: Mapped[TestCaseRow] = relationship(back_populates="steps")


class ArtifactRow(Base):
    """An evidence artifact (screenshot, video, log, ...) for a case or the whole run.

    ``test_case_id`` is NULL for run-level artifacts; ``run_id`` is always set so
    run-level rows can still be attributed to their run.
    """

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True, default=None)
    test_case_id: Mapped[int | None] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), index=True, default=None)
    kind: Mapped[str] = mapped_column(String(16), default="other")
    path: Mapped[str] = mapped_column(Text)

    test_case: Mapped[TestCaseRow | None] = relationship(back_populates="artifacts")


class HealingEventRow(Base):
    """A healing-related event (selector store hit, heal proposal, classification)."""

    __tablename__ = "healing_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True, default=None)
    test_case_id: Mapped[int | None] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), index=True, default=None)
    occurred_at: Mapped[datetime] = mapped_column(default=_utcnow)
    kind: Mapped[str] = mapped_column(String(32))
    canonical_name: Mapped[str | None] = mapped_column(String, default=None)
    detail: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str | None] = mapped_column(String(16), default=None)

    test_case: Mapped[TestCaseRow | None] = relationship(back_populates="healing_events")


def _run_to_dict(run: Run) -> dict:
    return {
        "run_id": run.run_id,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "status": run.status,
        "total": run.total,
        "passed": run.passed,
        "failed": run.failed,
        "errors": run.errors,
        "duration_s": run.duration_s,
        "total_cost": run.total_cost,
        "total_tokens": run.total_tokens,
        "junit_xml": run.junit_xml,
        "html": run.html,
        "report_md": run.report_md,
        "feature_file": run.feature_file,
        "meta": _decode_json(run.meta_json),
    }


def _case_to_dict(case: TestCaseRow) -> dict:
    return {
        "id": case.id,
        "run_id": case.run.run_id if case.run is not None else None,
        "case_key": case.case_key,
        "feature": case.feature,
        "status": case.status,
        "duration_s": case.duration_s,
        "failure_message": case.failure_message,
        "final_response": case.final_response,
        "cost": case.cost,
        "bundle_dir": case.bundle_dir,
    }


def _step_to_dict(step: StepRow) -> dict:
    return {
        "idx": step.idx,
        "step_type": step.step_type,
        "description": step.description,
        "status": step.status,
        "duration_s": step.duration_s,
        "error": step.error,
    }


def _artifact_to_dict(artifact: ArtifactRow) -> dict:
    return {
        "id": artifact.id,
        "kind": artifact.kind,
        "path": artifact.path,
        "test_case_id": artifact.test_case_id,
    }


def _healing_event_to_dict(event: HealingEventRow) -> dict:
    return {
        "id": event.id,
        "occurred_at": _iso(event.occurred_at),
        "kind": event.kind,
        "canonical_name": event.canonical_name,
        "detail": event.detail,
        "status": event.status,
    }


def _pass_rate(passed: int, total: int) -> float | None:
    """Pass rate clamped to [0, 1] — inconsistent upstream counters must not yield > 1.0."""
    if not total:
        return None
    return min(1.0, max(0.0, passed / total))


def _sqlite_configure(dbapi_connection, _connection_record) -> None:
    """Harden SQLite for concurrent writers: WAL journaling plus a busy timeout.

    Parallel scenario subprocesses write to the same history DB file; without
    WAL/busy_timeout their commits collide with "database is locked" errors.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()


class RunHistory:
    """SQLite-backed store of run history, safe for concurrent use.

    Within one process the engine allows cross-thread connections; across
    processes (parallel scenario subprocesses) WAL journaling plus a 30s
    busy timeout keep concurrent writers from failing with "database is locked".
    """

    def __init__(self, db_url: str | None = None):
        if db_url is None:
            db_url = self._default_db_url()
        self.db_url = db_url
        engine_kwargs: dict = {}
        if db_url.startswith("sqlite"):
            engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": SQLITE_CONNECT_TIMEOUT_S}
        self.engine = create_engine(db_url, **engine_kwargs)
        if db_url.startswith("sqlite"):
            event.listen(self.engine, "connect", _sqlite_configure)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self._migrate_missing_columns()
        # Per-junit cache of the collision-free bundle-dir name mapping, so the
        # per-case resolution below does not re-parse the XML for every case.
        self._bundle_dir_name_cache: dict[str, dict[str, str]] = {}

    @staticmethod
    def _default_db_url() -> str:
        """Default DB location: ``$HERCULES_HISTORY_DB`` when set, else ``<PROJECT_SOURCE_ROOT>/hercules_history.db``."""
        env_url = os.getenv("HERCULES_HISTORY_DB", "").strip()
        if env_url:
            # Accept a full SQLAlchemy URL or a bare filesystem path (mainly for tests).
            return env_url if "://" in env_url else f"sqlite:///{env_url}"
        try:
            from testzeus_hercules.config import (
                get_global_conf,  # lazy: keep heavy config import out of module import
            )

            project_root = get_global_conf().get_project_source_root()
        except Exception as exc:
            logger.warning("Could not read PROJECT_SOURCE_ROOT from config (%s); falling back to %s", exc, FALLBACK_PROJECT_SOURCE_ROOT)
            project_root = FALLBACK_PROJECT_SOURCE_ROOT
        return f"sqlite:///{os.path.join(project_root, DEFAULT_DB_FILENAME)}"

    def _migrate_missing_columns(self) -> None:
        """Additive schema migration: add model columns missing from an older on-disk schema.

        ``create_all`` only creates missing tables, so an older ``runs`` table without
        newer columns (e.g. ``total_tokens``) would pass construction and then fail on
        every insert, silently losing history. Missing columns are added via SQLite's
        ``ALTER TABLE ... ADD COLUMN``; existing rows read NULL for the new column and
        the ORM's Python-side defaults cover new inserts. Only sqlite databases are
        migrated (PRAGMA-based introspection).
        """
        if not self.db_url.startswith("sqlite"):
            return
        with self.engine.begin() as connection:
            for table in Base.metadata.sorted_tables:
                existing = {row[1] for row in connection.execute(text(f"PRAGMA table_info({table.name})")).fetchall()}
                for column in table.columns:
                    if column.name in existing:
                        continue
                    column_ddl = str(CreateColumn(column).compile(dialect=self.engine.dialect))
                    if not column.primary_key and not column.nullable:
                        # sqlite's ALTER ADD COLUMN rejects NOT NULL without a default;
                        # the ORM's Python-side defaults cover new inserts, so the added
                        # column tolerates NULLs for pre-existing rows instead.
                        column_ddl = column_ddl.replace("NOT NULL", "")
                    logger.info("Migrating history schema: adding %s.%s", table.name, column.name)
                    try:
                        connection.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column_ddl}"))
                    except SQLAlchemyError as exc:
                        # Parallel processes may have added the same column concurrently.
                        if "duplicate column name" not in str(exc).lower():
                            raise
                        logger.info("Column %s.%s was added concurrently; continuing", table.name, column.name)

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _get_run_row(session: Session, run_id: str) -> Run:
        run = session.scalar(select(Run).where(Run.run_id == run_id))
        if run is None:
            raise ValueError(f"Unknown run_id: {run_id!r}")
        return run

    @staticmethod
    def _get_case_row(session: Session, run_pk: int, case_key: str) -> TestCaseRow | None:
        return session.scalar(select(TestCaseRow).where(TestCaseRow.run_id == run_pk, TestCaseRow.case_key == case_key))

    def has_run(self, run_id: str) -> bool:
        """True when a run with this run_id is already recorded."""
        with self._session() as session:
            return session.scalar(select(Run.id).where(Run.run_id == run_id)) is not None

    def delete_run(self, run_id: str) -> bool:
        """Delete a run and all dependent rows (cases, steps, artifacts, healing events).

        Returns True when a row was removed. The DB-level ``ON DELETE CASCADE`` is not
        relied on (SQLite only enforces it with ``PRAGMA foreign_keys=ON``): case rows
        and their children go through the ORM relationship cascade, while run-level
        artifacts and healing events (``test_case_id`` IS NULL, no relationship from
        ``Run``) are deleted explicitly.
        """
        with self._session() as session:
            run = session.scalar(select(Run).where(Run.run_id == run_id))
            if run is None:
                return False
            session.execute(delete(ArtifactRow).where(ArtifactRow.run_id == run.id))
            session.execute(delete(HealingEventRow).where(HealingEventRow.run_id == run.id))
            session.delete(run)  # ORM cascade removes cases, steps and case-level children
        return True

    # -- writing -----------------------------------------------------------

    def start_run(self, run_id: str | None = None, meta: dict | None = None) -> str:
        """Create a new run row and return its run_id (generated when not given)."""
        with self._session() as session:
            if run_id is None:
                run_id = self._generate_run_id(session)
            elif session.scalar(select(Run.id).where(Run.run_id == run_id)) is not None:
                run_id = self._generate_run_id(session, base=run_id)
            run = Run(run_id=run_id, started_at=_utcnow(), status=RUN_STATUS_RUNNING, meta_json=json.dumps(meta, sort_keys=True) if meta else None)
            session.add(run)
        return run_id

    @staticmethod
    def _generate_run_id(session: Session, base: str | None = None) -> str:
        if not base:
            base = datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")
        candidate = base
        suffix = 0
        while session.scalar(select(Run.id).where(Run.run_id == candidate)) is not None:
            suffix += 1
            candidate = f"{base}_{suffix}"
        return candidate

    def _set_run_outputs(self, run_id: str, summary: dict) -> None:
        """Record the output-file paths early so bundle_dir derivation works while cases are recorded."""
        with self._session() as session:
            run = self._get_run_row(session, run_id)
            for key in ("junit_xml", "html", "report_md", "feature_file"):
                value = summary.get(key)
                if value:
                    setattr(run, key, _abspath(value))
            session.add(run)

    def finish_run(self, run_id: str, summary: dict | None = None, status: str | None = None, duration_s: float | None = None) -> None:
        """Finalize a run: counters, output paths, status and cost/token totals from the summary."""
        summary = summary or {}
        with self._session() as session:
            run = self._get_run_row(session, run_id)
            run.finished_at = _utcnow()

            for key in ("total", "passed", "failed", "errors"):
                value = _parse_int(summary.get(key))
                if value is not None:
                    setattr(run, key, value)

            for key in ("junit_xml", "html", "report_md", "feature_file"):
                value = summary.get(key)
                if value:
                    setattr(run, key, _abspath(value))

            if duration_s is None:
                duration_s = _parse_number(summary.get("duration_s"))
            if duration_s is not None:
                run.duration_s = float(duration_s)
            elif run.started_at is not None:
                run.duration_s = (run.finished_at - run.started_at).total_seconds()

            # total_cost / total_tokens: prefer summary["meta"], fall back to flat summary keys.
            summary_meta = summary.get("meta") if isinstance(summary.get("meta"), dict) else {}
            cost = _parse_number(summary_meta.get("total_cost", summary.get("total_cost")))
            tokens = _parse_int(summary_meta.get("total_tokens", summary.get("total_tokens")))
            if cost is not None:
                run.total_cost = cost
            if tokens is not None:
                run.total_tokens = tokens

            existing_meta = _decode_json(run.meta_json)
            meta = dict(existing_meta) if isinstance(existing_meta, dict) else {}
            meta.update(summary_meta)
            run.meta_json = json.dumps(meta, sort_keys=True) if meta else run.meta_json

            if status:
                run.status = status
            elif summary.get("status"):
                run.status = str(summary["status"])
            elif (run.errors or 0) > 0:
                run.status = RUN_STATUS_ERROR
            elif (run.failed or 0) > 0:
                run.status = RUN_STATUS_FAILED
            else:
                run.status = RUN_STATUS_PASSED
            session.add(run)

    def record_case(self, run_id: str, case: dict) -> int:
        """Record one testcase dict (as returned by parse_junit_testcases); returns the row id."""
        return self.record_cases(run_id, [case])[0]

    def record_cases(self, run_id: str, cases: list[dict]) -> list[int]:
        """Record several testcase dicts in ONE session; returns the row ids in order.

        Same row and artifact construction as the per-case path, but with a single
        commit for the whole batch instead of one session/commit per testcase.
        """
        if not cases:
            return []
        with self._session() as session:
            run = self._get_run_row(session, run_id)
            row_ids: list[int] = []
            for case in cases:
                props = case.get("properties") or {}
                case_key = case.get("name") or ""
                row = TestCaseRow(
                    run_id=run.id,
                    case_key=case_key,
                    feature=case.get("classname") or "",
                    status=case.get("status") or RUN_STATUS_PASSED,
                    duration_s=_parse_number(case.get("time")),
                    failure_message=case.get("failure_message"),
                    final_response=_final_response_from_system_out(case.get("system_out")),
                    cost=_cost_from_properties(props),
                    bundle_dir=self._bundle_dir_for_case(run, case_key),
                )
                session.add(row)
                session.flush()  # assign row.id before adding artifacts
                for prop_name, prop_value in props.items():
                    kind = _artifact_kind_for_property(prop_name)
                    if kind and prop_value:
                        session.add(ArtifactRow(run_id=run.id, test_case_id=row.id, kind=kind, path=_abspath(prop_value)))
                row_ids.append(row.id)
            return row_ids

    def _bundle_dir_for_case(self, run: Run, case_key: str) -> str | None:
        """Return the case's bundle dir under ``<junit_dir>/bundles`` when it exists.

        Bundle dir names are resolved with the same collision-free mapping the
        bundler used (hash suffix when two failing testcase names sanitize to
        the same dir), so a case whose name collides with another failing case
        attaches to its OWN evidence instead of the other case's bundle.
        """
        junit_xml = run.junit_xml
        if not junit_xml or not case_key:
            return None
        bundles_root = os.path.join(os.path.dirname(os.path.abspath(junit_xml)), "bundles")
        candidates = [sanitize_name(case_key), case_key]
        try:
            mapping = self._bundle_dir_names_for_junit(junit_xml)
            mapped = mapping.get(case_key)
            if mapped:
                candidates.insert(0, mapped)
        except Exception as exc:
            logger.debug("Could not resolve bundle-dir mapping for %s: %s", junit_xml, exc)
        for candidate in candidates:
            bundle_dir = os.path.join(bundles_root, candidate)
            if os.path.isdir(bundle_dir):
                return bundle_dir
        return None

    def _bundle_dir_names_for_junit(self, junit_xml: str) -> dict[str, str]:
        """Collision-free ``{testcase_name: bundle_dir_name}`` mapping, cached per junit path."""
        cached = self._bundle_dir_name_cache.get(junit_xml)
        if cached is not None:
            return cached
        testcases = parse_junit_testcases(junit_xml)
        mapping = resolve_bundle_dir_names([case.get("name") or "unknown_testcase" for case in testcases if case.get("status") != "passed"])
        self._bundle_dir_name_cache[junit_xml] = mapping
        return mapping

    def record_steps(self, run_id: str, case_key: str, steps: list[dict]) -> None:
        """Record per-step rows for a case; each step dict has idx/type/description/status/duration_s/error."""
        with self._session() as session:
            run = self._get_run_row(session, run_id)
            case = self._get_case_row(session, run.id, case_key)
            if case is None:
                raise ValueError(f"Unknown case_key {case_key!r} in run {run_id!r}")
            for step in steps:
                session.add(
                    StepRow(
                        test_case_id=case.id,
                        idx=int(step.get("idx") or 0),
                        step_type=str(step.get("type") or ""),
                        description=str(step.get("description") or ""),
                        status=step.get("status"),
                        duration_s=_parse_number(step.get("duration_s")),
                        error=step.get("error"),
                    )
                )

    def record_artifact(self, run_id: str, case_key: str | None, kind: str, path: str) -> None:
        """Record an artifact for one case, or for the whole run when case_key is None."""
        with self._session() as session:
            run = self._get_run_row(session, run_id)
            case = self._get_case_row(session, run.id, case_key) if case_key else None
            if case_key and case is None:
                raise ValueError(f"Unknown case_key {case_key!r} in run {run_id!r}")
            session.add(ArtifactRow(run_id=run.id, test_case_id=case.id if case else None, kind=kind, path=_abspath(path)))

    def record_healing_event(self, run_id: str, case_key: str | None, kind: str, canonical_name: str | None = None, detail: str | None = None, status: str | None = None) -> None:
        """Record a healing event for one case, or for the whole run when case_key is None."""
        with self._session() as session:
            run = self._get_run_row(session, run_id)
            case = self._get_case_row(session, run.id, case_key) if case_key else None
            if case_key and case is None:
                raise ValueError(f"Unknown case_key {case_key!r} in run {run_id!r}")
            session.add(HealingEventRow(run_id=run.id, test_case_id=case.id if case else None, kind=kind, canonical_name=canonical_name, detail=detail, status=status))

    # -- reading -----------------------------------------------------------

    def get_run(self, run_id: str) -> dict:
        """Full JSON-ready view of one run with its cases, artifacts and healing events."""
        with self._session() as session:
            run = self._get_run_row(session, run_id)
            result = _run_to_dict(run)
            cases = session.scalars(select(TestCaseRow).where(TestCaseRow.run_id == run.id).order_by(TestCaseRow.id)).all()
            result["cases"] = []
            for case in cases:
                case_dict = _case_to_dict(case)
                case_dict["artifacts"] = [_artifact_to_dict(artifact) for artifact in session.scalars(select(ArtifactRow).where(ArtifactRow.test_case_id == case.id).order_by(ArtifactRow.id)).all()]
                case_dict["healing_events"] = [
                    _healing_event_to_dict(event) for event in session.scalars(select(HealingEventRow).where(HealingEventRow.test_case_id == case.id).order_by(HealingEventRow.id)).all()
                ]
                result["cases"].append(case_dict)
            result["artifacts"] = [
                _artifact_to_dict(artifact) for artifact in session.scalars(select(ArtifactRow).where(ArtifactRow.run_id == run.id, ArtifactRow.test_case_id.is_(None)).order_by(ArtifactRow.id)).all()
            ]
            result["healing_events"] = [
                _healing_event_to_dict(event)
                for event in session.scalars(select(HealingEventRow).where(HealingEventRow.run_id == run.id, HealingEventRow.test_case_id.is_(None)).order_by(HealingEventRow.id)).all()
            ]
            return result

    def list_runs(self, limit: int = 20) -> list[dict]:
        """Newest-first run summaries with both stored counters and actual per-case counts."""
        with self._session() as session:
            runs = session.scalars(select(Run).order_by(Run.started_at.desc(), Run.id.desc()).limit(limit)).all()
            results: list[dict] = []
            for run in runs:
                status_counts: dict[str, int] = {}
                for row_status, count in session.execute(select(TestCaseRow.status, func.count()).where(TestCaseRow.run_id == run.id).group_by(TestCaseRow.status)).all():
                    status_counts[row_status] = count
                case_total = sum(status_counts.values())
                results.append(
                    {
                        "run_id": run.run_id,
                        "started_at": _iso(run.started_at),
                        "finished_at": _iso(run.finished_at),
                        "status": run.status,
                        "duration_s": run.duration_s,
                        # Plain column on Run; lets callers (dashboard) read cost without a per-row get_run.
                        "total_cost": run.total_cost,
                        "total": run.total,
                        "passed": run.passed,
                        "failed": run.failed,
                        "errors": run.errors,
                        "case_count": case_total,
                        "case_passed": status_counts.get(RUN_STATUS_PASSED, 0),
                        "case_failed": status_counts.get(RUN_STATUS_FAILED, 0),
                        "case_errors": status_counts.get(RUN_STATUS_ERROR, 0),
                        "pass_rate": _pass_rate(status_counts.get(RUN_STATUS_PASSED, 0), case_total) if case_total else _pass_rate(run.passed, run.total),
                    }
                )
            return results

    def get_case_detail(self, run_id: str, case_key: str) -> dict:
        """JSON-ready view of one case with its steps, artifacts and healing events."""
        with self._session() as session:
            run = self._get_run_row(session, run_id)
            case = self._get_case_row(session, run.id, case_key)
            if case is None:
                raise ValueError(f"Unknown case_key {case_key!r} in run {run_id!r}")
            detail = _case_to_dict(case)
            detail["steps"] = [_step_to_dict(step) for step in session.scalars(select(StepRow).where(StepRow.test_case_id == case.id).order_by(StepRow.idx)).all()]
            detail["artifacts"] = [_artifact_to_dict(artifact) for artifact in session.scalars(select(ArtifactRow).where(ArtifactRow.test_case_id == case.id).order_by(ArtifactRow.id)).all()]
            detail["healing_events"] = [
                _healing_event_to_dict(event) for event in session.scalars(select(HealingEventRow).where(HealingEventRow.test_case_id == case.id).order_by(HealingEventRow.id)).all()
            ]
            return detail

    def get_trend(self, limit: int = 30) -> dict:
        """Per-run pass rates plus aggregates (average pass rate, flakiest cases) over the last runs."""
        runs = self.list_runs(limit=limit)
        trend_runs = [
            {
                "run_id": entry["run_id"],
                "started_at": entry["started_at"],
                "passed": entry["case_passed"],
                "failed": entry["case_failed"],
                "errors": entry["case_errors"],
                "pass_rate": entry["pass_rate"],
            }
            for entry in runs
        ]
        rates = [entry["pass_rate"] for entry in trend_runs if entry["pass_rate"] is not None]

        per_case: dict[str, dict] = {}
        if runs:
            run_ids = [entry["run_id"] for entry in runs]
            with self._session() as session:
                rows = session.execute(
                    select(TestCaseRow.case_key, TestCaseRow.status, func.count())
                    .join(Run, Run.id == TestCaseRow.run_id)
                    .where(Run.run_id.in_(run_ids))
                    .group_by(TestCaseRow.case_key, TestCaseRow.status)
                ).all()
            for case_key, row_status, count in rows:
                entry = per_case.setdefault(case_key, {"case_key": case_key, "runs": 0, "passed": 0, "failed": 0, "errors": 0, "statuses": set()})
                entry["runs"] += count
                entry[row_status] = entry.get(row_status, 0) + count
                entry["statuses"].add(row_status)

        flaky = [entry for entry in per_case.values() if len(entry["statuses"]) > 1]
        flaky.sort(key=lambda entry: (-len(entry["statuses"]), -entry["runs"]))
        for entry in flaky:
            entry.pop("statuses")

        return {
            "runs": trend_runs,
            "aggregate": {"avg_pass_rate": (sum(rates) / len(rates)) if rates else None, "flakiest_cases": flaky[:5]},
        }


_default_history: RunHistory | None = None
_default_history_lock = threading.Lock()


def reset_default_history() -> None:
    """Drop the cached default RunHistory so the next call rebinds the DB (mainly for tests).

    The dropped instance's engine is disposed so its pooled SQLite connections
    are closed instead of leaking. Never raises.
    """
    global _default_history
    with _default_history_lock:
        history, _default_history = _default_history, None
    if history is not None:
        try:
            history.engine.dispose()
        except Exception as exc:  # noqa: BLE001 - disposal is best-effort
            logger.warning("Could not dispose default history engine: %s", exc)


def get_default_history() -> RunHistory:
    """Process-wide cached RunHistory bound to the default DB location (created lazily, thread-safely)."""
    global _default_history
    if _default_history is None:
        with _default_history_lock:
            if _default_history is None:  # re-check: another thread may have won the race
                _default_history = _create_default_history()
    return _default_history


def _sqlite_path_from_url(url: str) -> str | None:
    """Filesystem path behind a ``sqlite:///`` URL; None for other dialects."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    return url[len(prefix) :]


def _recover_default_db_location(db_path: str, exc: Exception) -> bool:
    """Best-effort repair of an unusable default DB location; True when a retry is worthwhile.

    Recovery paths: a missing parent directory is created, and a non-database file
    (e.g. truncated/corrupt) is moved aside as ``<name>.corrupt-<timestamp>`` so a
    fresh DB can take its place without losing the old bytes.
    """
    parent = os.path.dirname(os.path.abspath(db_path))
    if not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
            logger.warning("Created missing history DB directory %s", parent)
            return True
        except OSError as mkdir_exc:
            logger.error("Could not create history DB directory %s: %s", parent, mkdir_exc)
    if os.path.isfile(db_path) and "file is not a database" in str(exc).lower():
        quarantine_path = f"{db_path}.corrupt-{_utcnow().strftime('%Y%m%d_%H%M%S%f')}"
        try:
            os.replace(db_path, quarantine_path)
            logger.warning("History DB %s is not a SQLite database; moved it to %s and started a fresh one", db_path, quarantine_path)
            return True
        except OSError as rename_exc:
            logger.error("Could not move corrupt history DB %s aside: %s", db_path, rename_exc)
    return False


def _create_default_history() -> RunHistory:
    """RunHistory bound to the default location, with one recovery attempt on failure.

    Leniency lives here only — an explicit ``RunHistory(db_url)`` stays strict so
    tests and the dashboard keep failing fast on bad locations. When recovery is
    impossible, the error is logged clearly and re-raised (callers degrade).
    """
    url = RunHistory._default_db_url()
    try:
        return RunHistory(db_url=url)
    except Exception as exc:  # noqa: BLE001 - recovery below decides what is recoverable
        db_path = _sqlite_path_from_url(url)
        if db_path is None or not _recover_default_db_location(db_path, exc):
            logger.error("Default history DB %s is unusable: %s", redact_url(url), exc)
            raise
        try:
            return RunHistory(db_url=url)
        except Exception as retry_exc:  # noqa: BLE001 - callers already degrade without history
            logger.error("Default history DB %s still unusable after recovery attempt: %s", redact_url(url), retry_exc)
            raise


def record_run_summary(summary: dict, run_id: str | None = None, db: RunHistory | None = None) -> str:
    """Record a completed run summary (shape of ``__main__.sequential_process``) into history.

    Starts the run (run_id defaults to the junit output dir name), records every testcase
    found in ``summary["junit_xml"]``, adds run-level artifacts for the report and failure
    bundles, then finalizes the run. Missing files are logged, never raised.
    """
    if db is None:
        db = get_default_history()

    junit_xml = summary.get("junit_xml") or ""
    if run_id is None and junit_xml:
        run_id = os.path.basename(os.path.dirname(os.path.abspath(junit_xml))) or None

    if run_id is not None and db.has_run(run_id):
        # Idempotent re-record: replace the previous run instead of letting start_run
        # rename to <id>_1, which would create a phantom double-counted duplicate.
        logger.info("Run %s already recorded in history; replacing the previous record.", run_id)
        db.delete_run(run_id)
    run_id = db.start_run(run_id=run_id, meta={"feature_file": summary.get("feature_file")} if summary.get("feature_file") else None)
    db._set_run_outputs(run_id, summary)

    if junit_xml and not os.path.isfile(junit_xml):
        logger.warning("junit xml %s not found; recording run %s without testcases", junit_xml, run_id)
    elif junit_xml:
        try:
            cases = parse_junit_testcases(junit_xml)
        except Exception as exc:
            logger.warning("Could not parse junit xml %s: %s", junit_xml, exc)
            cases = []
        if cases:
            db.record_cases(run_id, cases)

    for bundle_path in summary.get("bundles") or []:
        db.record_artifact(run_id, None, "bundle", str(bundle_path))
    if summary.get("report_md"):
        db.record_artifact(run_id, None, "report", str(summary["report_md"]))

    status = summary.get("status")
    if not status:
        failed = summary.get("failed") or 0
        errors = summary.get("errors") or 0
        status = RUN_STATUS_ERROR if errors else (RUN_STATUS_FAILED if failed else RUN_STATUS_PASSED)
    db.finish_run(run_id, summary=summary, status=str(status))
    return run_id
