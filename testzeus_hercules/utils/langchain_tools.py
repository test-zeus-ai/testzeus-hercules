"""Convert Hercules too_registry entires to LangChain StructuredTools."""

from __future__ import annotations

import asyncio
import inspect
from typing import Annotated, Any, Callable, get_args, get_origin

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model
from testzeus_hercules.core.tools.tool_registry import tool_registry
from testzeus_hercules.utils.logger import logger


def _annotation_to_type_and_description(annotation: Any, description: str = "") -> tuple[Any, str]:
    """Map Annotated[T, "description"] to the base type and field description."""
    if annotation is inspect.Parameter.empty:
        return str, description

    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        if not args:
            return Any, description

        base_type = args[0]
        for metadata in args[1:]:
            if isinstance(metadata, str):
                description = metadata or description
                break
        return base_type, description

    return annotation, description


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
        field_type, description = _annotation_to_type_and_description(ann, param_name)
        if getattr(param, "default", inspect.Parameter.empty) is not inspect.Parameter.empty:
            fields[param_name] = (
                field_type,
                Field(default=param.default, description=description),
            )
        else:
            fields[param_name] = (
                field_type,
                Field(default=..., description=description),
            )

    if not fields:
        return create_model(f"{tool_name}_args")  # type: ignore[call-overload]

    return create_model(f"{tool_name}_args", **fields)  # type: ignore[call-overload]


def _normalize_legacy_kwargs(func: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Tolerate tool-call args produced from schemas cached before a restart."""
    if not kwargs:
        return kwargs

    def first_present(mapping: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in mapping and mapping[key] is not None:
                return mapping[key]
        return None

    sig = inspect.signature(func)
    params = sig.parameters
    accepts_var_kwargs = any(param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values())
    expected = {name for name, param in params.items() if name not in ("self", "cls") and param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)}
    normalized = dict(kwargs)

    if set(normalized) == {"kwargs"} and isinstance(normalized["kwargs"], dict):
        normalized = dict(normalized["kwargs"])

    entry = normalized.get("entry")
    if isinstance(entry, dict):
        entry_aliases = {
            "selector": ("selector", "md"),
            "text_to_enter": ("text_to_enter", "value_to_fill", "text"),
            "value_to_fill": ("value_to_fill", "option_value", "input_value"),
            "value_to_set": ("value_to_set", "value_to_fill"),
            "file_path": ("file_path", "path"),
        }
        for target_key, source_keys in entry_aliases.items():
            value = first_present(entry, *source_keys)
            if value is not None:
                normalized.setdefault(target_key, value)
    elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
        normalized.setdefault("selector", entry[0])
        normalized.setdefault("text_to_enter", entry[1])
        normalized.setdefault("value_to_fill", entry[1])
        normalized.setdefault("value_to_set", entry[1])
        normalized.setdefault("file_path", entry[1])

    if "selector" in expected and "selector" not in normalized and "md" in normalized:
        normalized["selector"] = normalized["md"]

    if "entries" in expected and "entries" not in normalized:
        for legacy_key in ("selector_text_list", "selector_value_list", "selector_file_list", "entries_list"):
            if legacy_key in normalized:
                normalized["entries"] = normalized[legacy_key]
                break

    if "text_to_enter" in expected and "text_to_enter" not in normalized:
        value = first_present(normalized, "value_to_fill", "text", "input_value")
        if value is not None:
            normalized["text_to_enter"] = value

    if "value_to_fill" in expected and "value_to_fill" not in normalized:
        value = first_present(normalized, "option_value", "input_value", "text_to_enter")
        if value is not None:
            normalized["value_to_fill"] = value

    if "value_to_set" in expected and "value_to_set" not in normalized:
        value = first_present(normalized, "value_to_fill")
        if value is not None:
            normalized["value_to_set"] = value

    if "file_path" in expected and "file_path" not in normalized:
        value = first_present(normalized, "path")
        if value is not None:
            normalized["file_path"] = value

    if accepts_var_kwargs:
        return normalized

    dropped = set(normalized) - expected
    if dropped:
        logger.debug("Dropping stale tool args for %s: %s", func.__name__, sorted(dropped))
    return {key: value for key, value in normalized.items() if key in expected}


def _wrap_tool_func(func: Callable[..., Any]) -> Callable[..., Any]:
    if inspect.iscoroutinefunction(func):

        async def _async_wrapper(**kwargs: Any) -> Any:
            return await func(**_normalize_legacy_kwargs(func, kwargs))

        return _async_wrapper

    def _sync_wrapper(**kwargs: Any) -> Any:
        return func(**_normalize_legacy_kwargs(func, kwargs))

    return _sync_wrapper


def registry_tools_to_structured_tools(agent_name: str) -> list[StructuredTool]:
    """Build LangChain tools for a navigation agent from the global registry."""
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
