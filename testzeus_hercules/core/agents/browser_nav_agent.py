import importlib
import os
from datetime import datetime
from string import Template
from typing import Any

import autogen  # type: ignore
from testzeus_hercules.core.memory.prompt_compressor import add_text_compressor
from testzeus_hercules.core.memory.static_ltm import get_user_ltm
from testzeus_hercules.core.prompts import LLM_PROMPTS
from testzeus_hercules.core.tools.api_calls import *
from testzeus_hercules.core.tools.captcha_solver import *
from testzeus_hercules.core.tools.click_using_selector import click as click_element
from testzeus_hercules.core.tools.dropdown_using_selector import *
from testzeus_hercules.core.tools.enter_date_time import *

# from hercules.core.tools.enter_text_and_click import enter_text_and_click
from testzeus_hercules.core.tools.enter_text_using_selector import (
    bulk_enter_text,
    entertext,
)
from testzeus_hercules.core.tools.get_dom_with_content_type import (
    get_dom_with_content_type,
)
from testzeus_hercules.core.tools.get_url import geturl
from testzeus_hercules.core.tools.hover import hover
from testzeus_hercules.core.tools.open_url import openurl
from testzeus_hercules.core.tools.pdf_text_extractor import extract_text_from_pdf
from testzeus_hercules.core.tools.press_key_combination import press_key_combination
from testzeus_hercules.core.tools.set_slider_value import *
from testzeus_hercules.core.tools.sql_calls import *
from testzeus_hercules.core.tools.tool_registry import tool_registry
from testzeus_hercules.core.tools.upload_file import *
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


class BrowserNavAgent:
    def __init__(self, model_config_list, llm_config_params: dict[str, Any], system_prompt: str | None, browser_nav_executor: autogen.UserProxyAgent):  # type: ignore
        """
        Initialize the BrowserNavAgent and store the AssistantAgent instance
        as an instance attribute for external access.

        Parameters:
        - model_config_list: A list of configuration parameters required for AssistantAgent.
        - llm_config_params: A dictionary of configuration parameters for the LLM.
        - system_prompt: The system prompt to be used for this agent or the default will be used if not provided.
        - user_proxy_agent: An instance of the UserProxyAgent class.
        """
        self.browser_nav_executor = browser_nav_executor
        user_ltm = self.__get_ltm()

        system_message = LLM_PROMPTS["BROWSER_AGENT_PROMPT"]
        if system_prompt and len(system_prompt) > 0:
            if isinstance(system_prompt, list):
                system_message = "\n".join(system_prompt)
            else:
                system_message = system_prompt
            logger.info(
                f"Using custom system prompt for BrowserNavAgent: {system_message}"
            )

        system_message = (
            system_message
            + "\n"
            + f"Today's date is {datetime.now().strftime('%d %B %Y')}"
        )
        if user_ltm:  # add the user LTM to the system prompt if it exists
            user_ltm = "\n" + user_ltm
            system_message = Template(system_message).substitute(
                basic_user_information=user_ltm
            )
        logger.info(f"Browser nav agent using model: {model_config_list[0]['model']}")
        self.agent = autogen.ConversableAgent(
            name="browser_navigation_agent",
            system_message=system_message,
            llm_config={
                "config_list": model_config_list,
                **llm_config_params,  # unpack all the name value pairs in llm_config_params as is
            },
        )
        add_text_compressor(self.agent)
        self.__register_tools()

    def __get_ltm(self) -> str | None:
        """
        Get the the long term memory of the user.
        returns: str | None - The user LTM or None if not found.
        """
        return get_user_ltm()

    def __register_tools(self) -> None:
        """
        Register all the tools that the agent can perform.
        """

        # Register each tool for LLM by assistant agent and for execution by user_proxy_agen

        self.agent.register_for_llm(description=LLM_PROMPTS["OPEN_URL_PROMPT"])(openurl)
        self.browser_nav_executor.register_for_execution()(openurl)

        # self.agent.register_for_llm(description=LLM_PROMPTS["ENTER_TEXT_AND_CLICK_PROMPT"])(enter_text_and_click)
        # self.browser_nav_executor.register_for_execution()(enter_text_and_click)

        self.agent.register_for_llm(
            description=LLM_PROMPTS["GET_DOM_WITH_CONTENT_TYPE_PROMPT"]
        )(get_dom_with_content_type)
        self.browser_nav_executor.register_for_execution()(get_dom_with_content_type)

        self.agent.register_for_llm(description=LLM_PROMPTS["CLICK_PROMPT"])(
            click_element
        )
        self.browser_nav_executor.register_for_execution()(click_element)

        self.agent.register_for_llm(description=LLM_PROMPTS["GET_URL_PROMPT"])(geturl)
        self.browser_nav_executor.register_for_execution()(geturl)

        self.agent.register_for_llm(description=LLM_PROMPTS["BULK_ENTER_TEXT_PROMPT"])(
            bulk_enter_text
        )
        self.browser_nav_executor.register_for_execution()(bulk_enter_text)

        self.agent.register_for_llm(description=LLM_PROMPTS["ENTER_TEXT_PROMPT"])(
            entertext
        )
        self.browser_nav_executor.register_for_execution()(entertext)

        self.agent.register_for_llm(
            description=LLM_PROMPTS["PRESS_KEY_COMBINATION_PROMPT"]
        )(press_key_combination)
        self.browser_nav_executor.register_for_execution()(press_key_combination)

        self.agent.register_for_llm(
            description=LLM_PROMPTS["EXTRACT_TEXT_FROM_PDF_PROMPT"]
        )(extract_text_from_pdf)
        self.browser_nav_executor.register_for_execution()(extract_text_from_pdf)

        self.agent.register_for_llm(description=LLM_PROMPTS["HOVER_PROMPT"])(hover)
        self.browser_nav_executor.register_for_execution()(hover)

        """
        # Register reply function for printing messages
        self.browser_nav_executor.register_reply( # type: ignore
            [autogen.Agent, None],
            reply_func=print_message_from_user_proxy,
            config={"callback": None},
        )
        self.agent.register_reply( # type: ignore
            [autogen.Agent, None],
            reply_func=print_message_from_browser_agent,
            config={"callback": None},
        )
        """
        self.__load_additional_tools()

        # print(f">>> Function map: {self.browser_nav_executor.function_map}") # type: ignore

    def __load_additional_tools(self) -> None:
        """
        Dynamically load additional tools from directories or specific Python files
        specified by an environment variable.
        """
        # Get additional tool directories or files from environment variable
        additional_tool_dirs: str = os.getenv("ADDITIONAL_TOOL_DIRS", "")
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
                    module_name = os.path.basename(tool_path)[
                        :-3
                    ]  # Strip .py extension
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
        for tool in tool_registry:
            self.agent.register_for_llm(description=tool["description"])(tool["func"])
            self.browser_nav_executor.register_for_execution()(tool["func"])
            logger.info("Registered additional tool: %s", tool["name"])
