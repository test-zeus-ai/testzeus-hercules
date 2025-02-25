from datetime import datetime
from string import Template
from typing import Any

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.agents.base_nav_agent import BaseNavAgent
from testzeus_hercules.utils.llm_helper import MultimodalConversableAgent
from testzeus_hercules.utils.logger import logger


class MultimodalBaseNavAgent(BaseNavAgent):
    agent_name: str = "multimodal_base_nav_agent"
    prompt = "Base Multimodal Agent"

    def __init__(
        self,
        model_config_list: list[dict[str, Any]],
        llm_config_params: dict[str, Any],
        system_prompt: str | None,
        nav_executor: Any,
        agent_name: str = None,
        agent_prompt: str | None = None,
    ) -> None:
        """
        Initialize the MultimodalBaseNavAgent using MultimodalConversableAgent instead of ConversableAgent.
        """
        self.nav_executor = nav_executor
        user_ltm = self.get_ltm()
        agent_name = self.agent_name if agent_name is None else agent_name

        system_message = agent_prompt or self.prompt
        if system_prompt and len(system_prompt) > 0:
            if isinstance(system_prompt, list):
                system_message = "\n".join(system_prompt)
            else:
                system_message = system_prompt
            logger.info(
                f"Using custom system prompt for MultimodalBaseNavAgent: {system_message}"
            )

        system_message = (
            system_message
            + "\n"
            + f"Current timestamp is {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        config = get_global_conf()

        if (
            not config.should_use_dynamic_ltm() and user_ltm
        ):  # Use static LTM when dynamic is disabled
            user_ltm = "\n" + user_ltm
            system_message = Template(system_message).substitute(
                basic_test_information=user_ltm
            )
        logger.info(
            f"Nav agent {agent_name} using model: {model_config_list[0]['model']}"
        )

        # Use MultimodalConversableAgent instead of ConversableAgent
        self.agent = MultimodalConversableAgent(
            name=agent_name,
            system_message=system_message,
            llm_config={
                "config_list": model_config_list,
                **llm_config_params,  # unpack all the name value pairs in llm_config_params as is
            },
            human_input_mode="NEVER",
        )

        self.register_tools()
