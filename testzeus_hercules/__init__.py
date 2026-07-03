"""Top-level package for testzeus-hercules.

Keep package import lightweight so importing a focused module such as
``testzeus_hercules.config`` does not eagerly import browser/tool dependencies.
"""

from importlib import import_module
from typing import Any

__all__ = ["core"]


def __getattr__(name: str) -> Any:
    if name == "core":
        module = import_module("testzeus_hercules.core")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
