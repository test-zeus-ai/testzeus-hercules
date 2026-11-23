"""The read-only ``select`` tool (``act.sql``), ported from the v1 SQL hardening tests.

Most cases use a fake async engine; the last ones run the standard library SQLite driver.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.exc import SQLAlchemyError

from testzeus_hercules.act import sql
from testzeus_hercules.act.sql import MAX_ROWS, MAX_STATEMENT_SECONDS, QueryRefused, SqlResult, check_query, select


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchmany(self, size: int) -> list[Any]:
        return self._rows[:size]

    def keys(self) -> list[str]:
        return list(self._rows[0]._mapping) if self._rows else []

    def close(self) -> None:
        pass


class _Row:
    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping


class _Connection:
    def __init__(self, rows: list[_Row] | None = None, fail_on_enter: Exception | None = None) -> None:
        self.executed: list[str] = []
        self.rows = rows or []
        self.fail_on_enter = fail_on_enter
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def __aenter__(self) -> _Connection:
        if self.fail_on_enter is not None:
            raise self.fail_on_enter
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        self.executed.append(str(statement))
        return _Result(self.rows)


class _Engine:
    def __init__(self, dialect: str = "postgresql", rows: list[_Row] | None = None, fail_on_enter: Exception | None = None) -> None:
        real = mysql.dialect() if dialect in ("mysql", "mariadb") else postgresql.dialect()
        self.dialect = SimpleNamespace(name=dialect, identifier_preparer=real.identifier_preparer)
        self.connection = _Connection(rows=rows, fail_on_enter=fail_on_enter)
        self.disposed = False

    def connect(self) -> _Connection:
        return self.connection

    async def dispose(self) -> None:
        self.disposed = True


def run(engine: _Engine, query: str = "SELECT 1", url: str = "postgresql+asyncpg://u:p@h/db", **kwargs: Any) -> SqlResult:
    return asyncio.run(select(url, query, make_async_engine=lambda target: engine, **kwargs))


# ── SELECT-only guard ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "keyword"),
    [
        ("WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "DELETE"),
        ("with u as (update t set a = 1 returning *) select * from u", "UPDATE"),
        ("WITH d AS (TRUNCATE t) SELECT * FROM d", "TRUNCATE"),
        ("WITH d AS (DROP TABLE t) SELECT * FROM d", "DROP"),
        ("WITH d AS (MERGE INTO t SELECT 1) SELECT * FROM d", "MERGE"),
        ("WITH d AS (INSERT INTO t VALUES (1)) SELECT * FROM d", "INSERT"),
        ("WITH d AS (SELECT 1), e AS (ALTER TABLE t ADD c int) SELECT * FROM d", "ALTER"),
        ("SELECT * INTO backup FROM t", "INTO"),
    ],
)
def test_data_modifying_statements_are_refused(query: str, keyword: str) -> None:
    with pytest.raises(QueryRefused, match=keyword):
        check_query(query)
    result = asyncio.run(select("sqlite:///db.sqlite", query))
    assert result.error_kind == "refused" and "Only SELECT queries are allowed" in (result.error or "")


@pytest.mark.parametrize("query", ["DELETE FROM t", "PRAGMA writable_schema = 1", "SELECT 1; DROP TABLE t", "  update t set a=1"])
def test_other_statements_and_stacking_are_refused(query: str) -> None:
    with pytest.raises(QueryRefused):
        check_query(query)


def test_literals_comments_and_identifiers_never_trip_the_guard() -> None:
    assert check_query("SELECT deleted_at, updated_by FROM t WHERE note = 'drop it; now' -- delete later\n").startswith("SELECT")
    assert check_query('WITH recent AS (SELECT "update" FROM t /* insert */) SELECT * FROM recent;') == 'WITH recent AS (SELECT "update" FROM t /* insert */) SELECT * FROM recent'
    engine = _Engine(rows=[_Row({"deleted_at": None})])
    assert run(engine, "WITH recent AS (SELECT deleted_at FROM t) SELECT * FROM recent").rows == ({"deleted_at": None},)


# ── read-only sessions and timeouts ──────────────────────────────────────────


def test_postgres_read_only_and_timeout_run_before_the_query() -> None:
    engine = _Engine()
    run(engine)
    executed = engine.connection.executed
    assert executed[0] == "SET TRANSACTION READ ONLY"
    assert f"SET statement_timeout = '{MAX_STATEMENT_SECONDS}s'" in executed
    assert executed.index("SET default_transaction_read_only = on") < executed.index("SELECT 1")
    assert engine.disposed


@pytest.mark.parametrize("dialect", ["mysql", "mariadb"])
def test_mysql_read_only_and_timeout(dialect: str) -> None:
    engine = _Engine(dialect)
    run(engine, url="mysql+aiomysql://u:p@h/db")
    executed = engine.connection.executed
    assert "SET SESSION TRANSACTION READ ONLY" in executed and f"SET SESSION max_execution_time = {MAX_STATEMENT_SECONDS * 1000}" in executed
    assert executed.index("SET TRANSACTION READ ONLY") < executed.index("SELECT 1")


def test_a_failing_safety_setting_does_not_block_the_query() -> None:
    class Failing(_Connection):
        async def execute(self, statement: Any, params: Any = None) -> _Result:
            if str(statement).startswith(("SET", "PRAGMA")):
                raise SQLAlchemyError("read-only not supported")
            return await super().execute(statement, params)

    engine = _Engine()
    engine.connection = Failing(rows=[_Row({"ok": 1})])
    assert run(engine, "SELECT 1 AS ok").rows == ({"ok": 1},)
    assert engine.connection.rollbacks == 1


@pytest.mark.parametrize("payload", ['public"; DROP TABLE users; --', "public; DROP TABLE x"])
def test_schema_names_are_quoted_identifiers(payload: str) -> None:
    engine = _Engine()
    run(engine, schema=payload)
    statement = next(item for item in engine.connection.executed if item.startswith("SET search_path TO"))
    assert statement.startswith('SET search_path TO "') and payload.replace('"', '""') in statement


# ── rows ─────────────────────────────────────────────────────────────────────


def test_rows_are_capped_and_flagged() -> None:
    engine = _Engine(rows=[_Row({"i": index}) for index in range(MAX_ROWS + 5)])
    result = run(engine)
    assert len(result.rows) == MAX_ROWS and result.truncated and result.rows[-1] == {"i": MAX_ROWS - 1}
    small = run(_Engine(rows=[_Row({"i": index}) for index in range(3)]))
    assert not small.truncated and small.columns == ("i",)


def test_values_become_json() -> None:
    import datetime as dt
    import decimal

    assert sql.jsonable(decimal.Decimal("2.50")) == "2.50" and sql.jsonable(decimal.Decimal("3")) == 3
    assert sql.jsonable(dt.date(2026, 1, 2)) == "2026-01-02" and sql.jsonable(b"\x00") == "AA=="


# ── redaction ────────────────────────────────────────────────────────────────


def test_connection_strings_and_passwords_never_reach_errors(caplog: pytest.LogCaptureFixture) -> None:
    secret = 'connection failed for "postgresql+asyncpg://bob:s3cret-pw@db.example.test:5432/db" password=hunter2'
    result = run(_Engine(fail_on_enter=SQLAlchemyError(secret)), url="postgresql+asyncpg://bob:s3cret-pw@db.example.test/db")
    assert result.error_kind == "database"
    assert "s3cret-pw" not in (result.error or "") and "hunter2" not in (result.error or "") and "db.example.test" in (result.error or "")
    assert "s3cret-pw" not in caplog.text and "hunter2" not in caplog.text


def test_the_connection_string_itself_is_scrubbed_even_without_a_url_shape() -> None:
    class Exploding(_Engine):
        def connect(self) -> _Connection:
            raise ValueError("cannot use dsn host=db user=eve password=letmein")

    result = run(Exploding(), url="postgresql+asyncpg://eve:letmein@db/x")
    assert "letmein" not in (result.error or "")


# ── real SQLite ──────────────────────────────────────────────────────────────


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        connection.executemany("INSERT INTO t VALUES (?, ?)", [(index, f"n{index}") for index in range(MAX_ROWS + 5)])


def test_real_sqlite_select_params_cap_and_cte(tmp_path: Path) -> None:
    _database(tmp_path / "real.sqlite")
    url = f"sqlite:///{tmp_path / 'real.sqlite'}"
    rows = asyncio.run(select(url, "SELECT id, name FROM t WHERE id < :n ORDER BY id", params={"n": 3}))
    assert rows.rows == ({"id": 0, "name": "n0"}, {"id": 1, "name": "n1"}, {"id": 2, "name": "n2"}) and rows.dialect == "sqlite"
    assert asyncio.run(select(url, "SELECT id FROM t ORDER BY id")).truncated
    assert asyncio.run(select(url, "WITH x AS (SELECT id FROM t WHERE id = 7) SELECT * FROM x")).rows == ({"id": 7},)


def test_sqlite_paths_are_relative_to_the_project_and_must_exist(tmp_path: Path) -> None:
    _database(tmp_path / "app db.sqlite")
    assert asyncio.run(select("sqlite:///app db.sqlite", "SELECT count(*) AS n FROM t", base_dir=tmp_path)).rows == ({"n": MAX_ROWS + 5},)
    missing = asyncio.run(select("sqlite:///nope.db", "SELECT 1", base_dir=tmp_path))
    assert "not found" in (missing.error or "") and not (tmp_path / "nope.db").exists()


def test_sqlite_files_open_read_only(tmp_path: Path) -> None:
    _database(tmp_path / "ro.sqlite")
    url = sql.prepare_url("sqlite:///ro.sqlite", base_dir=tmp_path)
    assert url.query["mode"] == "ro" and url.query["uri"] == "true"
    from sqlalchemy import create_engine, text

    with create_engine(url).connect() as connection, pytest.raises(Exception, match="readonly"):
        connection.execute(text("INSERT INTO t VALUES (1, 'x')"))


def test_aiosqlite_urls_fall_back_to_the_standard_driver(tmp_path: Path) -> None:
    _database(tmp_path / "a.sqlite")
    result = asyncio.run(select(f"sqlite+aiosqlite:///{tmp_path / 'a.sqlite'}", "SELECT count(*) AS n FROM t"))
    assert result.ok and result.rows == ({"n": MAX_ROWS + 5},)
