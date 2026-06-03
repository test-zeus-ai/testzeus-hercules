from datetime import datetime
from string import Template
from typing import Any

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.agents.base_nav_agent import BaseNavAgent
from testzeus_hercules.utils.logger import logger


class MultimodalBaseNavAgent(BaseNavAgent):
    """Navigation agent using a vision-capable chat model."""

    agent_name: str = "multimodal_base_nav_agent"
    prompt = "Base Multimodal Agent"

    def __init__(
        self,
        model_config: dict[str, Any],
        llm_config_params: dict[str, Any],
        system_prompt: str | None,
        agent_name: str = None,
        agent_prompt: str | None = None,
    ) -> None:
        self.agent_name = agent_name or self.agent_name
        user_ltm = self.get_ltm()

        system_message = agent_prompt or self.prompt
        if system_prompt and len(system_prompt) > 0:
            system_message = "\n".join(system_prompt) if isinstance(system_prompt, list) else system_prompt
            logger.info("Using custom system prompt for MultimodalBaseNavAgent: %s", system_message)
        
        system_message = system_message + "\n" + f"Current timestamp is {datetime.now().strftime("%Y/%m/%d %H:%M:%S")}"
        config = get_global_conf()

        if not config.should_use_dynamic_ltm() and user_ltm:
            user_ltm = "\n" + user_ltm
            system_message = Template(system_message).substitute(basic_test_information=user_ltm)

        from testzeus_hercules.utils.llm_helper import create_chat_model

        logger.info("Nav agent %s using model: %s", self.agent_name, model_config.get("model"))
        self.system_message = system_message
        self.llm = create_chat_model(model_config, llm_config_params)
        self.tools = []
        self.register_tools()
