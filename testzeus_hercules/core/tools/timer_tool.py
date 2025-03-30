import asyncio
import traceback
from typing import Annotated, Dict

from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger


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
    try:
        # Validate input
        if not isinstance(duration, (int, float)):
            return {"status": "error", "message": "Duration must be a number"}

        duration = float(duration)
        if duration < 0:
            return {"status": "error", "message": "Duration cannot be negative"}

        if duration > 3600:
            return {"status": "error", "message": "Duration cannot exceed 3600 seconds"}

        # Perform the wait
        logger.info(f"Starting wait for {duration} seconds")
        await asyncio.sleep(duration)
        logger.info(f"Completed wait for {duration} seconds")

        return {"status": "success", "message": f"Waited for {duration} seconds"}

    except Exception as e:

        traceback.print_exc()
        logger.error(f"Error during wait: {str(e)}")
        return {"status": "error", "message": f"Wait operation failed: {str(e)}"}


@tool(
    agent_names=["time_keeper_nav_agent"],
    description="Get the current timestamp in string format.",
    name="get_current_timestamp",
)
async def get_current_timestamp() -> Annotated[
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

    # Get current timestamp
    current_timestamp = datetime.now().isoformat()
    return {"timestamp": current_timestamp}
