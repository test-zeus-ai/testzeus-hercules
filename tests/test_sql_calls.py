import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.dialects import mysql, postgresql
from testzeus_hercules.core.tools import sql_calls as sql_calls_module
from testzeus_hercules.core.tools.sql_calls import execute_select_cte_query_sql


def _run_with_fake_engine(dialect, schema_name: str):
    """Run execute_select_cte_query_sql against a fake engine/connection and
    return the exact SQL text of the schema-setting statement it executed."""
    executed_statements = []

    async def fake_execute(clause, *args, **kwargs):
        executed_statements.append(getattr(clause, "text", str(clause)))

        class FakeResult:
            def __iter__(self):
                return iter([])

        return FakeResult()

    fake_connection = MagicMock()
    fake_connection.execute = AsyncMock(side_effect=fake_execute)
    fake_connection.__aenter__ = AsyncMock(return_value=fake_connection)
    fake_connection.__aexit__ = AsyncMock(return_value=False)

    fake_engine = MagicMock()
    fake_engine.connect = MagicMock(return_value=fake_connection)
    fake_engine.dialect = dialect
    fake_engine.dispose = AsyncMock()

    # Patch the module object directly (not by dotted string path): another test
    # module in this suite (test_llm_cli_aliases.py) deletes testzeus_hercules.*
    # entries from sys.modules without restoring them, which breaks string-path
    # patching if that test ran earlier in the same session.
    with patch.object(sql_calls_module, "create_async_engine", return_value=fake_engine):
        result = asyncio.run(
            execute_select_cte_query_sql(
                connection_string="postgresql+asyncpg://user:pass@localhost/db",
                query="SELECT 1",
                schema_name=schema_name,
            )
        )

    assert result == []
    return executed_statements[0]


def test_postgres_schema_name_with_injection_payload_is_quoted_not_interpolated():
    """A schema_name containing SQL syntax must not be able to break out of
    the SET search_path statement and inject additional SQL."""
    malicious_schema_name = 'public"; DROP TABLE users; --'

    schema_statement = _run_with_fake_engine(postgresql.dialect(), malicious_schema_name)

    # The old vulnerable code produced this string verbatim (raw interpolation).
    vulnerable_statement = f"SET search_path TO {malicious_schema_name}"
    assert schema_statement != vulnerable_statement

    # The identifier must be quoted, with embedded quotes escaped by doubling,
    # so the whole payload stays inside a single identifier token.
    assert schema_statement.startswith('SET search_path TO "')
    assert '""' in schema_statement


def test_mysql_schema_name_with_injection_payload_is_quoted_not_interpolated():
    malicious_schema_name = "schema`; DROP TABLE users; --"

    schema_statement = _run_with_fake_engine(mysql.dialect(), malicious_schema_name)

    vulnerable_statement = f"USE {malicious_schema_name}"
    assert schema_statement != vulnerable_statement
    assert schema_statement.startswith("USE `")


def test_postgres_benign_schema_name_still_works():
    # identifier_preparer.quote() only adds quotes when actually needed
    # (reserved words, mixed case, special characters); a plain lowercase
    # identifier like "public" is returned as-is, which is still safe.
    schema_statement = _run_with_fake_engine(postgresql.dialect(), "public")
    assert schema_statement == "SET search_path TO public"
