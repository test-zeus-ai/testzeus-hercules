# tool_registry.py
from collections.abc import Callable
from collections import defaultdict
from typing import Any

# Define the type of the functions that will be registered as tools
toolType = Callable[..., Any]

# Global registry to store private tool functions and their metadata
#
tool_registry: dict[list[dict[str, Any]]] = defaultdict(list)


def tool(
    agent_names: list[str], description: str, name: str | None = None
) -> Callable[[toolType], toolType]:
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
                    "name": (
                        name if name else func.__name__
                    ),  # Use provided name or fallback to function name
                    "func": func,
                    "description": description,
                }
            )
        return func

    return decorator
