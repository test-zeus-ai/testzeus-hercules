import asyncio
import traceback
from typing import Annotated, Dict

from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.integration.dual_mode_adapter import get_dual_mode_adapter


@tool(
    agent_names=["time_keeper_nav_agent"],
    description="Wait for a specified number of seconds. Only accepts numeric values between 0 and 3600 seconds.",
    name="wait_for_duration",
)
async def wait_for_duration(
    duration: Annotated[
        float,
        "Number of seconds to wait. Must be between 0 and 3600 seconds.",
    ],
    mode: Annotated[str, "Operation mode: 'agent' (default) or 'code'"] = "agent",
) -> Annotated[
    Dict[str, str],
    "Result of the wait operation containing status and message.",
]:
    """
    Wait for a specified duration asynchronously.

    Parameters:
    - duration (float): The number of seconds to wait. Must be between 0 and 3600 seconds.

    Returns:
    - Dict[str, str]: A dictionary containing the status and message of the wait operation.

    Example Usage:
    ```python
    result = await wait_for_duration(5.5)
    # Returns: {"status": "success", "message": "Waited for 5.5 seconds"}
    ```

    Notes:
    - Maximum wait time is 3600 seconds (1 hour)
    - Minimum wait time is 0 seconds
    - Non-numeric values will be rejected
    - Negative values will be rejected
    """
    adapter = get_dual_mode_adapter()
    
    if mode == "agent":
        query_selector = f"duration_{duration}"
    else:
        query_selector = f"duration_{duration}"
    
    try:
        # Validate input
        if not isinstance(duration, (int, float)):
            result = {"status": "error", "message": "Duration must be a number"}
            
            await adapter.log_tool_interaction(
                tool_name="wait_for_seconds",
                selector=f"duration_{duration}",
                action="wait_operation",
                success=False,
                error_message="Invalid duration type",
                additional_data={
                    "duration": duration,
                    "error_type": "validation"
                }
            )
            
            return result

        duration = float(duration)
        if duration < 0:
            result = {"status": "error", "message": "Duration cannot be negative"}
            
            await adapter.log_tool_interaction(
                tool_name="wait_for_seconds",
                selector=f"duration_{duration}",
                action="wait_operation",
                success=False,
                error_message="Negative duration",
                additional_data={
                    "duration": duration,
                    "error_type": "validation"
                }
            )
            
            return result

        if duration > 3600:
            result = {"status": "error", "message": "Duration cannot exceed 3600 seconds"}
            
            await adapter.log_tool_interaction(
                tool_name="wait_for_seconds",
                selector=f"duration_{duration}",
                action="wait_operation",
                success=False,
                error_message="Duration too long",
                additional_data={
                    "duration": duration,
                    "error_type": "validation"
                }
            )
            
            return result

        # Perform the wait
        logger.info(f"Starting wait for {duration} seconds")
        await asyncio.sleep(duration)
        logger.info(f"Completed wait for {duration} seconds")

        result = {"status": "success", "message": f"Waited for {duration} seconds"}
        
        await adapter.log_tool_interaction(
            tool_name="wait_for_seconds",
            selector=f"duration_{duration}",
            action="wait_operation",
            success=True,
            additional_data={
                "duration": duration,
                "wait_type": "fixed_duration"
            }
        )

        return result

    except Exception as e:
        traceback.print_exc()
        logger.error(f"Error during wait: {str(e)}")
        
        result = {"status": "error", "message": f"Wait operation failed: {str(e)}"}
        
        await adapter.log_tool_interaction(
            tool_name="wait_for_seconds",
            selector=f"duration_{duration}",
            action="wait_operation",
            success=False,
            error_message=str(e),
            additional_data={
                "duration": duration,
                "error_type": "unexpected"
            }
        )
        
        return result


@tool(
    agent_names=["time_keeper_nav_agent"],
    description="Get the current timestamp in string format.",
    name="get_current_timestamp",
)
async def get_current_timestamp(
    mode: Annotated[str, "Operation mode: 'agent' (default) or 'code'"] = "agent",
) -> Annotated[
    Dict[str, str],
    "Current timestamp in string format.",
]:
    """
    Get the current timestamp asynchronously.

    Returns:
    - Dict[str, str]: A dictionary containing the current timestamp.

    Example Usage:
    ```python
    result = await get_current_timestamp()
    # Returns: {"timestamp": "2023-10-01T12:00:00"}
    ```
    """
    from datetime import datetime
    
    adapter = get_dual_mode_adapter()
    
    if mode == "agent":
        query_selector = "system_time"
    else:
        query_selector = "system_time"

    try:
        # Get current timestamp
        current_timestamp = datetime.now().isoformat()
        result = {"timestamp": current_timestamp}
        
        await adapter.log_tool_interaction(
            tool_name="get_current_timestamp",
            selector="system_time",
            action="timestamp_retrieval",
            success=True,
            additional_data={
                "timestamp": current_timestamp,
                "operation_type": "system_time"
            }
        )
        
        return result
        
    except Exception as e:
        await adapter.log_tool_interaction(
            tool_name="get_current_timestamp",
            selector="system_time",
            action="timestamp_retrieval",
            success=False,
            error_message=str(e),
            additional_data={
                "error_type": "unexpected"
            }
        )
        
        raise
