"""Read-only SQL for SQL steps: one ``select`` tool that never raises.

Only a single ``SELECT`` (or ``WITH ... SELECT``) statement runs. Before anything
connects, string literals, quoted identifiers and comments are blanked and the rest
is scanned for data-modifying keywords (``WITH d AS (DELETE ...)``, ``SELECT ... INTO``)
and for a second statement; the scan is conservative and refuses rather than lets
through. As defence in depth the session is made read-only with a statement timeout
where the dialect supports it, and SQLite files are opened read-only (a relative
path is taken from the project folder, and a missing file is an error rather than
a new empty database). At most :data:`MAX_ROWS` rows are kept. Driver errors echo
connection strings: every message is passed through :func:`redact_text`.

Async drivers (``+asyncpg``, ``+aiosqlite``...) run on SQLAlchemy's async engine;
sync drivers run in a worker thread. ``sqlite+aiosqlite`` falls back to the
standard library driver when ``aiosqlite`` is not installed.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import decimal
import importlib.util
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import create_async_engine

from testzeus_hercules.act.redact import redact_text

logger = logging.getLogger(__name__)

MAX_ROWS = 1000
MAX_STATEMENT_SECONDS = 30
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|UPSERT|TRUNCATE|ALTER|DROP|CREATE|GRANT|REVOKE|INTO|COPY|CALL|EXEC|EXECUTE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|LOAD)\b",
    re.IGNORECASE,
)
# Functions that read or write files, reach other servers, run commands, change state or stall the database, even
# inside a SELECT. Matched as calls (``name(``), with or without identifier quotes.
_FORBIDDEN_FUNCTIONS = re.compile(
    r"""[\"`\[]?\b(load_file|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|pg_ls_logdir|pg_ls_waldir|lo_import|lo_export|lo_unlink|lo_from_bytea|lo_put"""
    r"""|dblink\w*|pg_sleep\w*|sleep|benchmark|waitfor|xp_\w+|sp_\w+|openrowset|opendatasource|openquery|utl_\w+|dbms_\w+|sys_exec|sys_eval"""
    r"""|nextval|setval|set_config|pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile|query_to_xml\w*|readfile|writefile|edit|fts3_tokenizer|load_extension)\b[\"`\]]?\s*\(""",
    re.IGNORECASE,
)
_OUTFILE = re.compile(r"\binto\s+(?:outfile|dumpfile)\b", re.IGNORECASE)
_STRINGS_AND_COMMENTS = re.compile(r"'(?:[^']|'')*'|--[^\n]*|/\*.*?(?:\*/|$)", re.DOTALL)
_LITERALS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`|\[[^\]]*\]|--[^\n]*|/\*.*?(?:\*/|$)", re.DOTALL)
_ASYNC_FALLBACK = {"sqlite+aiosqlite": ("aiosqlite", "sqlite")}


class QueryRefused(ValueError):
    """The query is not a single read-only SELECT; the message names the reason."""


@dataclass(frozen=True)
class SqlResult:
    """Rows as JSON-safe dicts, or ``error`` (redacted)."""

    rows: tuple[dict[str, Any], ...] = ()
    columns: tuple[str, ...] = ()
    truncated: bool = False
    duration_ms: int = 0
    dialect: str = ""
    error: str | None = None
    error_kind: str | None = None  # refused (not a read-only SELECT) or database
    statements: tuple[str, ...] = field(default=(), repr=False)

    @property
    def ok(self) -> bool:
        return self.error is None


def _blank(match: re.Match[str]) -> str:
    return " " * len(match.group(0))


def check_query(query: str) -> str:
    """The query, stripped, when it is one read-only SELECT; :class:`QueryRefused` otherwise."""
    stripped = query.strip().rstrip(";").strip()
    code = _LITERALS.sub(_blank, stripped)
    head = code.strip().lower()
    if not (head.startswith("select") or head.startswith("with")):
        raise QueryRefused("Only SELECT queries are allowed: the query must start with SELECT or WITH.")
    if ";" in code:
        raise QueryRefused("Only SELECT queries are allowed: run one statement per step (found ';' before the end).")
    calls = _STRINGS_AND_COMMENTS.sub(_blank, stripped)  # quoted identifiers kept: "pg_read_file"(...) is still a call
    function = _FORBIDDEN_FUNCTIONS.search(calls)
    if function:
        raise QueryRefused(f"Only plain SELECT queries are allowed: the function '{function.group(1).lower()}' reads or writes files, reaches other servers or changes state.")
    if _OUTFILE.search(code):
        raise QueryRefused("Only SELECT queries are allowed: INTO OUTFILE / DUMPFILE writes a file.")
    forbidden = _FORBIDDEN.search(code)
    if forbidden:
        where = "in the WITH clause" if head.startswith("with") and forbidden.start() < _top_level_select(code) else "in the query"
        raise QueryRefused(f"Only SELECT queries are allowed: data-modifying keyword '{forbidden.group(0).upper()}' found {where}.")
    return stripped


def _top_level_select(code: str) -> int:
    depth = 0
    for match in re.finditer(r"\bselect\b|[()]", code, re.IGNORECASE):
        token = match.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            return match.start()
    return len(code)


def safety_statements(dialect: str) -> list[str]:
    """Per-dialect read-only and statement-timeout statements, in the order they run (best effort)."""
    if dialect == "postgresql":
        return ["SET TRANSACTION READ ONLY", "SET default_transaction_read_only = on", f"SET statement_timeout = '{MAX_STATEMENT_SECONDS}s'"]
    if dialect in ("mysql", "mariadb"):
        return ["SET SESSION TRANSACTION READ ONLY", "SET TRANSACTION READ ONLY", f"SET SESSION max_execution_time = {MAX_STATEMENT_SECONDS * 1000}"]
    if dialect == "sqlite":
        return ["PRAGMA query_only = ON", f"PRAGMA busy_timeout = {MAX_STATEMENT_SECONDS * 1000}"]
    return []


def jsonable(value: Any) -> Any:
    """A database value as JSON: numbers and text unchanged, decimals and dates as text, bytes as base64."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) < 2**53 else str(value)
    if isinstance(value, (dt.date, dt.time, dt.datetime)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode()
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return str(value)


def prepare_url(connection: str, *, base_dir: Path | None) -> URL:
    """A SQLAlchemy URL; a SQLite file is opened read-only, relative to ``base_dir``, and must exist."""
    try:
        url = make_url(connection)
    except ArgumentError:
        raise ValueError("the connection string is not a valid SQLAlchemy URL (for example sqlite:///app.db or postgresql+asyncpg://user@host/db)") from None
    fallback = _ASYNC_FALLBACK.get(url.drivername)
    if fallback is not None and importlib.util.find_spec(fallback[0]) is None:
        url = url.set(drivername=fallback[1])
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:" or url.database.startswith("file:"):
        return url
    path = Path(url.database).expanduser()
    if not path.is_absolute():
        path = (base_dir or Path.cwd()) / path
    if not path.is_file():
        raise ValueError(f"SQLite database file not found: {path.name}")
    return url.set(database="file:" + quote(str(path.resolve()), safe="/:"), query={**dict(url.query), "mode": "ro", "uri": "true"})


def _is_async(url: URL) -> bool:
    return bool(getattr(url.get_dialect(), "is_async", False))


async def _apply_safety_async(connection: Any, dialect: str, ran: list[str]) -> None:
    for statement in safety_statements(dialect):
        try:
            await connection.execute(text(statement))
            ran.append(statement)
        except Exception as exc:
            logger.debug("read-only/timeout setting %r not applied for %s (%s)", statement, dialect, type(exc).__name__)
            try:
                await connection.rollback()
            except Exception:
                pass
            return


def _apply_safety_sync(connection: Any, dialect: str, ran: list[str]) -> None:
    for statement in safety_statements(dialect):
        try:
            connection.execute(text(statement))
            ran.append(statement)
        except Exception as exc:
            logger.debug("read-only/timeout setting %r not applied for %s (%s)", statement, dialect, type(exc).__name__)
            try:
                connection.rollback()
            except Exception:
                pass
            return


def _schema_statement(engine: Any, schema: str | None) -> str | None:
    if not schema:
        return None
    quoted = engine.dialect.identifier_preparer.quote(schema)
    if engine.dialect.name == "postgresql":
        return f"SET search_path TO {quoted}"
    if engine.dialect.name in ("mysql", "mariadb"):
        return f"USE {quoted}"
    return None


def _collect(result: Any, max_rows: int) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...], bool]:
    raw = result.fetchmany(max_rows + 1)
    columns = tuple(str(key) for key in result.keys()) if hasattr(result, "keys") else ()
    result.close()
    rows = tuple({str(key): jsonable(value) for key, value in row._mapping.items()} for row in raw[:max_rows])
    return rows, columns or (tuple(rows[0]) if rows else ()), len(raw) > max_rows


async def _run_async(engine: Any, query: str, params: Mapping[str, Any], schema: str | None, max_rows: int) -> tuple[Any, list[str]]:
    ran: list[str] = []
    try:
        async with engine.connect() as connection:
            await _apply_safety_async(connection, engine.dialect.name, ran)
            schema_sql = _schema_statement(engine, schema)
            if schema_sql:
                await connection.execute(text(schema_sql))
                ran.append(schema_sql)
            result = await connection.execute(text(query), dict(params))
            ran.append(query)
            return _collect(result, max_rows), ran
    finally:
        try:
            await engine.dispose()
        except Exception:
            pass


def _run_sync(engine: Any, query: str, params: Mapping[str, Any], schema: str | None, max_rows: int) -> tuple[Any, list[str]]:
    ran: list[str] = []
    try:
        with engine.connect() as connection:
            _apply_safety_sync(connection, engine.dialect.name, ran)
            schema_sql = _schema_statement(engine, schema)
            if schema_sql:
                connection.execute(text(schema_sql))
                ran.append(schema_sql)
            result = connection.execute(text(query), dict(params))
            ran.append(query)
            return _collect(result, max_rows), ran
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


EngineFactory = Callable[[URL], Any]


async def select(
    connection: str,
    query: str,
    *,
    params: Mapping[str, Any] | None = None,
    schema: str | None = None,
    base_dir: Path | None = None,
    max_rows: int = MAX_ROWS,
    secrets: tuple[str, ...] = (),
    make_engine: EngineFactory | None = None,
    make_async_engine: EngineFactory | None = None,
) -> SqlResult:
    """Run one read-only SELECT on ``connection`` and return its rows; never raises.

    ``make_engine``/``make_async_engine`` are seams for tests (a fake engine).
    """
    started = time.perf_counter()
    hidden = (connection, *secrets)
    try:
        sql = check_query(query)
    except QueryRefused as exc:
        return SqlResult(error=str(exc), error_kind="refused")
    try:
        url = prepare_url(connection, base_dir=base_dir)
        use_async = _is_async(url)
        if use_async:
            engine = (make_async_engine or (lambda target: create_async_engine(target)))(url)
            (rows, columns, truncated), ran = await _run_async(engine, sql, params or {}, schema, max_rows)
        else:
            engine = (make_engine or (lambda target: create_engine(target)))(url)
            (rows, columns, truncated), ran = await asyncio.to_thread(_run_sync, engine, sql, params or {}, schema, max_rows)
    except Exception as exc:  # connection, driver and query errors all come back as a redacted message
        message = redact_text(f"{type(exc).__name__}: {exc}", hidden)
        logger.error("SQL step failed: %s", message)
        return SqlResult(error=message, error_kind="database", duration_ms=_ms(started))
    return SqlResult(rows=rows, columns=columns, truncated=truncated, duration_ms=_ms(started), dialect=engine.dialect.name, statements=tuple(ran))


def _ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
