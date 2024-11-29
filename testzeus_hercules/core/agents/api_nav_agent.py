from testzeus_hercules.core.agents.base_nav_agent import BaseNavAgent
from testzeus_hercules.core.memory.state_handler import *
from testzeus_hercules.core.tools.api_calls import *
from testzeus_hercules.utils.logger import logger


class ApiNavAgent(BaseNavAgent):
    agent_name: str = "api_nav_agent"

    def register_tools(self) -> None:
        """
        Register all the tools that the agent can perform.
        """

        # Register each tool for LLM by assistant agent and for execution by user_proxy_agen

        self.load_additional_tools()
