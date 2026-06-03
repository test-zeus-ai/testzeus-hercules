"""Convert Hercules too_registry entires to LangChain StructuredTools."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from testzeus_hercules.core.tools.tool_registry import tool_registry
from testzeus_hercules.utils.logger import logger

def _annotations_to_field(annotation:Any, description:str = "") -> tuple[Any,Any]:
    """Map Annotated types to pydantic Field defaults."""
    default = ...
    if hasattr(annotation, "__metadata__") and annotation.__metadata__:
        meta = annotation.__metadata__[0]
        if isinstance(meta, str):
            description = meta or description
            origin = getattr(annotation, "__origin__", annotation)
            return (origin, Field(default=default, description=description))
        return (annotation, Field(default=default, description=description))

def _build_args_schema(func: Callable[..., Any], tool_name: str) -> type[BaseModel] | None:
    sig = inspect.signature(func)
    fields: dict[str, tuple[Any, Any]] = {}
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        if param.annotation is inspect.Parameter.empty:
            fields[param_name] = (str, Field(description=param_name))
            continue
        ann = param.annotation
        if getattr  (param, "default", inspect.Parameter.empty) is not inspect.Parameter.empty:
            default_val = param.default
            origin = getattr(ann, "__origin__", ann)
            desc = ""
            if hasattr(ann, "__metadata__") and ann.__metadata__ and isinstance(ann.__metadata__[0], str):
                desc = ann.__metadata__[0]
            fields[param_name] = (origin, Field(default=default_val, description=desc))
        else: 
            origin, field = _annotations_to_field(ann, param_name)
            fields[param_name] = (origin, field)
    if not fields:
        return None
    return create_model(f"{tool_name}_args", **fields) #type: ignore[call-overload]

def _wrap_tool_func(func: Callable[..., Any]) -> Callable[..., Any]:
    if inspect.iscoroutinefunction(func):

        async def _async_wrapper(**kwargs: Any) -> Any:
            return await func(**kwargs)
        
        return _async_wrapper
        
    def _sync_wrapper(**kwargs: Any) -> Any:
        return func(**kwargs)
    
    return _sync_wrapper

def registry_tools_to_structured_tools(agent_name: str) -> list[StructuredTool]:
    """Build LangChain tools for a navigation agent from the global registry. """
    tools: list[StructuredTool] = []
    tool_entries = tool_registry.get(agent_name, [])
    logger.info(f"[TOOL_DEBUG] Building tools for agent '{agent_name}'. Found {len(tool_entries)} tool entries in registry.")
    
    for entry in tool_entries:
        func = entry["func"]
        name = entry["name"]
        description = entry["description"]
        logger.info(f"[TOOL_DEBUG] Processing tool '{name}' for agent '{agent_name}'")
        
        try:
            args_schema = _build_args_schema(func, name)
            wrapped = _wrap_tool_func(func)
            tool = StructuredTool.from_function(
                coroutine=wrapped if inspect.iscoroutinefunction(func) else None,
                func=None if inspect.iscoroutinefunction(func) else wrapped,
                name=name,
                description=description,
                args_schema=args_schema,
            )
            tools.append(tool)
            logger.info(f"[TOOL_DEBUG] Successfully registered tool '{name}' for agent '{agent_name}'")
        except Exception as exc:
            logger.warning(f"[TOOL_DEBUG] Failed to register tool '{name}' for agent '{agent_name}': {exc}", exc_info=True)
            import traceback
            traceback.print_exc()
    
    logger.info(f"[TOOL_DEBUG] Completed building {len(tools)} tools for agent '{agent_name}': {[t.name for t in tools]}")
    return tools

def merge_tools(*tool_lists: list[StructuredTool]) -> list[StructuredTool]:
    """Merge tool lists, last duplicate name wins."""
    merged: dict[str, StructuredTool] = {}
    for tool_list in tool_lists:
        for tool in tool_list:
            merged[tool.name] = tool
    return list(merged.values())
