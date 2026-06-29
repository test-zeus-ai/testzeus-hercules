import importlib
import os
import sys
from datetime import datetime
from string import Template
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import StructuredTool

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.memory.static_ltm import get_user_ltm
from testzeus_hercules.core.tools.tool_registry import tool_registry
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.langchain_tools import registry_tools_to_structured_tools
from testzeus_hercules.utils.llm_helper import create_chat_model
from testzeus_hercules.utils.logger import logger


class BaseNavAgent:
    agent_name: str = "base_nav_agent"
    prompt = "Base Agent"

    def __init__(self, model_config: dict[str, Any], llm_config_params: dict[str, Any], system_prompt: str | None, agent_name: str | None = None, agent_prompt: str | None = None) -> None:
        self.agent_name = agent_name or self.agent_name
        user_ltm = self.get_ltm()

        system_message = agent_prompt or self.prompt
        if system_prompt and len(system_prompt) > 0:
            system_message = "\n".join(system_prompt) if isinstance(system_prompt, list) else system_prompt
            logger.info(f"Using custom system prompt for BaseNavAgent: {system_message}")

        system_message = system_message + "\n" + f"Current timestamp is {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        config = get_global_conf()

        logger.warning("[SYSTEM_PROMPT_DEBUG] agent=%s user_ltm=%r system_message_tail=%r", self.agent_name, user_ltm, system_message[-200:])
        if not config.should_use_dynamic_ltm() and user_ltm:
            user_ltm = "\n" + user_ltm
            system_message = Template(system_message).substitute(basic_test_information=user_ltm)

        logger.info("Nav agent %s using model %s", self.agent_name, model_config.get("model"))

        self.system_message = system_message
        self.llm: BaseChatModel = create_chat_model(model_config, llm_config_params)
        self.tools: list[StructuredTool] = []
        self.register_tools()

    def get_ltm(self) -> str | None:
        """Get the the long term memory of the user."""
        return get_user_ltm()

    def register_tools(self) -> None:
        """Register all the tools that the agent can perform."""
        logger.info(f"[REGISTER_TOOLS_DEBUG] Registering tools for agent: {self.agent_name}")
        self.tools = registry_tools_to_structured_tools(self.agent_name)
        logger.info(f"[REGISTER_TOOLS_DEBUG] Agent {self.agent_name} now has {len(self.tools)} tools: {[t.name for t in self.tools]}")

    async def shutdown(self) -> None:
        """Shutdown the agent."""
        pass

    def load_tools(self, additional_tool_dirs: str = os.getenv("ADDITIONAL_TOOL_DIRS", "")) -> None:
        """Dynamically load additional tools from directories or Python files."""
        additional_tool_paths: list[str] = additional_tool_dirs.split(",")

        for tool_path in additional_tool_paths:
            tool_path = tool_path.strip()
            if os.path.isdir(tool_path):
                parent_dir = os.path.dirname(tool_path)
                sys.path.insert(0, parent_dir)
                try:
                    dir_name = os.path.basename(tool_path)
                    for filename in os.listdir(tool_path):
                        if filename.endswith(".py"):
                            module_name = filename[:-3]
                            full_module_path = f"{dir_name}.{module_name}"
                            importlib.import_module(full_module_path)
                            add_event(
                                EventType.TOOL,
                                EventData(detail=f"Registering tool: {filename}"),
                            )
                finally:
                    sys.path.remove(parent_dir)
            elif tool_path.endswith(".py") and os.path.isfile(tool_path):
                parent_dir = os.path.dirname(tool_path)
                sys.path.insert(0, parent_dir)
                try:
                    module_name = os.path.basename(tool_path)[:-3]  # Strip .py extension
                    importlib.import_module(module_name)
                    add_event(
                        EventType.TOOL,
                        EventData(detail=f"Registering tool: {module_name}"),
                    )
                finally:
                    sys.path.remove(parent_dir)
            else:
                logger.warning("Invalid tool path specified: %s", tool_path)

        self.tools = registry_tools_to_structured_tools(self.agent_name)
        for tool_entry in tool_registry.get(self.agent_name, []):
            logger.info("Registered tool: %s", tool_entry["name"])
