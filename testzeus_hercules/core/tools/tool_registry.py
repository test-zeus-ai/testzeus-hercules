# tool_registry.py
import os
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from testzeus_hercules.config import get_proof_path
from testzeus_hercules.utils import logger

# Define the type of the functions that will be registered as tools
toolType = Callable[..., Any]

# Global registry to store private tool functions and their metadata
#
tool_registry: dict[list[dict[str, Any]]] = defaultdict(list)


def api_logger(logging_string: str) -> None:
    """
    Function to log to a file.

    Parameters:
    - logging_string (str): The string to log.
    """
    proof_path = os.path.join(get_proof_path(), "api_logs.log")
    with open(proof_path, "a", encoding="utf-8") as file:
        file.write(logging_string + "\n")


def sec_logger(logging_string: str) -> None:
    """
    Function to log to a file.

    Parameters:
    - logging_string (str): The string to log.
    """
    proof_path = os.path.join(get_proof_path(), "sec_logs.log")
    with open(proof_path, "a", encoding="utf-8") as file:
        file.write(logging_string + "\n")


def tool(agent_names: list[str], description: str, name: str | None = None) -> Callable[[toolType], toolType]:
    """
    Decorator for registering private tools.

    Parameters:
    - description: A string describing the tool's function.
    - name: Optional name to register the tool with. If not provided, the function's name will be used.

    Returns:
    - A decorator function that registers the tool in the global registry.
    """

    def decorator(func: toolType) -> toolType:
        for agent_name in agent_names:
            tool_registry[agent_name].append(
                {
                    "name": (name if name else func.__name__),  # Use provided name or fallback to function name
                    "func": func,
                    "description": description,
                }
            )
        return func

    return decorator
