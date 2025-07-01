"""
Dual-mode SQL operations tool.
"""

import traceback
from typing import Optional, Dict, Any, List, Union
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.sql import text
from .base import BaseTool
from .logger import InteractionLogger
from ..config import ToolsConfig
from ..playwright_manager import ToolsPlaywrightManager


class SqlOperationsTool(BaseTool):
    """Dual-mode SQL operations tool."""
    
    def __init__(self, config: Optional[ToolsConfig] = None, playwright_manager: Optional[ToolsPlaywrightManager] = None):
        super().__init__(config, playwright_manager)
        self.logger = InteractionLogger(config)


async def execute_select_query(
    connection_string: str,
    query: str,
    schema_name: str = "",
    params: Optional[Dict[str, Any]] = None,
    config: Optional[ToolsConfig] = None,
    playwright_manager: Optional[ToolsPlaywrightManager] = None
) -> Dict[str, Any]:
    """
    Execute a SELECT SQL query with dual-mode support.
    
    Args:
        connection_string: Async database connection string in SQLAlchemy format
        query: SELECT SQL query to execute (must start with 'SELECT' or 'WITH')
        schema_name: Optional database schema name to use
        params: Optional parameters for parameterized queries
        config: Tools configuration
        playwright_manager: Playwright manager instance
        
    Returns:
        Dictionary with success status and query results
    """
    tool = SqlOperationsTool(config, playwright_manager)
    
    try:
        query_lower = query.strip().lower()
        if not (query_lower.startswith("select") or query_lower.startswith("with")):
            result = {
                "success": False,
                "error": "Only SELECT queries are allowed",
                "query": query,
                "mode": tool.config.mode
            }
            
            await tool.logger.log_interaction(
                tool_name="execute_select_query",
                selector=connection_string,
                action="sql_query",
                success=False,
                error_message="Invalid query type",
                mode=tool.config.mode,
                additional_data={"query": query, "query_type": "invalid"}
            )
            
            return result
        
        engine: AsyncEngine = create_async_engine(connection_string, echo=False)
        
        try:
            async with engine.connect() as connection:
                if schema_name:
                    if engine.dialect.name == "postgresql":
                        await connection.execute(text(f"SET search_path TO {schema_name}"))
                    elif engine.dialect.name in ["mysql", "mariadb"]:
                        await connection.execute(text(f"USE {schema_name}"))
                
                result_set = await connection.execute(text(query), params or {})
                rows = [dict(row) for row in result_set]
                
                result = {
                    "success": True,
                    "data": rows,
                    "row_count": len(rows),
                    "query": query,
                    "schema_name": schema_name,
                    "mode": tool.config.mode
                }
                
                await tool.logger.log_interaction(
                    tool_name="execute_select_query",
                    selector=connection_string,
                    action="sql_query",
                    success=True,
                    mode=tool.config.mode,
                    additional_data={
                        "query": query,
                        "schema_name": schema_name,
                        "row_count": len(rows),
                        "params": params
                    }
                )
                
                return result
                
        finally:
            await engine.dispose()
            
    except SQLAlchemyError as e:
        result = {
            "success": False,
            "error": f"SQLAlchemy error: {str(e)}",
            "query": query,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="execute_select_query",
            selector=connection_string,
            action="sql_query",
            success=False,
            error_message=str(e),
            mode=tool.config.mode,
            additional_data={"query": query, "error_type": "sqlalchemy"}
        )
        
        return result
        
    except Exception as e:
        result = {
            "success": False,
            "error": f"Unexpected error: {str(e)}",
            "query": query,
            "mode": tool.config.mode
        }
        
        await tool.logger.log_interaction(
            tool_name="execute_select_query",
            selector=connection_string,
            action="sql_query",
            success=False,
            error_message=str(e),
            mode=tool.config.mode,
            additional_data={"query": query, "error_type": "unexpected"}
        )
        
        return result
