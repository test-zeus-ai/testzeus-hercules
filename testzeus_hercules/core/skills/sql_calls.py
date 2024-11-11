from typing import Annotated, Any, Dict, List, Optional, Union

from testzeus_hercules.core.skills.skill_registry import skill, skill_registry
from testzeus_hercules.utils.logger import logger
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.sql import text


@skill(
    description="Execute a SELECT SQL query on remote db, it should be only used when the instruction request to fetch data from database.",
    name="execute_select_query_sql_async",
)
async def execute_select_cte_query_sql(
    connection_string: Annotated[
        str,
        "The async database connection string in SQLAlchemy format. "
        "E.g., 'postgresql+asyncpg://user:password@host:port/database'.",
    ],
    query: Annotated[
        str, "The SELECT SQL query to execute. Must start with 'SELECT' or 'WITH'."
    ],
    schema: Annotated[
        Optional[str],
        "Optional database schema to use. If not provided, assumes schema is specified in the query.",
    ] = None,
    params: Annotated[
        Optional[Dict[str, Any]],
        "Optional parameters to pass to the query for parameterized queries.",
    ] = None,
) -> Annotated[
    Union[List[Dict[str, Any]], Dict[str, str]],
    "The query results or an error message.",
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
    - schema (Optional[str]): Optional database schema to use.
      If not provided, assumes schema is specified in the query using dot notation.
    - params (Optional[Dict[str, Any]]): Optional parameters to pass to the query.
      Use this for parameterized queries to prevent SQL injection.

    Returns:
    - Union[List[Dict[str, Any]], Dict[str, str]]: The query results as a list of dictionaries,
      or a dictionary containing an 'error' key with the error message.

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
            print(f"Error: {results['error']}")
        else:
            # Process the results
            for row in results:
                print(row)

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
    - **Schema Selection**: If 'schema' is provided, the function will set the schema for the session.
      For PostgreSQL, it uses 'SET search_path TO schema_name'.
      For MySQL/MariaDB, it uses 'USE schema_name'.
      For SQLite, schema selection is not applicable.
    - **Error Handling**: The function returns a dictionary with an 'error' key if an exception occurs.
      Check for this in your code to handle errors gracefully.

    """
    try:
        # Ensure only SELECT queries are allowed
        query_lower = query.strip().lower()
        if not (query_lower.startswith("select") or query_lower.startswith("with")):
            raise ValueError("Only SELECT queries are allowed.")

        # Create the async engine
        engine: AsyncEngine = create_async_engine(connection_string, echo=False)

        async with engine.connect() as connection:  # type: AsyncConnection
            if schema:
                if engine.dialect.name == "postgresql":
                    await connection.execute(text(f"SET search_path TO {schema}"))
                elif engine.dialect.name in ["mysql", "mariadb"]:
                    await connection.execute(text(f"USE {schema}"))
                # For SQLite, schema setting is not applicable

            result = await connection.execute(text(query), params or {})
            rows = [dict(row) for row in result]
            return rows
    except SQLAlchemyError as e:
        logger.error(f"SQLAlchemy error occurred: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return {"error": str(e)}
    finally:
        await engine.dispose()
