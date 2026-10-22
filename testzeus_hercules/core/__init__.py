"""Core package exports.

The package initializer stays lightweight so importing a focused submodule such
as ``testzeus_hercules.core.agents_llm_config_manager`` does not eagerly import
agents, tools, and memory modules that can depend back on the caller.
"""

from importlib import import_module
from typing import Any

__all__ = [
    "LLM_PROMPTS",
    "PlaywrightManager",
    "SimpleHercules",
    "agents",
    "final_reply_callback_user_proxy",
    "memory",
    "tools",
]

_SUBMODULE_EXPORTS = {
    "agents": "testzeus_hercules.core.agents",
    "memory": "testzeus_hercules.core.memory",
    "tools": "testzeus_hercules.core.tools",
}

_ATTRIBUTE_EXPORTS = {
    "LLM_PROMPTS": ("testzeus_hercules.core.prompts", "LLM_PROMPTS"),
    "PlaywrightManager": (
        "testzeus_hercules.core.playwright_manager",
        "PlaywrightManager",
    ),
    "SimpleHercules": ("testzeus_hercules.core.simple_hercules", "SimpleHercules"),
    "final_reply_callback_user_proxy": (
        "testzeus_hercules.core.post_process_responses",
        "final_reply_callback_user_proxy",
    ),
}


def __getattr__(name: str) -> Any:
    if name in _SUBMODULE_EXPORTS:
        module = import_module(_SUBMODULE_EXPORTS[name])
        globals()[name] = module
        return module

    if name in _ATTRIBUTE_EXPORTS:
        module_name, attribute_name = _ATTRIBUTE_EXPORTS[name]
        value = getattr(import_module(module_name), attribute_name)
        globals()[name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
