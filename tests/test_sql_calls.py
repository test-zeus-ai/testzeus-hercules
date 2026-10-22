"""Unit tests for the SQL tool hardening (core.tools.sql_calls).

Covers: write-through CTE rejection, schema_name identifier quoting,
per-dialect read-only/statement-timeout session settings, the MAX_ROWS
materialization cap and driver-error redaction. No database is involved —
create_async_engine is faked.
"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import mysql, postgresql
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
        real = mysql.dialect() if dialect in ("mysql", "mariadb") else postgresql.dialect()
        self.dialect = SimpleNamespace(name=dialect, identifier_preparer=real.identifier_preparer)
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


# ── finding 2: schema_name is quoted as an identifier, never interpolated ───


def _schema_statement(monkeypatch, dialect: str, schema_name: str) -> str:
    engine = _FakeEngine(dialect=dialect)
    monkeypatch.setattr(sql_calls, "create_async_engine", lambda cs, echo=False: engine)
    scheme = "postgresql+asyncpg" if dialect == "postgresql" else "mysql+aiomysql"
    result = _run(execute_select_cte_query_sql(connection_string=f"{scheme}://u:p@h/db", query="SELECT 1", schema_name=schema_name))
    assert result == []
    return next(stmt for stmt in _statements(engine.connection) if stmt.startswith(("SET search_path TO", "USE ")))


@pytest.mark.parametrize("payload", ['public"; DROP TABLE users; --', "public; DROP TABLE x", 'app"schema'])
def test_postgres_injection_payload_stays_inside_one_quoted_identifier(monkeypatch, payload) -> None:
    statement = _schema_statement(monkeypatch, "postgresql", payload)
    assert statement != f"SET search_path TO {payload}"  # not raw interpolation
    assert statement.startswith('SET search_path TO "')
    assert statement.endswith('"')
    # embedded quotes are escaped by doubling, so the payload cannot close the identifier early
    assert payload.replace('"', '""') in statement


def test_mysql_injection_payload_is_backtick_quoted(monkeypatch) -> None:
    payload = "schema`; DROP TABLE users; --"
    statement = _schema_statement(monkeypatch, "mysql", payload)
    assert statement != f"USE {payload}"
    assert statement.startswith("USE `")


@pytest.mark.parametrize("schema", ["app_schema", "schema-name", "my.schema"])
def test_legitimate_schema_names_are_accepted(monkeypatch, schema) -> None:
    statement = _schema_statement(monkeypatch, "postgresql", schema)
    assert schema in statement


def test_mariadb_use_schema(monkeypatch) -> None:
    assert _schema_statement(monkeypatch, "mariadb", "shop") == "USE shop"


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
