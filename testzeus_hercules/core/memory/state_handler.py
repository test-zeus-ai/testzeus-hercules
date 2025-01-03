from collections import defaultdict, deque
from typing import Annotated, Any, Dict, Union

from testzeus_hercules.config import DEFAULT_TEST_ID
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger

# Module-level state string
_state_string: Dict[str, str] = defaultdict(str)

_state_dict: Dict[str, Any] = defaultdict(deque)


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
        _state_string[DEFAULT_TEST_ID] += text
        logger.info(f"Appended text to state. New state length: {len(_state_string[DEFAULT_TEST_ID])}")
        return {"message": "Text appended successfully."}
    except Exception as e:
        logger.error(f"An error occurred while appending to state: {e}")
        return {"error": str(e)}


def store_run_data(
    text: Annotated[
        str,
        "The confirmation of stored value.",
    ]
) -> Annotated[
    Dict[str, Union[str, None]],
    "A dictionary containing a 'message' key with a success confirmation or an 'error' key with an error message.",
]:
    global _state_dict
    try:
        _state_dict[DEFAULT_TEST_ID].append(text)
        if len(_state_dict[DEFAULT_TEST_ID]) > 2:
            while len(_state_dict[DEFAULT_TEST_ID]) > 2:
                _state_dict[DEFAULT_TEST_ID].popleft()
        processed_text = ", ".join(_state_dict[DEFAULT_TEST_ID])
        logger.info(f"Added to context. New state length: {len(processed_text)}")
        return {"message": "Context Added successfully."}
    except Exception as e:
        logger.error(f"An error occurred while adding adding context: {e}")
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
        logger.info(f"Retrieving current state. State length: {len(_state_string[DEFAULT_TEST_ID])}")
        return _state_string[DEFAULT_TEST_ID]
    except Exception as e:
        logger.error(f"An error occurred while retrieving state: {e}")
        return {"error": str(e)}


def get_run_data() -> Annotated[
    Union[str, Dict[str, str]],
    "The stored value.",
]:
    global _state_dict
    try:
        processed_text = ", ".join(_state_dict[DEFAULT_TEST_ID])
        logger.info(f"Retrieving current context. State length: {len(processed_text)}")
        return processed_text
    except Exception as e:
        logger.error(f"An error occurred while retrieving context: {e}")
        return {"error": str(e)}
