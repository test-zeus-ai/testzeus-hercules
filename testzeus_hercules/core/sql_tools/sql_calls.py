from typing import Annotated, Any, Dict, List, Optional, Union

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["sql_nav_agent"],
    description="Execute a SELECT SQL query on remote db, it should be only used when the instruction request to fetch data from database.",
    name="execute_select_query_sql",
)
def execute_select_query_sql(
    connection_string: Annotated[
        str,
        "database connection string in SQLAlchemy format. "
        "E.g., 'postgresql://user:password@host:port/database'.",
    ],
    query: Annotated[
        str, "SELECT SQL query to execute. Must start with 'SELECT' or 'WITH'."
    ],
    schema: Annotated[
        str,
        "Optional database schema to use. If not provided, assumes schema is specified in the query.",
    ] = "",
    params: Annotated[
        Optional[Dict[str, Any]],
        "Optional parameters to pass to the query for parameterized queries.",
    ] = None,
) -> Annotated[
    Union[List[Dict[str, Any]], Dict[str, str]],
    "SQL query results or an error message.",
]:
    """
    Execute a SELECT SQL query on a remote database.

    Args:
        connection_string: SQLAlchemy connection string
        query: SQL query to execute (must be SELECT)
        schema: Optional database schema
        params: Optional query parameters

    Returns:
        List of dictionaries containing query results, or error message
    """
    try:
        # Ensure only SELECT queries are allowed
        query_lower = query.strip().lower()
        if not (query_lower.startswith("select") or query_lower.startswith("with")):
            raise ValueError("Only SELECT queries are allowed.")

        # Create the engine
        engine = create_engine(connection_string, echo=False)

        with engine.connect() as connection:
            if schema:
                if engine.dialect.name == "postgresql":
                    connection.execute(text(f"SET search_path TO {schema}"))
                elif engine.dialect.name in ["mysql", "mariadb"]:
                    connection.execute(text(f"USE {schema}"))
                # For SQLite, schema setting is not applicable

            result = connection.execute(text(query), params or {})
            rows = [dict(row) for row in result]
            return rows

    except SQLAlchemyError as e:
        logger.error(f"SQLAlchemy error occurred: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return {"error": str(e)}
    finally:
        if "engine" in locals():
            engine.dispose()
