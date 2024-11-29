import importlib
import os
from datetime import datetime
from string import Template
from typing import Any

import autogen  # type: ignore
from testzeus_hercules.core.memory.prompt_compressor import add_text_compressor
from testzeus_hercules.core.memory.static_ltm import get_user_ltm
from testzeus_hercules.core.prompts import LLM_PROMPTS
from testzeus_hercules.core.tools.tool_registry import tool_registry
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


class BaseNavAgent:
    agent_name: str = "base_nav_agent"

    def __init__(self, model_config_list, llm_config_params: dict[str, Any], system_prompt: str | None, nav_executor: autogen.UserProxyAgent, agent_name: str = "browser_navigation_agent", agent_prompt: str | None = LLM_PROMPTS["BROWSER_AGENT_PROMPT"]):  # type: ignore
        """
        Initialize the BaseNavAgent and store the AssistantAgent instance
        as an instance attribute for external access.

        Parameters:
        - model_config_list: A list of configuration parameters required for AssistantAgent.
        - llm_config_params: A dictionary of configuration parameters for the LLM.
        - system_prompt: The system prompt to be used for this agent or the default will be used if not provided.
        - user_proxy_agent: An instance of the UserProxyAgent class.
        """
        self.nav_executor = nav_executor
        user_ltm = self.__get_ltm()

        system_message = agent_prompt
        if system_prompt and len(system_prompt) > 0:
            if isinstance(system_prompt, list):
                system_message = "\n".join(system_prompt)
            else:
                system_message = system_prompt
            logger.info(f"Using custom system prompt for BaseNavAgent: {system_message}")

        system_message = system_message + "\n" + f"Today's date is {datetime.now().strftime('%d %B %Y')}"
        if user_ltm:  # add the user LTM to the system prompt if it exists
            user_ltm = "\n" + user_ltm
            system_message = Template(system_message).substitute(basic_test_information=user_ltm)
        logger.info(f"Nav agent {agent_name} using model: {model_config_list[0]['model']}")
        self.agent = autogen.ConversableAgent(
            name=agent_name,
            system_message=system_message,
            llm_config={
                "config_list": model_config_list,
                **llm_config_params,  # unpack all the name value pairs in llm_config_params as is
            },
        )
        # add_text_compressor(self.agent)
        self.register_tools()

    def __get_ltm(self) -> str | None:
        """
        Get the the long term memory of the user.
        returns: str | None - The user LTM or None if not found.
        """
        return get_user_ltm()

    def register_tools(self) -> None:
        """
        Register all the tools that the agent can perform.
        """

        # Register each tool for LLM by assistant agent and for execution by user_proxy_agen
        return None

    def load_additional_tools(self, additional_tool_dirs: str = os.getenv("ADDITIONAL_TOOL_DIRS", "")) -> None:
        """
        Dynamically load additional tools from directories or specific Python files
        specified by an environment variable.
        """
        # Get additional tool directories or files from environment variable
        if len(additional_tool_dirs) != 0:
            additional_tool_paths: list[str] = additional_tool_dirs.split(",")

            for tool_path in additional_tool_paths:
                tool_path = tool_path.strip()  # Strip whitespace

                if os.path.isdir(tool_path):
                    # If the path is a directory, process all .py files in it
                    for filename in os.listdir(tool_path):
                        if filename.endswith(".py"):
                            module_name = filename[:-3]  # Remove .py extension
                            module_path = f"{tool_path.replace('/', '.')}.{module_name}"
                            importlib.import_module(module_path)
                            add_event(
                                EventType.TOOL,
                                EventData(detail=f"Registering tool: {filename}"),
                            )

                elif tool_path.endswith(".py") and os.path.isfile(tool_path):
                    # If the path is a specific .py file, load it directly
                    module_name = os.path.basename(tool_path)[:-3]  # Strip .py extension
                    directory_path = os.path.dirname(tool_path).replace("/", ".")
                    module_path = f"{directory_path}.{module_name}"
                    importlib.import_module(module_path)
                    add_event(
                        EventType.TOOL,
                        EventData(detail=f"Registering tool: {module_name}"),
                    )
                else:
                    logger.warning("Invalid tool path specified: %s", tool_path)

        # Register the tools that were dynamically discovered
        for tool_registry_for_agent in tool_registry:
            if tool_registry_for_agent != self.agent_name:
                continue
            for tool in tool_registry[tool_registry_for_agent]:
                self.agent.register_for_llm(description=tool["description"])(tool["func"])
                self.nav_executor.register_for_execution()(tool["func"])
                logger.info("Registered additional tool: %s", tool["name"])
