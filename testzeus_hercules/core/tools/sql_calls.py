import re
from typing import Annotated, Any, Dict, List, Optional, Union

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.sql import text
from testzeus_hercules.core.tools.tool_registry import tool, tool_registry
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.redact import redact_url

# Maximum number of rows materialized into the tool result. Anything beyond this
# is dropped and flagged with a truncation notice (an unbounded SELECT * on a
# big table would otherwise inflate both RSS and the LLM context).
MAX_ROWS = 1000

# Best-effort server-side statement timeout, in seconds, applied per dialect
# right after connect (see _apply_safety_settings).
MAX_STATEMENT_SECONDS = 30

# Destructive statement keywords rejected inside a WITH clause. The "SELECT or
# WITH" prefix guard alone lets write-through CTEs through ("WITH d AS (DELETE
# FROM t RETURNING *) SELECT * FROM d" starts with "with" but deletes rows), so
# the part of the query before the top-level SELECT is scanned with this
# word-boundary pattern. The scan is conservative: a quoted identifier or
# string literal containing one of these words (e.g. 'drop it') is a tolerated
# false positive — such a query is rejected, never allowed through.
_DESTRUCTIVE_KEYWORD_RE = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|ALTER|DROP|CREATE|GRANT|REVOKE)\b", re.IGNORECASE)

# URL-shaped tokens in driver error text (DSNs usually appear embedded in the
# message, not as the whole message) get redact_url applied; password-like
# key=value / key: value assignments are stripped as well.
_URL_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s'\"]+")
_SECRET_ASSIGNMENT_RE = re.compile(r"(?i)\b(password|passwd|pwd)\s*[=:]\s*(?!\[REDACTED\])\S+")


def _scrub_error(error: Exception) -> str:
    """Return the driver error text with credentials redacted.

    Driver exceptions echo the connection string (user/password/host) back, and
    that text would otherwise flow straight into the LLM context.
    """
    message = str(error)
    message = _URL_TOKEN_RE.sub(lambda match: redact_url(match.group(0)), message)
    return _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", message)


def _top_level_select_index(query: str) -> int:
    """Index of the first SELECT keyword at paren depth 0, or -1 if none.

    CTE bodies live inside parentheses, so the main SELECT of a WITH query is
    the first depth-0 SELECT; everything before it is the WITH section.
    """
    depth = 0
    for match in re.finditer(r"\bselect\b|[()]", query, re.IGNORECASE):
        token = match.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            return match.start()
    return -1


async def _apply_safety_settings(connection: AsyncConnection, dialect_name: str) -> None:
    """Best-effort per-dialect session hardening; failures never block the query.

    Enforces read-only execution and a statement timeout where the dialect
    supports it, as defense in depth behind the SELECT-only query guard.
    """
    try:
        if dialect_name == "postgresql":
            await connection.execute(text("SET default_transaction_read_only = on"))
            await connection.execute(text(f"SET statement_timeout = '{MAX_STATEMENT_SECONDS}s'"))
        elif dialect_name in ("mysql", "mariadb"):
            await connection.execute(text(f"SET SESSION max_execution_time = {MAX_STATEMENT_SECONDS * 1000}"))
        elif dialect_name == "sqlite":
            await connection.execute(text("PRAGMA query_only = ON"))
            await connection.execute(text(f"PRAGMA busy_timeout = {MAX_STATEMENT_SECONDS * 1000}"))
    except Exception as e:
        logger.debug(f"Could not apply read-only/timeout session settings for {dialect_name}: {e}")


@tool(
    agent_names=["sql_nav_agent"],
    description="Execute a SELECT SQL query on remote db, it should be only used when the instruction request to fetch data from database.",
    name="execute_select_query_sql_async",
)
async def execute_select_cte_query_sql(
    connection_string: Annotated[
        str,
        "async database connection string in SQLAlchemy format. " "E.g., 'postgresql+asyncpg://user:password@host:port/database'.",
    ],
    query: Annotated[str, "SELECT SQL query to execute. Must start with 'SELECT' or 'WITH'."],
    schema_name: Annotated[
        str,
        "Optional database schema_name to use. If not provided, assumes schema_name is specified in the query.",
    ] = "",
    params: Annotated[
        dict,
        "Optional parameters to pass to the query for parameterized queries.",
    ] = None,
) -> Annotated[
    Union[List[dict], dict],
    "SQL query results or an error message.",
]:
    """
    Execute a SELECT SQL query asynchronously using SQLAlchemy.

    Parameters:
    - connection_string (str): The async database connection string in SQLAlchemy format.
      Examples:
        - PostgreSQL: "postgresql+asyncpg://user:password@host:port/database"
        - MySQL: "mysql+aiomysql://user:password@host:port/database"
        - SQLite: "sqlite+aiosqlite:///path/to/database.db"
    - query (str): The SELECT SQL query to execute. Must start with 'SELECT' or 'WITH'.
      Data-modifying statements are rejected, including write-through CTEs
      ("WITH d AS (DELETE FROM t RETURNING *) SELECT ..."): the WITH section is
      scanned for destructive keywords before execution. The scan is
      conservative and may false-positive on quoted identifiers or string
      literals containing those keywords — such queries are rejected, never
      allowed through. Read-only session mode and a statement timeout are also
      enforced where the dialect supports them.
    - schema_name (Optional[str|None]): Optional database schema_name to use.
      If not provided, assumes schema_name is specified in the query using dot notation.
      Must contain only letters, digits and underscores.
    - params (Optional[Dict[str, Any]]): Optional parameters to pass to the query.
      Use this for parameterized queries to prevent SQL injection.

    Returns:
    - Union[List[Dict[str, Any]], Dict[str, str]]: The query results as a list of dictionaries,
      or a dictionary containing an 'error' key with an error message (driver
      details are redacted before returning). At most MAX_ROWS (1000) rows are
      materialized; when truncated, a final {"notice": ...} entry is appended.

    Example Usage:

    ```python
    import asyncio

    # Import the function
    # from your_module import execute_select_query_sql_async

    async def main():
        # Define the connection string
        connection_string = "postgresql+asyncpg://user:password@localhost:5432/mydatabase"

        # Define the SELECT query with a CTE
        query = '''
        WITH active_users AS (
            SELECT id, username FROM users WHERE active = :active_status
        )
        SELECT * FROM active_users WHERE id > :min_id
        '''

        # Define the parameters
        params = {"active_status": True, "min_id": 100}

        # Execute the query
        results = await execute_select_query_sql_async(
            connection_string=connection_string,
            query=query,
            params=params
        )

        # Check for errors
        if isinstance(results, dict) and "error" in results:
            logger.info(f"Error: {results['error']}")
        else:
            # Process the results
            for row in results:
                logger.info(row)

    # Run the async main function
    asyncio.run(main())
    ```

    Notes:
    - **Security Considerations**: Always use parameterized queries via the 'params' argument
      to prevent SQL injection attacks.
    - **Async Drivers**: Ensure that you have the appropriate async database driver installed.
      For example:
        - PostgreSQL: 'asyncpg' (install with 'pip install asyncpg')
        - MySQL: 'aiomysql' (install with 'pip install aiomysql')
        - SQLite: 'aiosqlite' (install with 'pip install aiosqlite')
    - **schema_name Selection**: If 'schema_name' is provided, the function will set the schema_name for the session.
      For PostgreSQL, it uses 'SET search_path TO schema_name'.
      For MySQL/MariaDB, it uses 'USE schema_name'.
      For SQLite, schema_name selection is not applicable.
    - **Error Handling**: The function returns a dictionary with an 'error' key if an exception occurs.
      Check for this in your code to handle errors gracefully.

    """
    engine: AsyncEngine = None
    try:
        # Ensure only SELECT queries are allowed
        query_lower = query.strip().lower()
        if not (query_lower.startswith("select") or query_lower.startswith("with")):
            raise ValueError("Only SELECT queries are allowed.")

        # A WITH query must not smuggle data-modifying statements through its
        # CTE bodies; scan everything before the top-level SELECT.
        if query_lower.startswith("with"):
            select_idx = _top_level_select_index(query)
            with_section = query[: select_idx if select_idx >= 0 else len(query)]
            destructive_match = _DESTRUCTIVE_KEYWORD_RE.search(with_section)
            if destructive_match:
                raise ValueError(f"Only SELECT queries are allowed: data-modifying keyword '{destructive_match.group(0).upper()}' found in the WITH clause.")

        # Create the async engine
        engine: AsyncEngine = create_async_engine(connection_string, echo=False)

        async with engine.connect() as connection:  # type: AsyncConnection
            # Defense in depth: read-only sessions + statement timeout where
            # the dialect supports them (best-effort, never blocks the query).
            await _apply_safety_settings(connection, engine.dialect.name)

            if schema_name:
                # Quote as an identifier via the dialect's preparer instead of interpolating
                # the raw string into SQL, so schema_name can't inject additional SQL.
                quoted_schema_name = engine.dialect.identifier_preparer.quote(schema_name)
                if engine.dialect.name == "postgresql":
                    await connection.execute(text(f"SET search_path TO {quoted_schema_name}"))
                elif engine.dialect.name in ["mysql", "mariadb"]:
                    await connection.execute(text(f"USE {quoted_schema_name}"))
                # For SQLite, schema_name setting is not applicable

            safe_params = params if isinstance(params, dict) else {}
            result = await connection.execute(text(query), safe_params)
            # Materialize at most MAX_ROWS rows; drop the rest and flag it.
            raw_rows = await result.fetchmany(MAX_ROWS + 1)
            await result.close()
            truncated = len(raw_rows) > MAX_ROWS
            rows = [dict(row._mapping) for row in raw_rows[:MAX_ROWS]]
            if truncated:
                rows.append({"notice": f"Result truncated to the first {MAX_ROWS} rows."})
            return rows
    except SQLAlchemyError as e:

        logger.error(f"SQLAlchemy error occurred: {_scrub_error(e)}", exc_info=True)
        return {"error": _scrub_error(e)}
    except Exception as e:

        logger.error(f"An unexpected error occurred: {_scrub_error(e)}", exc_info=True)
        return {"error": _scrub_error(e)}
    finally:
        try:
            await engine.dispose()
        except Exception:
            pass
