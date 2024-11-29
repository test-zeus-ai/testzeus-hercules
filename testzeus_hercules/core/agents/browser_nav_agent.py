import importlib
import os
from datetime import datetime
from string import Template
from typing import Any

import autogen  # type: ignore
from testzeus_hercules.core.agents.base_nav_agent import BaseNavAgent
from testzeus_hercules.core.memory.prompt_compressor import add_text_compressor
from testzeus_hercules.core.memory.state_handler import *
from testzeus_hercules.core.memory.static_ltm import get_user_ltm
from testzeus_hercules.core.prompts import LLM_PROMPTS
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
from testzeus_hercules.core.tools.tool_registry import tool_registry
from testzeus_hercules.core.tools.upload_file import *
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


class BrowserNavAgent(BaseNavAgent):
    agent_name: str = "browser_nav_agent"

    def register_tools(self) -> None:
        """
        Register all the tools that the agent can perform.
        """

        # Register each tool for LLM by assistant agent and for execution by user_proxy_agen

        self.agent.register_for_llm(description=LLM_PROMPTS["OPEN_URL_PROMPT"])(openurl)
        self.nav_executor.register_for_execution()(openurl)

        # self.agent.register_for_llm(description=LLM_PROMPTS["ENTER_TEXT_AND_CLICK_PROMPT"])(enter_text_and_click)
        # self.nav_executor.register_for_execution()(enter_text_and_click)

        self.agent.register_for_llm(description=LLM_PROMPTS["GET_DOM_WITH_CONTENT_TYPE_PROMPT"])(get_dom_with_content_type)
        self.nav_executor.register_for_execution()(get_dom_with_content_type)

        self.agent.register_for_llm(description=LLM_PROMPTS["CLICK_PROMPT"])(click_element)
        self.nav_executor.register_for_execution()(click_element)

        self.agent.register_for_llm(description=LLM_PROMPTS["GET_URL_PROMPT"])(geturl)
        self.nav_executor.register_for_execution()(geturl)

        self.agent.register_for_llm(description=LLM_PROMPTS["BULK_ENTER_TEXT_PROMPT"])(bulk_enter_text)
        self.nav_executor.register_for_execution()(bulk_enter_text)

        self.agent.register_for_llm(description=LLM_PROMPTS["ENTER_TEXT_PROMPT"])(entertext)
        self.nav_executor.register_for_execution()(entertext)

        self.agent.register_for_llm(description=LLM_PROMPTS["PRESS_KEY_COMBINATION_PROMPT"])(press_key_combination)
        self.nav_executor.register_for_execution()(press_key_combination)

        self.agent.register_for_llm(description=LLM_PROMPTS["EXTRACT_TEXT_FROM_PDF_PROMPT"])(extract_text_from_pdf)
        self.nav_executor.register_for_execution()(extract_text_from_pdf)

        self.agent.register_for_llm(description=LLM_PROMPTS["HOVER_PROMPT"])(hover)
        self.nav_executor.register_for_execution()(hover)

        """
        # Register reply function for printing messages
        self.nav_executor.register_reply( # type: ignore
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
        self.load_additional_tools()

        # print(f">>> Function map: {self.nav_executor.function_map}") # type: ignore
