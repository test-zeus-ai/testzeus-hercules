# tool_registry.py
from collections.abc import Callable
from typing import Any

# Define the type of the functions that will be registered as tools
toolType = Callable[..., Any]

# Global registry to store private tool functions and their metadata
tool_registry: list[dict[str, Any]] = []


def tool(description: str, name: str | None = None) -> Callable[[toolType], toolType]:
    """
    Decorator for registering private tools.

    Parameters:
    - description: A string describing the tool's function.
    - name: Optional name to register the tool with. If not provided, the function's name will be used.

    Returns:
    - A decorator function that registers the tool in the global registry.
    """

    def decorator(func: toolType) -> toolType:
        tool_registry.append(
            {
                "name": (name if name else func.__name__),  # Use provided name or fallback to function name
                "func": func,
                "description": description,
            }
        )
        return func

    return decorator
