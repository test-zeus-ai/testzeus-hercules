from typing import Annotated, Dict, Union

from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger

# Module-level state string
_state_string: str = ""


@tool(
    agent_names=["browser_nav_agent", "api_nav_agent", "sql_nav_agent"],
    description="Tool to store information.",
    name="store_data",
)
def store_data(
    text: Annotated[
        str,
        "The confirmation of stored value.",
    ]
) -> Annotated[
    Dict[str, Union[str, None]],
    "A dictionary containing a 'message' key with a success confirmation or an 'error' key with an error message.",
]:
    global _state_string
    try:
        _state_string += text
        logger.info(f"Appended text to state. New state length: {len(_state_string)}")
        return {"message": "Text appended successfully."}
    except Exception as e:
        logger.error(f"An error occurred while appending to state: {e}")
        return {"error": str(e)}


# @tool(
#     agent_names=["browser_nav_agent", "api_nav_agent", "sql_nav_agent"],
#     description="Tool to retrieve the stored information.",
#     name="get_stored_data",
# )
def get_stored_data() -> Annotated[
    Union[str, Dict[str, str]],
    "The stored value.",
]:
    try:
        logger.info(f"Retrieving current state. State length: {len(_state_string)}")
        return _state_string
    except Exception as e:
        logger.error(f"An error occurred while retrieving state: {e}")
        return {"error": str(e)}
