"""Unit tests for the SQL tool hardening (core.tools.sql_calls).

Covers: write-through CTE rejection, schema_name identifier validation,
per-dialect read-only/statement-timeout session settings, the MAX_ROWS
materialization cap and driver-error redaction. No database is involved —
create_async_engine is faked.
"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError
from testzeus_hercules.core.tools import sql_calls
from testzeus_hercules.core.tools.sql_calls import execute_select_cte_query_sql


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    async def fetchmany(self, size):
        return self._rows[:size]

    async def close(self):
        pass


class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class _FakeConnection:
    def __init__(self, rows=None, dialect="postgresql", fail_on_enter=None):
        self.executed = []  # list of statement texts
        self.dialect = SimpleNamespace(name=dialect)
        self._rows = rows or []
        self._fail_on_enter = fail_on_enter

    async def __aenter__(self):
        if self._fail_on_enter is not None:
            raise self._fail_on_enter
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        self.executed.append(str(statement))
        return _FakeResult(self._rows)


class _FakeEngine:
    def __init__(self, dialect="postgresql", rows=None, fail_on_enter=None):
        self.dialect = SimpleNamespace(name=dialect)
        self.connection = _FakeConnection(rows=rows, dialect=dialect, fail_on_enter=fail_on_enter)
        self.disposed = False

    def connect(self):
        return self.connection

    async def dispose(self):
        self.disposed = True


def _run(coro):
    return asyncio.run(coro)


def _statements(connection):
    return connection.executed


# ── finding 1: SELECT-only guard incl. write-through CTEs ───────────────────


def test_delete_cte_is_rejected() -> None:
    result = _run(execute_select_cte_query_sql(connection_string="sqlite+aiosqlite:///db.sqlite", query="WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d"))
    assert isinstance(result, dict) and "error" in result
    assert "Only SELECT queries are allowed" in result["error"]
    assert "DELETE" in result["error"]


@pytest.mark.parametrize(
    "query,keyword",
    [
        ("WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "DELETE"),
        ("with u as (update t set a = 1 returning *) select * from u", "UPDATE"),
        ("WITH d AS (TRUNCATE t) SELECT * FROM d", "TRUNCATE"),
        ("WITH d AS (DROP TABLE t) SELECT * FROM d", "DROP"),
        ("WITH d AS (MERGE INTO t SELECT 1) SELECT * FROM d", "MERGE"),
        ("WITH d AS (INSERT INTO t VALUES (1)) SELECT * FROM d", "INSERT"),
        ("WITH d AS (SELECT 1), e AS (ALTER TABLE t ADD c int) SELECT * FROM d", "ALTER"),
    ],
)
def test_destructive_cte_variants_are_rejected(query, keyword) -> None:
    result = _run(execute_select_cte_query_sql(connection_string="sqlite+aiosqlite:///db.sqlite", query=query))
    assert isinstance(result, dict) and "error" in result
    assert "Only SELECT queries are allowed" in result["error"]
    assert keyword in result["error"]


def test_plain_delete_is_rejected() -> None:
    result = _run(execute_select_cte_query_sql(connection_string="sqlite+aiosqlite:///db.sqlite", query="DELETE FROM t"))
    assert isinstance(result, dict) and "error" in result
    assert "Only SELECT queries are allowed" in result["error"]


def test_word_boundaries_keep_identifiers_out_of_the_guard(monkeypatch) -> None:
    # "deleted_at" / "updated_by" must not trip the destructive-keyword scan.
    engine = _FakeEngine(dialect="postgresql", rows=[_FakeRow({"deleted_at": None})])
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine)
    result = _run(
        execute_select_cte_query_sql(
            connection_string="postgresql+asyncpg://u:p@h/db",
            query="WITH recent AS (SELECT deleted_at, updated_by FROM t) SELECT * FROM recent",
        )
    )
    assert result == [{"deleted_at": None}]


def test_benign_select_cte_passes(monkeypatch) -> None:
    engine = _FakeEngine(dialect="postgresql", rows=[_FakeRow({"id": 1})])
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine)
    result = _run(execute_select_cte_query_sql(connection_string="postgresql+asyncpg://u:p@h/db", query="WITH active AS (SELECT 1 AS id) SELECT * FROM active"))
    assert result == [{"id": 1}]
    assert "WITH active AS (SELECT 1 AS id) SELECT * FROM active" in _statements(engine.connection)


# ── finding 1b / 3a: read-only + statement timeout session settings ─────────


def test_postgres_read_only_and_timeout_statements_sent(monkeypatch) -> None:
    engine = _FakeEngine(dialect="postgresql")
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine)
    _run(execute_select_cte_query_sql(connection_string="postgresql+asyncpg://u:p@h/db", query="SELECT 1"))
    stmts = _statements(engine.connection)
    assert "SET default_transaction_read_only = on" in stmts
    assert f"SET statement_timeout = '{sql_calls.MAX_STATEMENT_SECONDS}s'" in stmts
    assert stmts.index("SET default_transaction_read_only = on") < stmts.index("SELECT 1")  # applied right after connect


def test_sqlite_query_only_pragma_attempted(monkeypatch) -> None:
    engine = _FakeEngine(dialect="sqlite")
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine)
    _run(execute_select_cte_query_sql(connection_string="sqlite+aiosqlite:///db.sqlite", query="SELECT 1"))
    stmts = _statements(engine.connection)
    assert "PRAGMA query_only = ON" in stmts
    assert f"PRAGMA busy_timeout = {sql_calls.MAX_STATEMENT_SECONDS * 1000}" in stmts


def test_mysql_statement_timeout_sent(monkeypatch) -> None:
    engine = _FakeEngine(dialect="mysql")
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine)
    _run(execute_select_cte_query_sql(connection_string="mysql+aiomysql://u:p@h/db", query="SELECT 1"))
    assert f"SET SESSION max_execution_time = {sql_calls.MAX_STATEMENT_SECONDS * 1000}" in _statements(engine.connection)


def test_safety_settings_failure_does_not_block_query(monkeypatch) -> None:
    class _FailingSafetyConnection(_FakeConnection):
        async def execute(self, statement, params=None):
            if str(statement).startswith(("SET default_transaction", "SET statement_timeout", "PRAGMA")):
                raise SQLAlchemyError("read-only not supported")
            return await super().execute(statement, params)

    engine = _FakeEngine(dialect="postgresql")
    engine.connection = _FailingSafetyConnection(rows=[_FakeRow({"ok": 1})], dialect="postgresql")
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine)
    result = _run(execute_select_cte_query_sql(connection_string="postgresql+asyncpg://u:p@h/db", query="SELECT 1 AS ok"))
    assert result == [{"ok": 1}]


# ── finding 2: schema_name identifier validation ────────────────────────────


@pytest.mark.parametrize("bad_schema", ["public; DROP TABLE x", 'app"schema', "a b", "schema-name", "schema;--"])
def test_invalid_schema_name_rejected(monkeypatch, bad_schema) -> None:
    engine = _FakeEngine(dialect="postgresql")
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine)
    result = _run(execute_select_cte_query_sql(connection_string="postgresql+asyncpg://u:p@h/db", query="SELECT 1", schema_name=bad_schema))
    assert isinstance(result, dict) and "error" in result
    assert "schema_name" in result["error"]
    # The raw identifier must never be interpolated into any executed statement.
    assert all(bad_schema not in stmt for stmt in engine.connection.executed)
    assert not any("search_path" in stmt or "USE" in stmt for stmt in engine.connection.executed)


def test_valid_schema_name_interpolated_safely(monkeypatch) -> None:
    engine = _FakeEngine(dialect="postgresql")
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine)
    result = _run(execute_select_cte_query_sql(connection_string="postgresql+asyncpg://u:p@h/db", query="SELECT 1", schema_name="app_schema"))
    assert result == []
    assert "SET search_path TO app_schema" in _statements(engine.connection)


def test_mysql_use_schema_path_validated(monkeypatch) -> None:
    engine_bad = _FakeEngine(dialect="mysql")
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine_bad)
    result = _run(execute_select_cte_query_sql(connection_string="mysql+aiomysql://u:p@h/db", query="SELECT 1", schema_name="shop; DROP db"))
    assert isinstance(result, dict) and "error" in result
    assert not any("USE" in stmt for stmt in engine_bad.connection.executed)

    engine_ok = _FakeEngine(dialect="mariadb")
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine_ok)
    _run(execute_select_cte_query_sql(connection_string="mysql+aiomysql://u:p@h/db", query="SELECT 1", schema_name="shop"))
    assert "USE shop" in _statements(engine_ok.connection)


# ── finding 3b: MAX_ROWS materialization cap ────────────────────────────────


def test_rows_capped_at_max_rows_with_notice(monkeypatch) -> None:
    rows = [_FakeRow({"i": i}) for i in range(sql_calls.MAX_ROWS + 500)]
    engine = _FakeEngine(dialect="sqlite", rows=rows)
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine)
    result = _run(execute_select_cte_query_sql(connection_string="sqlite+aiosqlite:///db.sqlite", query="SELECT 1"))
    assert len(result) == sql_calls.MAX_ROWS + 1
    assert result[-1] == {"notice": f"Result truncated to the first {sql_calls.MAX_ROWS} rows."}
    assert result[0] == {"i": 0}
    assert result[-2] == {"i": sql_calls.MAX_ROWS - 1}


def test_rows_under_cap_have_no_notice(monkeypatch) -> None:
    rows = [_FakeRow({"i": i}) for i in range(5)]
    engine = _FakeEngine(dialect="sqlite", rows=rows)
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine)
    result = _run(execute_select_cte_query_sql(connection_string="sqlite+aiosqlite:///db.sqlite", query="SELECT 1"))
    assert result == [{"i": i} for i in range(5)]


# ── finding 4: driver errors are redacted before being returned ─────────────


def test_dsn_credentials_redacted_in_error(monkeypatch) -> None:
    engine = _FakeEngine(fail_on_enter=SQLAlchemyError('connection failed for "postgresql+asyncpg://bob:s3cret-pw@secret-db.example.com:5432/db"'))
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine)
    result = _run(execute_select_cte_query_sql(connection_string="postgresql+asyncpg://bob:s3cret-pw@secret-db.example.com/db", query="SELECT 1"))
    assert isinstance(result, dict) and "error" in result
    assert "s3cret-pw" not in result["error"]
    assert "bob:" not in result["error"]
    assert "secret-db.example.com" in result["error"]  # host survives, creds do not


def test_password_assignment_redacted_in_error(monkeypatch) -> None:
    engine = _FakeEngine(fail_on_enter=SQLAlchemyError("auth failed: password=hunter2 user=bob passwd=topsecret"))
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine)
    result = _run(execute_select_cte_query_sql(connection_string="postgresql+asyncpg://u:p@h/db", query="SELECT 1"))
    assert isinstance(result, dict) and "error" in result
    assert "hunter2" not in result["error"]
    assert "topsecret" not in result["error"]
    assert "[REDACTED]" in result["error"]


def test_non_sqlalchemy_error_also_redacted(monkeypatch) -> None:
    class _ExplodingEngine(_FakeEngine):
        def connect(self):
            raise ValueError("bad url: postgresql://eve:letmein@db.internal/x password=abc123")

    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: _ExplodingEngine())
    result = _run(execute_select_cte_query_sql(connection_string="postgresql://eve:letmein@db.internal/x", query="SELECT 1"))
    assert isinstance(result, dict) and "error" in result
    assert "letmein" not in result["error"]
    assert "abc123" not in result["error"]
