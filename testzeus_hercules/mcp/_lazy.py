"""Import the MCP SDK's stdio server pieces without its HTTP stack.

``import mcp`` (and ``mcp.server``) run package ``__init__`` files that import
FastMCP, which pulls in Starlette, uvicorn and pydantic-settings: about 0.4 s the
shim does not need. :func:`install` registers the two packages as lazy modules
whose ``__init__`` runs only when an attribute that it defines is first read
(``from mcp import ClientSession`` still works), while submodules such as
``mcp.types`` and ``mcp.server.lowlevel`` import directly.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
import threading
import types
from typing import Any

_LOCK = threading.RLock()


class _LazyPackage(types.ModuleType):
    """A package module that executes its real ``__init__`` on the first missing attribute."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        if _is_submodule(self, name):  # `from mcp import types` imports just that submodule
            return importlib.import_module(f"{self.__name__}.{name}")
        with _LOCK:
            namespace = self.__dict__
            if not namespace.get("_hercules_initialised"):
                namespace["_hercules_initialised"] = True
                origin = self.__spec__.origin if self.__spec__ is not None else None
                if origin and os.path.isfile(origin):
                    with open(origin, encoding="utf-8") as handle:
                        exec(compile(handle.read(), origin, "exec"), namespace)  # noqa: S102 - the package's own __init__
        try:
            return self.__dict__[name]
        except KeyError:
            raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}") from None


def _is_submodule(package: types.ModuleType, name: str) -> bool:
    if not name.isidentifier():
        return False
    for directory in getattr(package, "__path__", ()):
        try:
            entries = os.listdir(directory)  # exact names: file systems may ignore case
        except OSError:
            continue
        if name + ".py" in entries or (name in entries and os.path.isfile(os.path.join(directory, name, "__init__.py"))):
            return True
    return False


def _lazy_package(name: str, spec: importlib.machinery.ModuleSpec) -> _LazyPackage:
    module = _LazyPackage(name)
    module.__spec__ = spec
    module.__loader__ = spec.loader
    module.__file__ = spec.origin
    module.__package__ = name
    module.__path__ = list(spec.submodule_search_locations or [])
    return module


def install() -> None:
    """Register ``mcp`` and ``mcp.server`` as lazy packages unless they are already imported."""
    with _LOCK:
        if "mcp" in sys.modules:
            return
        spec = importlib.util.find_spec("mcp")
        if spec is None or not spec.submodule_search_locations:
            return  # not installed: the real import will raise a clear error
        root = _lazy_package("mcp", spec)
        server_dir = os.path.join(list(spec.submodule_search_locations)[0], "server")
        server_spec = importlib.machinery.ModuleSpec("mcp.server", loader=None, origin=os.path.join(server_dir, "__init__.py"), is_package=True)
        server_spec.submodule_search_locations = [server_dir]
        server = _lazy_package("mcp.server", server_spec)
        sys.modules["mcp"] = root
        sys.modules["mcp.server"] = server
        root.__dict__["server"] = server
